"""Typed data model for the 'Buy or Wait?' financial affordability agent.

These dataclasses mirror the CSV schemas described in problem_statement.md.
Keep field names identical to the CSV column names so loaders.py can build
these with **row.to_dict() without a translation layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class FinancialProfile:
	user_id: str
	home_currency: str
	available_balance: float
	minimum_balance_to_keep: float
	financial_priorities: str          # raw string; parse to list where needed
	spending_preferences: str          # raw string; parse to list where needed
	payment_methods_user_will_consider: list[str] = field(default_factory=list)


@dataclass
class FinancialEvent:
	event_id: str
	user_id: str
	event_date: Optional[date]
	event_type: str                    # e.g. income, recurring_expense, one_time, investment, transfer, refund
	amount: Optional[float]            # None means "resolve via linked image" -- never treat as 0
	currency: str
	is_recurring: bool = False
	is_flexible: bool = False          # only flexible recurring expenses may be stopped/reduced
	status: str = "confirmed"          # confirmed / pending / cancelled / failed / estimate, etc.
	linked_event_id: Optional[str] = None  # points to an earlier event in the same lifecycle
	raw: dict = field(default_factory=dict)  # keep original row for fields not modeled explicitly


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
	start_date: date
	num_payments: int
	days_between_payments: int
	amount_per_payment: Optional[float]
	financing_fee: float = 0.0
	total_payable: Optional[float] = None


@dataclass
class Message:
	message_id: str
	user_id: Optional[str]
	request_id: Optional[str]
	related_event_id: Optional[str]
	message_date: Optional[date]
	text: str


@dataclass
class ImageRef:
	image_id: str
	user_id: Optional[str]
	request_id: Optional[str]
	related_event_id: Optional[str]
	path: str


@dataclass
class Request:
	request_id: str
	user_id: str
	request_date: date
	request_type: str
	requested_amount: float
	desired_completion_date: Optional[date]
	allows_partial_payment: bool
	request_text: str


@dataclass
class OutputRow:
	request_id: str
	amount_safe_to_pay: float
	affordability_status: str
	recommended_payment_method: str
	payment_plan: str
	earliest_date_for_full_payment: str  # "" allowed when not expected within forecast
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

ALLOWED_REQUEST_TYPE = {
	"purchase",
	"travel",
	"education",
	"family_transfer",
	"debt_repayment",
	"investment",
	"housing",
	"emergency_expense",
	"other",
}
