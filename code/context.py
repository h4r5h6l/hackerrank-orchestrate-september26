"""Per-request user financial context.

Reconstructs, for one (user, request_date):
  * the profile
  * all financial events, with blank amounts filled from images
  * recurring-series projections (detected from settled history)
  * explicit future events (settled / scheduled / pending-debit)
  * message-driven amendments (salary amount/date/stop/resume, rent
    multiplier, one-time confirmed credits)

forecast.py turns the resulting context into daily balance timelines.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from evidence import MessageFacts, parse_message_facts, resolve_image_amount
from loaders import DataBundle
from models import Request

HORIZON_DAYS = 90

# A series must have this many cadence-consistent gaps to be recurring.
MIN_GAPS = 3
# Gap multiples outside [lo, hi] x median are treated as outliers.
GAP_LO, GAP_HI = 0.5, 1.5
# Monthly period => snap projection to the same day-of-month.
MONTHLY_LO, MONTHLY_HI = 25, 33


@dataclass
class RecurringSeries:
    key: tuple          # (category, direction, event_type)
    cadence: int
    anchor_date: date   # last cadence-consistent occurrence date
    amount: float       # per-occurrence amount (source currency)
    flexibility: str
    min_allowed: float | None
    currency: str = ""
    event_id: str | None = None
    description: str = ""


@dataclass
class Occurrence:
    day: date
    amount: float       # signed, home currency
    essential: bool = True
    event_id: str | None = None


@dataclass
class SalaryProjection:
    amount: float
    day_of_month: int
    last_date: date
    currency: str = ""
    stopped: bool = False
    shift_date: date | None = None
    resume_amount: float | None = None
    resume_date: date | None = None
    amount_from: dict = field(default_factory=dict)  # date -> amount


@dataclass
class UserContext:
    user_id: str
    home_currency: str
    available_balance: float
    minimum_balance_to_keep: float
    payment_methods_user_will_consider: list
    max_installment_months: int | None
    protected_categories: set
    reduce_categories: set
    stop_categories: set
    events: list
    recurring: list
    explicit: list
    one_time_credits: list
    salary: SalaryProjection | None
    rent_mult: float = 1.0
    _rates: object = None

    def to_home(self, amount: float, on_date: date, from_ccy: str) -> float:
        if not from_ccy or from_ccy == self.home_currency:
            return amount
        if self._rates is None:
            return amount
        return self._rates.convert(amount, on_date, from_ccy, self.home_currency)
def _month_step(d: date) -> date:
    """Same day-of-month next month (clamped to month end)."""
    if d.month == 12:
        y, m = d.year + 1, 1
    else:
        y, m = d.year, d.month + 1
    import calendar
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def next_salary_on_or_after(salary: SalaryProjection, start: date) -> date:
    d = salary.last_date
    while d < start:
        d = _month_step(d)
    return d


def build_user_context(req: Request, bundle: DataBundle, rates) -> UserContext:
    profile = bundle.profiles[req.user_id]
    raw_events = list(bundle.events_by_user.get(req.user_id, []))

    # 1. resolve blank amounts from linked images
    for e in raw_events:
        if e.amount is None:
            imgs = bundle.images_by_event.get(e.event_id, [])
            e.amount = resolve_image_amount(imgs[0]) if imgs else None

    # 2. gather message facts (newest wins for conflicting fields)
    facts = MessageFacts()
    for msg in bundle.messages_by_user.get(req.user_id, []):
        f = parse_message_facts(msg)
        if f.salary_amount:
            facts.salary_amount = f.salary_amount
        if f.salary_date_shift:
            facts.salary_date_shift = f.salary_date_shift
        if f.salary_stop:
            facts.salary_stop = True
        if f.salary_resume_date:
            facts.salary_resume_date = f.salary_resume_date
        if f.one_time_credit:
            facts.one_time_credit.extend(f.one_time_credit)
        if f.rent_mult:
            facts.rent_mult = f.rent_mult

    history = [e for e in raw_events
               if e.cash_date is not None and e.cash_date < req.request_date]
    future = [e for e in raw_events
              if e.cash_date is not None and e.cash_date >= req.request_date]
    hist_settled = [e for e in history if e.status == "settled" and e.amount is not None]

    rent_mult = facts.rent_mult[0][0] if facts.rent_mult else 1.0

    # 3. recurring series from settled history (salary handled separately)
    recurring = _detect_recurring(hist_settled)

    # 4. salary projection from history + message amendments
    salary = _project_salary(hist_settled, facts)

    # 5. one-time confirmed credits (undated ones attach to the next salary)
    one_time = [(a, d, c) for a, d, c in facts.one_time_credit]
    if one_time and salary:
        for a, d, c in list(one_time):
            if d is None:
                one_time.append((a, next_salary_on_or_after(salary, req.request_date), c))

    return UserContext(
        user_id=profile.user_id,
        home_currency=profile.home_currency,
        available_balance=profile.current_available_balance,
        minimum_balance_to_keep=profile.minimum_balance_to_keep,
        payment_methods_user_will_consider=profile.payment_methods_user_will_consider,
        max_installment_months=profile.max_installment_months,
        protected_categories=profile.protected_categories,
        reduce_categories=profile.reduce_categories,
        stop_categories=profile.stop_categories,
        events=raw_events,
        recurring=recurring,
        explicit=future,
        one_time_credits=one_time,
        salary=salary,
        rent_mult=rent_mult,
        _rates=rates,
    )
def _robust_median_gaps(dates):
    """Return (median_gap, list_of_valid_gaps) with 0.5x..1.5x median filter."""
    if len(dates) < 2:
        return None, []
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    if not gaps:
        return None, []
    med = sorted(gaps)[len(gaps) // 2]
    if med <= 0:
        return None, []
    valid = [g for g in gaps if GAP_LO * med <= g <= GAP_HI * med]
    if len(valid) < len(gaps) // 2:
        return None, []
    return med, valid


def _detect_recurring(hist_settled) -> list:
    by_key = defaultdict(list)
    for e in hist_settled:
        if e.category in ("salary",):
            continue
        if e.direction == "credit":
            continue  # non-salary inflows (refunds, etc.) are not projected
        if e.event_type in ("refund", "investment_purchase", "investment_valuation",
                            "investment_sale"):
            continue
        by_key[(e.category, e.direction, e.event_type)].append(e)

    series = []
    for key, events in by_key.items():
        events.sort(key=lambda e: e.cash_date)
        dates = [e.cash_date for e in events]
        if len(dates) < 4:
            # need enough history to call something recurring
            continue
        med, valid = _robust_median_gaps(dates)
        if med is None or len(valid) < MIN_GAPS:
            continue
        cadence = sorted(valid)[len(valid) // 2]
        anchor = events[-1]
        # amount = last observed amount (tuned during calibration)
        series.append(RecurringSeries(
            key=key, cadence=cadence, anchor_date=anchor.cash_date,
            amount=anchor.amount, flexibility=anchor.flexibility,
            min_allowed=anchor.minimum_allowed_amount, currency=anchor.currency,
            event_id=anchor.event_id, description=anchor.description,
        ))
    return series


def _project_salary(hist_settled, facts) -> SalaryProjection | None:
    salary_events = sorted(
        [e for e in hist_settled if e.category == "salary" and e.direction == "credit"],
        key=lambda e: e.cash_date)
    if not salary_events:
        return None

    dates = [e.cash_date for e in salary_events]
    med, valid = _robust_median_gaps(dates)
    if med is None or len(valid) < MIN_GAPS:
        return None
    cadence = sorted(valid)[len(valid) // 2]

    # anchor: last cadence-consistent event
    anchor = salary_events[0]
    for i in range(1, len(salary_events)):
        gap = (salary_events[i].cash_date - salary_events[i - 1].cash_date).days
        if GAP_LO * cadence <= gap <= GAP_HI * cadence:
            anchor = salary_events[i]
        elif (salary_events[i].cash_date - anchor.cash_date).days <= GAP_HI * cadence:
            anchor = salary_events[i]
    base_amount = anchor.amount or 0.0

    proj = SalaryProjection(
        amount=base_amount,
        day_of_month=anchor.cash_date.day,
        last_date=anchor.cash_date,
        currency=anchor.currency,
    )

    # apply message amendments (newest wins)
    for amt, from_date, ccy in facts.salary_amount:
        if ccy:
            proj.currency = ccy
        if from_date is not None:
            proj.amount_from[from_date] = amt
        else:
            proj.amount = amt
    if facts.salary_stop:
        proj.stopped = True
    if facts.salary_resume_date:
        amt, d, ccy = facts.salary_resume_date[-1]
        proj.resume_amount = amt if amt is not None else proj.amount
        if ccy:
            proj.currency = ccy
        proj.resume_date = d
        proj.stopped = False
    if facts.salary_date_shift:
        proj.shift_date = datetime.strptime(facts.salary_date_shift[-1], "%Y-%m-%d").date()
    return proj