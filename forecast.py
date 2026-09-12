"""90-day balance forecast and the safety-check primitives built on it.

Core idea: build a daily balance timeline for a user from
available_balance + recurring/one-off events over the forecast window,
then answer two questions against it:
  - amount_safe_to_pay(request_date): max payable today without breaching
    minimum_balance_to_keep at any point in the window
  - earliest_date_for_full_payment(amount): first date the full amount
    clears the same check
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from context import UserContext
from models import FinancialEvent

FORECAST_DAYS = 90


@dataclass
class DailyBalance:
	day: date
	balance: float
	essential_outflow: float  # essential (non-flexible) spend booked that day


def build_daily_timeline(
	ctx: UserContext,
	start_date: date,
	horizon_days: int = FORECAST_DAYS,
	suppress_event_ids: frozenset[str] = frozenset(),
	extra_payments: list[tuple[date, float]] = (),
) -> list[DailyBalance]:
	"""Project balance day-by-day.

	`suppress_event_ids` lets the decision layer test "what if we stop/reduce
	this flexible expense" without mutating ctx.events.
	`extra_payments` overlays a candidate payment plan on top of the
	baseline so we can test whether IT keeps the balance safe.
	"""
	end_date = start_date + timedelta(days=horizon_days)
	occurrences = _expand_events_to_occurrences(ctx.events, start_date, end_date, suppress_event_ids)
	occurrences.extend(extra_payments)
	occurrences.sort(key=lambda t: t[0])

	timeline: list[DailyBalance] = []
	balance = ctx.available_balance
	occ_iter = iter(occurrences)
	next_occ = next(occ_iter, None)

	day = start_date
	while day <= end_date:
		essential_today = 0.0
		while next_occ is not None and next_occ[0] == day:
			balance += next_occ[1]
			if len(next_occ) > 2 and next_occ[2] == "essential":
				essential_today += -next_occ[1] if next_occ[1] < 0 else 0.0
			next_occ = next(occ_iter, None)
		timeline.append(DailyBalance(day=day, balance=balance, essential_outflow=essential_today))
		day += timedelta(days=1)

	return timeline


def _expand_events_to_occurrences(
	events: list[FinancialEvent],
	start_date: date,
	end_date: date,
	suppress_event_ids: frozenset[str],
) -> list[tuple]:
	"""Turn events (one-off and recurring) into (date, signed_amount, tag)
	occurrences within [start_date, end_date].

	TODO: recurring events need a cadence field (e.g. frequency_days or an
	explicit schedule) from financial_events.csv -- wire that in once the
	exact column is confirmed. One-off events with a date in range are
	included as-is; income is a positive amount, expenses negative.
	"""
	occurrences: list[tuple] = []
	for event in events:
		if event.event_id in suppress_event_ids:
			continue
		if event.amount is None or event.event_date is None:
			continue
		signed = event.amount if _is_inflow(event) else -abs(event.amount)
		tag = "essential" if not event.is_flexible else "flexible"

		if not event.is_recurring:
			if start_date <= event.event_date <= end_date:
				occurrences.append((event.event_date, signed, tag))
			continue

		# Recurring: TODO plug in real cadence once schema confirms it.
		# Placeholder assumes monthly (~30 days) recurrence from event_date.
		cursor = event.event_date
		while cursor <= end_date:
			if cursor >= start_date:
				occurrences.append((cursor, signed, tag))
			cursor += timedelta(days=30)

	return occurrences


def _is_inflow(event: FinancialEvent) -> bool:
	return event.event_type in {"income", "salary", "refund"}


def min_balance_over(timeline: list[DailyBalance]) -> float:
	return min(d.balance for d in timeline) if timeline else float("-inf")


def is_safe(
	ctx: UserContext,
	start_date: date,
	horizon_days: int = FORECAST_DAYS,
	suppress_event_ids: frozenset[str] = frozenset(),
	extra_payments: list[tuple[date, float]] = (),
) -> bool:
	timeline = build_daily_timeline(
		ctx, start_date, horizon_days, suppress_event_ids, extra_payments
	)
	return min_balance_over(timeline) >= ctx.minimum_balance_to_keep


def amount_safe_to_pay(
	ctx: UserContext,
	request_date: date,
	requested_amount: float,
	horizon_days: int = FORECAST_DAYS,
) -> float:
	"""Binary-search the max payable today (no spending changes) that keeps
	every day of the forecast >= minimum_balance_to_keep, capped at
	requested_amount.
	"""
	lo, hi = 0.0, requested_amount
	if not is_safe(ctx, request_date, horizon_days, extra_payments=[(request_date, 0.0)]):
		return 0.0  # baseline itself is already unsafe
	for _ in range(40):  # sufficient precision for currency-scale amounts
		mid = (lo + hi) / 2
		if is_safe(ctx, request_date, horizon_days, extra_payments=[(request_date, -mid)]):
			lo = mid
		else:
			hi = mid
	return round(lo, 2)


def earliest_date_for_full_payment(
	ctx: UserContext,
	request_date: date,
	requested_amount: float,
	desired_completion_date: date | None,
	horizon_days: int = FORECAST_DAYS,
) -> date | None:
	"""First date within the forecast window where paying the FULL amount
	on that date keeps the balance safe. Returns None if it never becomes
	safe within the window.
	"""
	end_date = request_date + timedelta(days=horizon_days)
	day = request_date
	while day <= end_date:
		if is_safe(ctx, request_date, horizon_days, extra_payments=[(day, -requested_amount)]):
			return day
		day += timedelta(days=1)
	return None
