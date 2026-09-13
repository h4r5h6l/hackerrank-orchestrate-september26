"""Candidate plan generation, eligibility, tie-break ranking.

Output row assembly follows the contract in problem_statement.md:
  - amount_safe_to_pay and earliest_date_for_full_payment are measured on
    the BASELINE timeline (no spending changes)
  - spending changes may enable full/partial payment (affordable_with_plan)
  - partial_payment = exactly 2 payments summing to requested_amount
  - installments must match a supplied payment option exactly
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from context import RecurringSeries, UserContext
from forecast import (amount_safe_to_pay as _safe_amount, build_occurrences,
                      earliest_date_for_full_payment as _earliest_full, is_safe)
from models import Request

IT = "installments"
FP = "full_payment"
PP = "partial_payment"
WAIT = "wait"


@dataclass
class CandidatePlan:
    method: str
    payments: list
    total_cost: float = 0.0
    payment_option_id: str | None = None
    spending_changes: list = field(default_factory=list)

    def completes_by(self, deadline) -> bool:
        if not self.payments:
            return False
        if deadline is None:
            return True
        return self.payments[-1][0] <= deadline

    def start_date(self) -> date:
        return self.payments[0][0] if self.payments else date.max

    def key(self, req):
        completes = 0 if self.completes_by(req.desired_completion_date) else 1
        needs_changes = 1 if self.spending_changes else 0
        return (completes, needs_changes, round(self.total_cost, 6),
                self.start_date(), len(self.payments), self.payment_option_id or "")


def _expand_installment(opt):
    """[(date, amount), ...] exactly per the payment option."""
    if opt.payment_amount is None or opt.first_payment_date is None:
        return []
    freq = opt.payment_frequency_days or 0
    schedule = []
    d = opt.first_payment_date
    for _ in range(max(1, opt.number_of_payments)):
        schedule.append((d, opt.payment_amount))
        d = d + timedelta(days=freq)
    return schedule


def instant_candidates(req, ctx, safe_amount, full_date, options):
    """Candidates that need no spending changes."""
    cands = []
    if full_date == req.request_date:
        cands.append(CandidatePlan(
            method=FP,
            payments=[(req.request_date, req.requested_amount)],
            total_cost=req.requested_amount))
    if (req.allows_partial_payment and 0 < safe_amount < req.requested_amount
            and full_date is not None
            and (req.desired_completion_date is None
                 or full_date <= req.desired_completion_date)):
        remainder = round(req.requested_amount - safe_amount, 2)
        cands.append(CandidatePlan(
            method=PP,
            payments=[(req.request_date, safe_amount), (full_date, remainder)],
            total_cost=req.requested_amount))
    for opt in options:
        if opt.payment_method != IT:
            continue
        if ctx.max_installment_months is not None and \
                opt.number_of_payments > ctx.max_installment_months:
            continue
        schedule = _expand_installment(opt)
        if not schedule:
            continue
        if req.desired_completion_date is not None and \
                schedule[-1][0] > req.desired_completion_date:
            continue
        cost = opt.total_payable_amount if opt.total_payable_amount is not None \
            else sum(a for _, a in schedule)
        cands.append(CandidatePlan(
            method=IT, payments=schedule, total_cost=cost,
            payment_option_id=opt.payment_option_id))
    if full_date is not None and full_date != req.request_date:
        cands.append(CandidatePlan(
            method=WAIT,
            payments=[(full_date, req.requested_amount)],
            total_cost=req.requested_amount))
    return cands
def _flex_targets(ctx):
    """Spending-change targets a user is permitted to make.

    Returns (series_targets, explicit_targets):
      series_targets: list of (series, action, value)  action in stop|reduce
      explicit_targets: list of (event, action, value)
    """
    series_targets = []
    for s in ctx.recurring:
        cat = s.key[0]
        if cat in ctx.protected_categories:
            continue
        flex = s.flexibility
        can_stop = flex in ("stoppable", "reducible_or_stoppable") and cat in ctx.stop_categories
        can_reduce = flex in ("reducible", "reducible_or_stoppable") and cat in ctx.reduce_categories
        if can_stop:
            series_targets.append((s, "stop", 0.0))
        if can_reduce and s.min_allowed is not None and s.min_allowed < s.amount:
            series_targets.append((s, "reduce", s.min_allowed))
    explicit_targets = []
    for e in ctx.explicit:
        cat = e.category
        if cat in ctx.protected_categories:
            continue
        if e.flexibility in ("stoppable", "reducible_or_stoppable") and cat in ctx.stop_categories:
            explicit_targets.append((e, "stop", 0.0))
    return series_targets, explicit_targets


def _digits(n, width):
    out = []
    for _ in range(width):
        out.append(n % 3)
        n //= 3
    return out


def spending_change_candidates(req, ctx, safe_amount, full_date,
                               series_targets, explicit_targets):
    """Full_payment-with-spending-changes candidates (affordable_with_plan).

    All FP-with-changes candidates share the same ranking key (same
    payments/cost/date), so the first safe combo with the FEWEST changes
    is optimal: combos are evaluated fewest-changes-first with an early
    exit.  Combinatorics are capped for target-heavy users.
    """
    if full_date == req.request_date:
        return []  # full payment already safe today: no changes needed
    targets = list(series_targets) + list(explicit_targets)
    if not targets:
        return []
    MAX_TARGETS, MAX_COMBOS = 10, 3000
    if len(targets) > MAX_TARGETS:
        targets = targets[:MAX_TARGETS]
    n = len(targets)

    def _nz(c):
        return sum(1 for d in _digits(c, n) if d)

    order = sorted(range(3 ** n), key=_nz)[:MAX_COMBOS]
    seen = set()
    for combo in order:
        r = _eval_combo(ctx, combo, targets)
        if r is None:
            continue
        changes, suppress_categories, reduce_to, suppress_event_ids = r
        key = tuple(changes)
        if key in seen:
            continue
        seen.add(key)
        payments = [(req.request_date, req.requested_amount)]
        if is_safe(ctx, req.request_date,
                   suppress_event_ids=suppress_event_ids,
                   suppress_categories=suppress_categories,
                   reduce_to=reduce_to,
                   extra_payments=payments):
            if not req.desired_completion_date or \
                    req.request_date <= req.desired_completion_date:
                return [CandidatePlan(
                    method=FP, payments=payments,
                    total_cost=req.requested_amount,
                    spending_changes=changes)]
    return []


def _eval_combo(ctx, combo, targets):
    """Return (changes, suppress_categories, reduce_to, suppress_event_ids) or None."""
    suppress_categories = set()
    reduce_to = {}
    suppress_event_ids = set()
    changes = []
    for i, idx in enumerate(_digits(combo, len(targets))):
        if idx == 0 or len(changes) >= 3:
            continue
        t = targets[i]
        if isinstance(t[0], RecurringSeries):
            s, action, value = t
            if action == "stop":
                suppress_categories.add(s.key[0])
                changes.append(f"stop:{s.event_id}")
            else:
                reduce_to[s.key] = value
                changes.append(f"reduce_to:{s.event_id}:{value:g}")
        else:
            e, action, value = t
            suppress_event_ids.add(e.event_id)
            changes.append(f"stop:{e.event_id}")
    if not changes:
        return None
    return changes, suppress_categories, reduce_to, suppress_event_ids
def select_plan(req, ctx, options, safe_amount, full_date):
    """Rank all safe eligible plans and return the chosen CandidatePlan or None."""
    cands = instant_candidates(req, ctx, safe_amount, full_date, options)

    if full_date != req.request_date:
        series_targets, explicit_targets = _flex_targets(ctx)
        cands.extend(spending_change_candidates(
            req, ctx, safe_amount, full_date, series_targets, explicit_targets))

    # eligibility filter
    methods = set(ctx.payment_methods_user_will_consider)
    eligible = []
    for c in cands:
        if c.method in (FP, PP, IT):
            if c.method in methods:
                eligible.append(c)
        elif c.method == WAIT:
            if FP in methods:
                eligible.append(c)
    if not eligible:
        return None
    return min(eligible, key=lambda c: c.key(req))


def _fmt_commas(amount) -> str:
    if abs(amount - round(amount)) < 1e-9:
        return f"{int(round(amount)):,}"
    return f"{amount:,.2f}"


def _fmt_date(d) -> str:
    return f"{d.day} {d:%B} {d.year}"


def build_explanation(req, ctx, chosen, safe_amount) -> str:
    ccy = ctx.home_currency
    mn = _fmt_commas(round(ctx.minimum_balance_to_keep, 2))
    if chosen is None:
        if safe_amount > 0:
            return (f"Although {ccy} {_fmt_commas(safe_amount)} is available today, "
                    f"the full amount cannot be completed safely within 90 days.")
        deadline = req.desired_completion_date
        if deadline:
            return (f"Do not make this payment by {_fmt_date(deadline)}. None of the "
                    f"available options keeps the {ccy} {mn} minimum protected.")
        return (f"None of the available options keeps the {ccy} {mn} minimum "
                f"protected within the forecast period.")

    if chosen.method == FP:
        descs = []
        for ch in chosen.spending_changes:
            parts = ch.split(":")
            eid = parts[1]
            target = _find_target(ctx, eid)
            if not target:
                descs.append(ch)
                continue
            if parts[0] == "stop":
                descs.append(f"Stop the {target}")
            else:
                descs.append(f"reduce the {target} to {ccy} {_fmt_commas(float(parts[2]))}")
        if descs:
            head = f"{descs[0].capitalize()}"
            for d in descs[1:]:
                head += f" and {d}"
            return (f"{head}, then pay {ccy} {_fmt_commas(req.requested_amount)} today. "
                    f"This leaves at least {ccy} {mn} available.")
        return (f"Pay {ccy} {_fmt_commas(req.requested_amount)} today. This leaves at "
                f"least {ccy} {mn} available over the next 90 days.")

    if chosen.method == WAIT:
        d = chosen.payments[0][0]
        return (f"Pay {ccy} {_fmt_commas(req.requested_amount)} in full on "
                f"{_fmt_date(d)}. Paying earlier would take the balance below the "
                f"{ccy} {mn} minimum.")

    if chosen.method == PP:
        d1, a1 = chosen.payments[0]
        d2, a2 = chosen.payments[1]
        return (f"Pay {ccy} {_fmt_commas(a1)} today and the remaining "
                f"{ccy} {_fmt_commas(a2)} on {_fmt_date(d2)}. This completes the "
                f"full request and keeps the {ccy} {mn} minimum protected.")

    # installments
    n = len(chosen.payments)
    amt = chosen.payments[0][1]
    start = chosen.payments[0][0]
    return (f"Use {n} installments of {ccy} {_fmt_commas(amt)}, starting "
            f"{_fmt_date(start)}. This leaves at least {ccy} {mn} available.")


def _find_target(ctx, event_id: str) -> str | None:
    for s in ctx.recurring:
        if s.event_id == event_id:
            return s.description.lower()
    for e in ctx.explicit:
        if e.event_id == event_id:
            return e.description.lower()
    for e in ctx.events:
        if e.event_id == event_id:
            return e.description.lower()
    return None


def build_output_row(req, ctx, safe_amount, full_date, chosen, explanation):
    from models import OutputRow, fmt_amount
    if chosen is None:
        return OutputRow(
            request_id=req.request_id,
            amount_safe_to_pay=safe_amount,
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payment_plan="none",
            earliest_date_for_full_payment=full_date.isoformat() if full_date else "",
            spending_changes_needed="none",
            decision_explanation=explanation)

    if chosen.method == FP and not chosen.spending_changes:
        status = "affordable_now"
    elif chosen.method == WAIT:
        status = "affordable_later"
    else:
        status = "affordable_with_plan"

    plan = "|".join(f"{d.isoformat()}:{fmt_amount(a)}" for d, a in chosen.payments)
    changes = "|".join(chosen.spending_changes) if chosen.spending_changes else "none"

    return OutputRow(
        request_id=req.request_id,
        amount_safe_to_pay=safe_amount,
        affordability_status=status,
        recommended_payment_method=chosen.method,
        payment_plan=plan,
        earliest_date_for_full_payment=full_date.isoformat() if full_date else "",
        spending_changes_needed=changes,
        decision_explanation=explanation)