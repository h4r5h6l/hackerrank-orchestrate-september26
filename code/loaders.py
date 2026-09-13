"""Read every dataset CSV into the typed model.

Pure I/O: no currency conversion (fx.py) and no message-fact parsing
(evidence.py) live here.  The one exception is blank event amounts,
which are resolved from the linked evidence image via ocr.py at load
time, because everything downstream expects concrete amounts.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import ocr
from models import (
    ExchangeRate,
    FinancialEvent,
    FinancialProfile,
    ImageRef,
    Message,
    PaymentOption,
    Request,
)


def _read_csv(path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_dt(value):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _parse_float(value):
    value = (value or "").strip()
    if not value:
        return None
    return float(value)


def _parse_int(value):
    value = (value or "").strip()
    if not value:
        return None
    return int(value)


def _parse_list(value, sep="|") -> list:
    value = (value or "").strip()
    if not value:
        return []
    return [v.strip() for v in value.split(sep) if v.strip()]


@dataclass
class DataBundle:
    requests: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    events_by_user: dict = field(default_factory=lambda: defaultdict(list))
    rates: list = field(default_factory=list)
    payment_options: dict = field(default_factory=lambda: defaultdict(list))
    messages_by_user: dict = field(default_factory=lambda: defaultdict(list))
    messages_by_request: dict = field(default_factory=lambda: defaultdict(list))
    messages_by_event: dict = field(default_factory=lambda: defaultdict(list))
    images_by_user: dict = field(default_factory=lambda: defaultdict(list))
    images_by_request: dict = field(default_factory=lambda: defaultdict(list))
    images_by_event: dict = field(default_factory=lambda: defaultdict(list))
    media_dir: Path = field(default_factory=lambda: Path("dataset/media/images"))
    # event_id -> (image_id, resolved_amount_or_None, provenance)
    blank_amount_resolution: dict = field(default_factory=dict)


def load_all(dataset_dir) -> DataBundle:
    dataset_dir = Path(dataset_dir)
    bundle = DataBundle(media_dir=dataset_dir / "media" / "images")

    # --- financial profiles -------------------------------------------------
    for row in _read_csv(dataset_dir / "financial_profiles.csv"):
        profile = FinancialProfile(
            user_id=row["user_id"],
            home_currency=row["home_currency"],
            current_available_balance=_parse_float(row["current_available_balance"]) or 0.0,
            minimum_balance_to_keep=_parse_float(row["minimum_balance_to_keep"]) or 0.0,
            financial_priorities=row.get("financial_priorities", ""),
            protected_categories=set(_parse_list(row.get("expense_categories_to_protect", ""))),
            reduce_categories=set(_parse_list(
                row.get("expense_categories_user_is_willing_to_reduce", ""))),
            stop_categories=set(_parse_list(
                row.get("expense_categories_user_is_willing_to_stop", ""))),
            payment_methods_user_will_consider=_parse_list(
                row.get("payment_methods_user_will_consider", "")),
            max_installment_months=_parse_int(row.get("max_installment_months", "")),
        )
        bundle.profiles[profile.user_id] = profile

    # --- financial events ------------------------------------------------------
    for row in _read_csv(dataset_dir / "financial_events.csv"):
        event = FinancialEvent(
            event_id=row["event_id"],
            user_id=row["user_id"],
            event_type=row.get("event_type", ""),
            description=row.get("description", ""),
            category=row.get("category", ""),
            direction=row.get("direction", ""),
            amount=_parse_float(row.get("amount", "")),
            currency=row.get("currency", ""),
            event_date=_parse_date(row.get("event_date", "")),
            settlement_date=_parse_date(row.get("settlement_date", "")),
            status=row.get("status", ""),
            linked_event_id=row.get("linked_event_id") or None,
            flexibility=row.get("flexibility", "") or "fixed",
            minimum_allowed_amount=_parse_float(row.get("minimum_allowed_amount", "")),
            raw=dict(row),
        )
        bundle.events[event.event_id] = event
        bundle.events_by_user[event.user_id].append(event)

    # --- exchange rates -----------------------------------------------------
    for row in _read_csv(dataset_dir / "exchange_rates.csv"):
        bundle.rates.append(ExchangeRate(
            rate_date=_parse_date(row["rate_date"]),
            from_currency=row["from_currency"],
            to_currency=row["to_currency"],
            rate=float(row["rate"]),
        ))
    bundle.rates.sort(key=lambda r: (r.rate_date, r.from_currency, r.to_currency))

    # --- payment options --------------------------------------------------------
    for row in _read_csv(dataset_dir / "request_payment_options.csv"):
        opt = PaymentOption(
            payment_option_id=row["payment_option_id"],
            request_id=row["request_id"],
            payment_method=row.get("payment_method", ""),
            payment_amount=_parse_float(row.get("payment_amount", "")),
            number_of_payments=int(row.get("number_of_payments") or 1),
            first_payment_date=_parse_date(row.get("first_payment_date", "")),
            payment_frequency_days=_parse_int(row.get("payment_frequency_days", "")),
            financing_fee=_parse_float(row.get("financing_fee", "")) or 0.0,
            total_payable_amount=_parse_float(row.get("total_payable_amount", "")),
        )
        bundle.payment_options[opt.request_id].append(opt)

    # --- messages ------------------------------------------------------------
    for row in _read_csv(dataset_dir / "messages.csv"):
        msg = Message(
            message_id=row["message_id"],
            user_id=row.get("user_id") or None,
            request_id=row.get("request_id") or None,
            related_event_id=row.get("related_event_id") or None,
            sent_at=_parse_dt(row.get("sent_at", "")),
            source_type=row.get("source_type", ""),
            text=row.get("message_text", ""),
        )
        if msg.user_id:
            bundle.messages_by_user[msg.user_id].append(msg)
        if msg.request_id:
            bundle.messages_by_request[msg.request_id].append(msg)
        if msg.related_event_id:
            bundle.messages_by_event[msg.related_event_id].append(msg)

    # --- images ------------------------------------------------------------
    for row in _read_csv(dataset_dir / "images.csv"):
        img = ImageRef(
            image_id=row["image_id"],
            user_id=row.get("user_id") or None,
            request_id=row.get("request_id") or None,
            related_event_id=row.get("related_event_id") or None,
            path=str(bundle.media_dir / f"{row['image_id']}.png"),
        )
        if img.user_id:
            bundle.images_by_user[img.user_id].append(img)
        if img.request_id:
            bundle.images_by_request[img.request_id].append(img)
        if img.related_event_id:
            bundle.images_by_event[img.related_event_id].append(img)

    # --- requests ------------------------------------------------------------
    for row in _read_csv(dataset_dir / "requests.csv"):
        req = Request(
            request_id=row["request_id"],
            user_id=row["user_id"],
            request_date=_parse_date(row["request_date"]),
            request_type=row.get("request_type", ""),
            requested_amount=float(row["requested_amount"]),
            desired_completion_date=_parse_date(row.get("desired_completion_date", "")),
            allows_partial_payment=_parse_bool(row.get("allows_partial_payment", "")),
            request_text=row.get("request_text", ""),
        )
        bundle.requests[req.request_id] = req

    _resolve_blank_amounts(bundle)
    return bundle


def _resolve_blank_amounts(bundle: DataBundle) -> dict:
    """Fill blank event amounts from the linked evidence image via OCR.

    Each blank-amount event has exactly one linked image in images.csv.
    The image is read with the tesseract pipeline in ocr.py; the
    per-image verified pin (IMAGE_AMOUNTS in evidence.py) breaks
    ties/ambiguity so the run stays deterministic and offline-safe.
    """
    for eid, event in bundle.events.items():
        if event.amount is not None:
            continue
        images = bundle.images_by_event.get(eid, [])
        if not images:
            bundle.blank_amount_resolution[eid] = ("", None, "no-linked-image")
            continue
        img = images[0]
        amount, provenance = ocr.extract_image_amount(img.path, img.image_id)
        if amount is not None:
            event.amount = amount
        bundle.blank_amount_resolution[eid] = (img.image_id, amount, provenance)
    return bundle.blank_amount_resolution


