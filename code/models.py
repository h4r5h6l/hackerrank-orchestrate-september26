"""Typed data model for the 'Buy or Wait?' financial agent.

Field names mirror the real dataset/ CSV columns exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: str = ""
    protected_categories: set = field(default_factory=set)
    reduce_categories: set = field(default_factory=set)
    stop_categories: set = field(default_factory=set)
    payment_methods_user_will_consider: list = field(default_factory=list)
    max_installment_months: Optional[int] = None


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str            # income / expense / subscription / debt_payment / refund / investment_* / ...
    description: str
    category: str              # salary / rent / utilities / groceries / dining / ...
    direction: str             # debit / credit / non_cash
    amount: Optional[float]    # None means "resolve via linked image"
    currency: str
    event_date: Optional[date]
    settlement_date: Optional[date]
    status: str                # settled / pending / scheduled / cancelled / failed / unrealized / ...
    linked_event_id: Optional[str] = None
    flexibility: str = "fixed"  # fixed / reducible / stoppable / reducible_or_stoppable
    minimum_allowed_amount: Optional[float] = None
    raw: dict = field(default_factory=dict)

    @property
    def cash_date(self) -> Optional[date]:
        return self.settlement_date or self.event_date

    def is_cash(self) -> bool:
        if self.direction == "non_cash":
            return False
        if self.status in {"failed", "cancelled", "unrealized", "duplicate"}:
            return False
        if self.status == "pending" and self.direction == "credit":
            return False  # pending credits (prizes, refunds) are not reserveable
        return True


@dataclass
class ExchangeRate:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: float


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str        # full_payment / installments
    payment_amount: Optional[float]
    number_of_payments: int
    first_payment_date: Optional[date]
    payment_frequency_days: Optional[int]
    financing_fee: float = 0.0
    total_payable_amount: Optional[float] = None


@dataclass
class Message:
    message_id: str
    user_id: Optional[str]
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: Optional[datetime]
    source_type: str = ""
    text: str = ""


@dataclass
class ImageRef:
    image_id: str
    user_id: Optional[str]
    request_id: Optional[str]
    related_event_id: Optional[str]
    path: str = ""


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: float
    desired_completion_date: Optional[date]
    allows_partial_payment: bool
    request_text: str = ""


@dataclass
class OutputRow:
    request_id: str
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str

    def as_row(self) -> list:
        return [
            self.request_id,
            self.amount_safe_to_pay,
            self.affordability_status,
            self.recommended_payment_method,
            self.payment_plan,
            self.earliest_date_for_full_payment,
            self.spending_changes_needed,
            self.decision_explanation,
        ]


OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

ALLOWED_AFFORDABILITY_STATUS = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}

ALLOWED_PAYMENT_METHOD = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}


def fmt_amount(value: Optional[float]) -> str:
    """Amount formatting used inside payment_plan (ints stay int, floats get 2dp)."""
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}"


def fmt_safe_amount(value: Optional[float]) -> str:
    """Column formatting for amount_safe_to_pay: up to 2 decimals, no trailing zeros."""
    if value is None:
        return ""
    s = f"{value:.2f}"
    if s.endswith(".00"):
        return s[:-3]
    if s.endswith("0"):
        return s[:-1]
    return s