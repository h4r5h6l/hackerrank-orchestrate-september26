"""Output contract: row validation and CSV writing.

validate_output_row enforces the §6.2 submission contract for one row;
write_output_csv writes the final output file with the exact columns.
"""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta

from models import (ALLOWED_AFFORDABILITY_STATUS, ALLOWED_PAYMENT_METHOD,
                    OUTPUT_COLUMNS, fmt_amount, fmt_safe_amount)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_output_row(row, req, options=(), ctx=None) -> list:
    """Return a list of contract violations ([] when the row is valid)."""
    errors = []
    safe = row.amount_safe_to_pay

    if not (0 <= safe <= req.requested_amount + 1e-9):
        errors.append(f"amount_safe_to_pay {safe} outside [0, {req.requested_amount}]")
    if row.affordability_status not in ALLOWED_AFFORDABILITY_STATUS:
        errors.append(f"affordability_status {row.affordability_status!r} not allowed")
    if row.recommended_payment_method not in ALLOWED_PAYMENT_METHOD:
        errors.append(f"recommended_payment_method {row.recommended_payment_method!r} not allowed")

    plan = row.payment_plan
    if plan != "none":
        parts = plan.split("|")
        dates, amts = [], []
        for p in parts:
            if ":" not in p or not _DATE_RE.match(p.split(":", 1)[0]):
                errors.append(f"payment_plan entry {p!r} malformed")
                continue
            d, a = p.split(":", 1)
            dates.append(d)
            try:
                amts.append(float(a))
            except ValueError:
                errors.append(f"payment_plan amount {a!r} not numeric")
        if dates and dates != sorted(dates):
            errors.append("payment_plan not chronological")
        method = row.recommended_payment_method
        if method == "partial_payment":
            if len(parts) != 2:
                errors.append("partial_payment must have exactly 2 entries")
            else:
                if abs(sum(amts) - req.requested_amount) > 0.01:
                    errors.append(f"partial sum {sum(amts)} != requested {req.requested_amount}")
                if abs(amts[0] - safe) > 0.01:
                    errors.append("first partial payment != amount_safe_to_pay")
                if dates[0] != req.request_date.isoformat():
                    errors.append("first partial payment not on request_date")
                if req.desired_completion_date and dates[1] > req.desired_completion_date.isoformat():
                    errors.append("second partial payment after desired_completion_date")
        if method == "installments":
            if len(parts) < 2:
                errors.append("installments plan must have >= 2 payments")
        if method == "full_payment" and plan != "none":
            if len(parts) != 1 or abs(amts[0] - req.requested_amount) > 0.01:
                errors.append("full_payment plan must be one payment of requested_amount")
        if method == "wait" and plan != "none" and len(parts) != 1:
            errors.append("wait plan must be a single payment")
        if method == "not_recommended" and plan != "none":
            errors.append("not_recommended must have payment_plan none")

    # earliest-date rules
    if row.affordability_status == "affordable_now":
        if row.earliest_date_for_full_payment != req.request_date.isoformat():
            errors.append("affordable_now must have earliest == request_date")
    if row.affordability_status == "not_affordable":
        if row.earliest_date_for_full_payment not in ("",):
            errors.append("not_affordable must have empty earliest date "
                          "(no safe full payment within the forecast period)")

    # spending changes
    sc = row.spending_changes_needed
    if sc == "none":
        if row.recommended_payment_method == "full_payment" and \
                row.affordability_status == "affordable_with_plan":
            errors.append("affordable_with_plan full_payment needs spending changes")
    else:
        items = sc.split("|")
        if len(items) > 3:
            errors.append("more than 3 spending changes")
        for it in items:
            m = re.match(r"^(stop:(\S+))|(reduce_to:(\S+):[\d.]+)$", it)
            if not m:
                errors.append(f"bad spending-change token {it!r}")
                continue
            eid = m.group(2) or m.group(4)
            if ctx is not None:
                cat = None
                for s in ctx.recurring:
                    if s.event_id == eid:
                        cat = s.key[0]
                for e in ctx.explicit:
                    if e.event_id == eid:
                        cat = e.category
                if cat is None:
                    errors.append(f"{eid}: unknown spending-change target")
                elif cat in ctx.protected_categories:
                    errors.append(f"{eid}: protected category {cat}")
    return errors


def write_output_csv(rows, path) -> int:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(OUTPUT_COLUMNS)
        for row in rows:
            w.writerow(row.as_row())
    return len(rows)
