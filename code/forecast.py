"""90-day daily balance timeline and safety checks.

Input: a UserContext (recurring series + explicit events + salary
projection + one-time credits). Output: daily balances.

Safety rule: the balance must never fall below
minimum_balance_to_keep at any day in [request_date, end].
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from context import (HORIZON_DAYS, MONTHLY_HI, MONTHLY_LO, Occurrence,
                     UserContext, _month_step)


def _series_next(anchor: date, cadence: int) -> date:
    if MONTHLY_LO <= cadence <= MONTHLY_HI:
        return _month_step(anchor)
    return anchor + timedelta(days=cadence)


def _salary_occurrences(proj, request_date, end, essential=True):
    """Projected salary occurrences in [request_date, end]."""
    out = []
    if proj is None:
        return out

    def amended(d, base):
        amt = base
        for from_d, v in sorted(proj.amount_from.items()):
            if d >= from_d:
                amt = v
        return amt

    if proj.stopped and proj.resume_date:
        d = proj.resume_date
        base = proj.resume_amount if proj.resume_amount is not None else proj.amount
        while d <= end:
            if d >= request_date:
                out.append((d, amended(d, base)))
            d = _month_step(d)
    elif not proj.stopped:
        first = True
        d = _month_step(proj.last_date)
        while d <= end:
            if d >= request_date:
                amt = amended(d, proj.amount)
                # a message may move the next payday earlier (salary_date_shift)
                if first and proj.shift_date and proj.shift_date >= request_date:
                    out.append((proj.shift_date, amt))
                    d = proj.shift_date
                else:
                    out.append((d, amt))
                first = False
            d = _month_step(d)
    return out


def build_occurrences(ctx: UserContext, request_date: date,
                      suppress_event_ids=frozenset(),
                      suppress_categories=(),
                      reduce_to: dict = None,
                      extra_payments=()) -> list:
    """All cash occurrences in [request_date, request_date + HORIZON_DAYS].

    extra_payments: (date, -amount) tuples for candidate plans.
    suppress_event_ids: drop these explicit events (spending change).
    suppress_categories: drop recurring series in these categories.
    reduce_to: series key -> amount to use instead (reduce spending change).
    """
    reduce_to = reduce_to or {}
    end = request_date + timedelta(days=HORIZON_DAYS)
    out = []

    # -- explicit future events --------------------------------------------
    explicit_debit_dates = defaultdict(set)
    for e in ctx.explicit:
        if e.cash_date is None or not (request_date <= e.cash_date <= end):
            continue
        if e.event_id in suppress_event_ids:
            continue
        if e.amount is None:
            continue
        if e.direction != "credit":
            explicit_debit_dates[e.category].add(e.cash_date)
        signed = e.amount if e.direction == "credit" else -abs(e.amount)
        signed = ctx.to_home(signed, e.cash_date, e.currency)
        out.append(Occurrence(day=e.cash_date, amount=signed,
                              essential=(e.flexibility == "fixed"),
                              event_id=e.event_id))

# __PART2__

    # -- recurring series -------------------------------------------------------
    # A cadence-projected occurrence that lands on (or near) an explicit
    # supplied debit of the same category is the SAME payment, not a new
    # one: the explicit row is the record of truth for that period.  Skip
    # the projection within a tolerance so the debit is counted once.
    for s in ctx.recurring:
        if s.key[0] in suppress_categories:
            continue
        debit_dates = sorted(explicit_debit_dates.get(s.key[0], ()))
        tol = max(2, min(7, s.cadence // 4))
        amount = s.amount if s.key not in reduce_to else reduce_to[s.key]
        if ctx.rent_mult != 1.0 and s.key[0] == "rent":
            amount *= ctx.rent_mult
        if amount <= 0:
            continue
        essential = (s.flexibility == "fixed")
        d = _series_next(s.anchor_date, s.cadence)
        while d <= end:
            near_explicit = any(abs((dd - d).days) <= tol for dd in debit_dates)
            if d >= request_date and not near_explicit:
                home = ctx.to_home(amount, d, s.currency)
                out.append(Occurrence(day=d, amount=-abs(home),
                                      essential=essential))
            d = _series_next(d, s.cadence)

    # -- salary projection (positive, already message-amended) -------------------
    for d, amt in _salary_occurrences(ctx.salary, request_date, end):
        out.append(Occurrence(day=d,
                              amount=abs(ctx.to_home(amt, d, ctx.salary.currency)),
                              essential=True))

    # -- one-time confirmed credits ----------------------------------------------
    for amt, d, ccy in ctx.one_time_credits:
        if amt is None or d is None:
            continue
        if request_date <= d <= end:
            out.append(Occurrence(day=d,
                                  amount=abs(ctx.to_home(amt, d, ccy)),
                                  essential=True))

    # -- candidate plan payments ---------------------------------------------------
    for d, p in extra_payments:
        if request_date <= d <= end:
            out.append(Occurrence(day=d, amount=p, essential=True))

    out.sort(key=lambda o: (o.day, o.amount))
    return out


def timeline_min_balance(ctx: UserContext, request_date: date,
                         suppress_event_ids=frozenset(),
                         suppress_categories=(),
                         reduce_to: dict = None,
                         extra_payments=()) -> float:
    occs = build_occurrences(ctx, request_date,
                             suppress_event_ids=suppress_event_ids,
                             suppress_categories=suppress_categories,
                             reduce_to=reduce_to,
                             extra_payments=extra_payments)
    balance = ctx.available_balance
    floor = balance
    idx = 0
    day = request_date
    end = request_date + timedelta(days=HORIZON_DAYS)
    while day <= end:
        while idx < len(occs) and occs[idx].day == day:
            balance += occs[idx].amount
            idx += 1
        floor = min(floor, balance)
        day += timedelta(days=1)
    return floor


def is_safe(ctx: UserContext, request_date: date,
            suppress_event_ids=frozenset(), suppress_categories=(),
            reduce_to: dict = None, extra_payments=()) -> bool:
    return timeline_min_balance(
        ctx, request_date, suppress_event_ids, suppress_categories, reduce_to,
        extra_payments) >= ctx.minimum_balance_to_keep - 1e-6


def amount_safe_to_pay(ctx: UserContext, request_date: date,
                       requested_amount: float) -> float:
    """Max payable on request_date (no spending changes), capped at requested_amount."""
    if requested_amount <= 0:
        return 0.0
    if not is_safe(ctx, request_date, extra_payments=[(request_date, -requested_amount)]):
        lo, hi = 0.0, requested_amount
    else:
        return round(requested_amount, 2)
    for _ in range(45):
        mid = (lo + hi) / 2
        if is_safe(ctx, request_date, extra_payments=[(request_date, -mid)]):
            lo = mid
        else:
            hi = mid
    return round(lo, 2)


def earliest_date_for_full_payment(ctx: UserContext, request_date: date,
                                   requested_amount: float) -> date | None:
    """First day the FULL payment is safe (no spending changes)."""
    end = request_date + timedelta(days=HORIZON_DAYS)
    day = request_date
    while day <= end:
        if is_safe(ctx, request_date, extra_payments=[(day, -requested_amount)]):
            return day
        day += timedelta(days=1)
    return None
