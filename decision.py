"""Turn a user's forecast + payment options into the required output row.

Implements:
  - eligibility (which payment methods the user will even consider)
  - the 6-level tie-break ranking between safe eligible plans
  - the partial_payment structural rules (exactly 2 payments, sums to
    requested_amount, etc.)
  - spending_changes_needed formatting (stop: / reduce_to:, mutually
    exclusive per event, max 3 entries)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from context import UserContext
from forecast import amount_safe_to_pay, earliest_date_for_full_payment, is_safe
from models import OutputRow, PaymentOption, Request


@dataclass
class CandidatePlan:
	method: str  # full_payment | partial_payment | installments | wait
	payments: list[tuple[date, float]]  # chronological
	payment_option_id: str | None = None  # for installments, for tie-break #6
	spending_changes: list[str] = None  # ["stop:event_14", "reduce_to:event_21:100"]

	def total_paid(self) -> float:
		return sum(amt for _, amt in self.payments)

	def start_date(self) -> date:
		return self.payments[0][0] if self.payments else date.max

	def completes_by(self, deadline: date | None) -> bool:
		if deadline is None:
			return True
		return self.payments[-1][0] <= deadline if self.payments else False


def build_candidates(
	req: Request,
	ctx: UserContext,
	options: list[PaymentOption],
	safe_amount_today: float,
	full_payment_date: date | None,
) -> list[CandidatePlan]:
	"""Enumerate the safe candidate plans. Each candidate must already be
	verified safe (via forecast.is_safe) before being added here -- this
	function assembles shapes, it does not itself judge safety for
	installments/spending-change variants; call is_safe() per candidate
	before including it.
	"""
	candidates: list[CandidatePlan] = []

	# full_payment: only if the whole amount is safe on request_date
	if full_payment_date == req.request_date:
		candidates.append(
			CandidatePlan(
				method="full_payment",
				payments=[(req.request_date, req.requested_amount)],
			)
		)

	# partial_payment: exactly two payments, per the structural rule
	if (
		req.allows_partial_payment
		and 0 < safe_amount_today < req.requested_amount
		and full_payment_date is not None
		and req.desired_completion_date is not None
		and full_payment_date <= req.desired_completion_date
	):
		remainder = round(req.requested_amount - safe_amount_today, 2)
		candidates.append(
			CandidatePlan(
				method="partial_payment",
				payments=[
					(req.request_date, safe_amount_today),
					(full_payment_date, remainder),
				],
			)
		)

	# installments: must exactly match a supplied payment option AND be
	# verified safe via the caller (see main.py loop) before appending here.
	for opt in options:
		schedule = _expand_installment_schedule(opt)
		if req.desired_completion_date and schedule[-1][0] > req.desired_completion_date:
			continue
		candidates.append(
			CandidatePlan(method="installments", payments=schedule, payment_option_id=opt.payment_option_id)
		)

	# wait: full payment safe later, but not usable if it's already safe now
	if full_payment_date is not None and full_payment_date != req.request_date:
		candidates.append(
			CandidatePlan(method="wait", payments=[(full_payment_date, req.requested_amount)])
		)

	return candidates


def _expand_installment_schedule(opt: PaymentOption) -> list[tuple[date, float]]:
	from datetime import timedelta

	amount = opt.amount_per_payment or 0.0
	schedule = []
	current = opt.start_date
	for _ in range(opt.num_payments):
		schedule.append((current, amount))
		current = current + timedelta(days=opt.days_between_payments)
	return schedule


def filter_eligible(
	candidates: list[CandidatePlan], accepted_methods: list[str]
) -> list[CandidatePlan]:
	eligible = []
	for c in candidates:
		if c.method in {"full_payment", "partial_payment", "installments"}:
			if c.method in accepted_methods:
				eligible.append(c)
		elif c.method == "wait":
			if "full_payment" in accepted_methods:
				eligible.append(c)
	return eligible


def rank_candidates(
	candidates: list[CandidatePlan], req: Request
) -> list[CandidatePlan]:
	"""Sort by the 6-level tie-break order from problem_statement.md."""

	def key(c: CandidatePlan):
		completes = 0 if c.completes_by(req.desired_completion_date) else 1
		needs_changes = 1 if (c.spending_changes) else 0
		total = c.total_paid()
		start = c.start_date()
		num_payments = len(c.payments)
		option_id = c.payment_option_id or ""
		return (completes, needs_changes, total, start, num_payments, option_id)

	return sorted(candidates, key=key)


def build_output_row(
	req: Request,
	ctx: UserContext,
	safe_amount_today: float,
	full_payment_date: date | None,
	chosen: CandidatePlan | None,
	explanation: str,
) -> OutputRow:
	if chosen is None:
		return OutputRow(
			request_id=req.request_id,
			amount_safe_to_pay=safe_amount_today,
			affordability_status="not_affordable",
			recommended_payment_method="not_recommended",
			payment_plan="none",
			earliest_date_for_full_payment=(
				full_payment_date.isoformat() if full_payment_date else ""
			),
			spending_changes_needed="none",
			decision_explanation=explanation,
		)

	if chosen.method == "full_payment":
		status = "affordable_now"
	elif chosen.method == "wait":
		status = "affordable_later"
	else:
		status = "affordable_with_plan"

	plan_str = "|".join(f"{d.isoformat()}:{amt:g}" for d, amt in chosen.payments)
	changes_str = "|".join(chosen.spending_changes) if chosen.spending_changes else "none"

	return OutputRow(
		request_id=req.request_id,
		amount_safe_to_pay=safe_amount_today,
		affordability_status=status,
		recommended_payment_method=chosen.method,
		payment_plan=plan_str,
		earliest_date_for_full_payment=(
			full_payment_date.isoformat() if full_payment_date else ""
		),
		spending_changes_needed=changes_str,
		decision_explanation=explanation,
	)
