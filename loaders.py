"""Load and join all dataset CSVs into a single DataBundle.

Handles:
  - date/bool parsing
  - the blank-amount-via-image resolution rule (event.amount is None ->
    look up images.csv by related_event_id -> OCR/vision-extract the amount)

Does NOT do currency conversion (see fx.py) or message-driven amendments
(see context.py) -- this module is purely "read the CSVs into typed objects".
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from models import (
	ExchangeRate,
	FinancialEvent,
	FinancialProfile,
	ImageRef,
	Message,
	PaymentOption,
	Request,
)


def _parse_date(value: str) -> date | None:
	value = (value or "").strip()
	if not value:
		return None
	return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_bool(value: str) -> bool:
	return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _parse_float(value: str) -> float | None:
	value = (value or "").strip()
	if not value:
		return None
	return float(value)


def _parse_list(value: str, sep: str = "|") -> list[str]:
	value = (value or "").strip()
	if not value:
		return []
	return [v.strip() for v in value.split(sep) if v.strip()]


def _read_csv(path: Path) -> list[dict]:
	with path.open(newline="", encoding="utf-8") as f:
		return list(csv.DictReader(f))


@dataclass
class DataBundle:
	requests: dict[str, Request] = field(default_factory=dict)
	profiles: dict[str, FinancialProfile] = field(default_factory=dict)
	events: dict[str, FinancialEvent] = field(default_factory=dict)
	events_by_user: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
	rates: list[ExchangeRate] = field(default_factory=list)
	payment_options: dict[str, list[PaymentOption]] = field(default_factory=lambda: defaultdict(list))
	messages_by_event: dict[str, list[Message]] = field(default_factory=lambda: defaultdict(list))
	messages_by_request: dict[str, list[Message]] = field(default_factory=lambda: defaultdict(list))
	messages_by_user: dict[str, list[Message]] = field(default_factory=lambda: defaultdict(list))
	images_by_event: dict[str, list[ImageRef]] = field(default_factory=lambda: defaultdict(list))
	images_by_request: dict[str, list[ImageRef]] = field(default_factory=lambda: defaultdict(list))
	media_dir: Path = Path("dataset/media/images")


def load_all(dataset_dir: str | Path) -> DataBundle:
	dataset_dir = Path(dataset_dir)
	bundle = DataBundle(media_dir=dataset_dir / "media" / "images")

	for row in _read_csv(dataset_dir / "financial_profiles.csv"):
		profile = FinancialProfile(
			user_id=row["user_id"],
			home_currency=row["home_currency"],
			available_balance=_parse_float(row["available_balance"]) or 0.0,
			minimum_balance_to_keep=_parse_float(row["minimum_balance_to_keep"]) or 0.0,
			financial_priorities=row.get("financial_priorities", ""),
			spending_preferences=row.get("spending_preferences", ""),
			payment_methods_user_will_consider=_parse_list(
				row.get("payment_methods_user_will_consider", "")
			),
		)
		bundle.profiles[profile.user_id] = profile

	for row in _read_csv(dataset_dir / "financial_events.csv"):
		event = FinancialEvent(
			event_id=row["event_id"],
			user_id=row["user_id"],
			event_date=_parse_date(row.get("event_date", "")),
			event_type=row.get("event_type", ""),
			amount=_parse_float(row.get("amount", "")),  # may be None -> resolve via image below
			currency=row.get("currency", ""),
			is_recurring=_parse_bool(row.get("is_recurring", "")),
			is_flexible=_parse_bool(row.get("is_flexible", "")),
			status=row.get("status", "confirmed"),
			linked_event_id=row.get("linked_event_id") or None,
			raw=row,
		)
		bundle.events[event.event_id] = event
		bundle.events_by_user[event.user_id].append(event.event_id)

	for row in _read_csv(dataset_dir / "exchange_rates.csv"):
		bundle.rates.append(
			ExchangeRate(
				rate_date=_parse_date(row["rate_date"]),
				from_currency=row["from_currency"],
				to_currency=row["to_currency"],
				rate=float(row["rate"]),
			)
		)

	for row in _read_csv(dataset_dir / "request_payment_options.csv"):
		opt = PaymentOption(
			payment_option_id=row["payment_option_id"],
			request_id=row["request_id"],
			start_date=_parse_date(row.get("start_date", "")),
			num_payments=int(row.get("num_payments") or 1),
			days_between_payments=int(row.get("days_between_payments") or 0),
			amount_per_payment=_parse_float(row.get("amount_per_payment", "")),
			financing_fee=_parse_float(row.get("financing_fee", "")) or 0.0,
			total_payable=_parse_float(row.get("total_payable", "")),
		)
		bundle.payment_options[opt.request_id].append(opt)

	for row in _read_csv(dataset_dir / "messages.csv"):
		msg = Message(
			message_id=row["message_id"],
			user_id=row.get("user_id") or None,
			request_id=row.get("request_id") or None,
			related_event_id=row.get("related_event_id") or None,
			message_date=_parse_date(row.get("message_date", "")),
			text=row.get("text", ""),
		)
		if msg.related_event_id:
			bundle.messages_by_event[msg.related_event_id].append(msg)
		if msg.request_id:
			bundle.messages_by_request[msg.request_id].append(msg)
		if msg.user_id:
			bundle.messages_by_user[msg.user_id].append(msg)

	for row in _read_csv(dataset_dir / "images.csv"):
		img = ImageRef(
			image_id=row["image_id"],
			user_id=row.get("user_id") or None,
			request_id=row.get("request_id") or None,
			related_event_id=row.get("related_event_id") or None,
			path=str(bundle.media_dir / f"{row['image_id']}.png"),
		)
		if img.related_event_id:
			bundle.images_by_event[img.related_event_id].append(img)
		if img.request_id:
			bundle.images_by_request[img.request_id].append(img)

	for row in _read_csv(dataset_dir / "requests.csv"):
		req = Request(
			request_id=row["request_id"],
			user_id=row["user_id"],
			request_date=_parse_date(row["request_date"]),
			request_type=row["request_type"],
			requested_amount=float(row["requested_amount"]),
			desired_completion_date=_parse_date(row.get("desired_completion_date", "")),
			allows_partial_payment=_parse_bool(row.get("allows_partial_payment", "")),
			request_text=row.get("request_text", ""),
		)
		bundle.requests[req.request_id] = req

	_resolve_blank_amounts_from_images(bundle)
	return bundle


def _resolve_blank_amounts_from_images(bundle: DataBundle) -> None:
	"""Per problem_statement.md: a blank event.amount must be resolved via
	the linked image, never treated as zero. Vision extraction itself is
	pluggable -- wire in an OCR/vision call in extract_amount_from_image().
	"""
	for event in bundle.events.values():
		if event.amount is not None:
			continue
		images = bundle.images_by_event.get(event.event_id, [])
		if not images:
			# No image to resolve from -- flag loudly rather than silently
			# defaulting to 0, per the spec's explicit warning.
			event.raw["_unresolved_amount"] = True
			continue
		event.amount = extract_amount_from_image(images[0].path)


def extract_amount_from_image(image_path: str) -> float | None:
	"""Stub: plug in OCR/vision-model extraction here.

	Should return the numeric amount found in the receipt/screenshot, in
	whatever currency the image shows (currency conversion happens later
	in fx.py, not here).
	"""
	raise NotImplementedError(
		f"Wire up vision/OCR extraction for {image_path}"
	)
