"""Write output.csv and run the pre-submission validation checklist."""

from __future__ import annotations

import csv
from pathlib import Path

from models import (
	ALLOWED_AFFORDABILITY_STATUS,
	ALLOWED_PAYMENT_METHOD,
	OUTPUT_COLUMNS,
	OutputRow,
)


def write_output(rows: list[OutputRow], path: str | Path) -> None:
	path = Path(path)
	with path.open("w", newline="", encoding="utf-8") as f:
		writer = csv.writer(f)
		writer.writerow(OUTPUT_COLUMNS)
		for row in rows:
			writer.writerow(row.as_row())


def validate(rows: list[OutputRow], expected_request_ids: set[str]) -> list[str]:
	"""Return a list of human-readable problems; empty list means clean."""
	problems: list[str] = []

	seen_ids = {r.request_id for r in rows}
	missing = expected_request_ids - seen_ids
	extra = seen_ids - expected_request_ids
	if missing:
		problems.append(f"Missing output rows for request_ids: {sorted(missing)}")
	if extra:
		problems.append(f"Unexpected request_ids in output: {sorted(extra)}")
	if len(rows) != len(seen_ids):
		problems.append("Duplicate request_id rows detected in output.")

	for row in rows:
		if row.affordability_status not in ALLOWED_AFFORDABILITY_STATUS:
			problems.append(f"{row.request_id}: invalid affordability_status '{row.affordability_status}'")
		if row.recommended_payment_method not in ALLOWED_PAYMENT_METHOD:
			problems.append(f"{row.request_id}: invalid recommended_payment_method '{row.recommended_payment_method}'")
		if row.amount_safe_to_pay < 0:
			problems.append(f"{row.request_id}: amount_safe_to_pay is negative")
		if row.affordability_status == "affordable_now" and row.recommended_payment_method != "full_payment":
			problems.append(f"{row.request_id}: affordable_now must pair with full_payment")
		if row.recommended_payment_method == "partial_payment":
			if row.affordability_status != "affordable_with_plan":
				problems.append(f"{row.request_id}: partial_payment must have affordability_status affordable_with_plan")
			parts = row.payment_plan.split("|") if row.payment_plan != "none" else []
			if len(parts) != 2:
				problems.append(f"{row.request_id}: partial_payment plan must have exactly 2 payments")
		changes = row.spending_changes_needed.split("|") if row.spending_changes_needed != "none" else []
		if len(changes) > 3:
			problems.append(f"{row.request_id}: more than 3 spending_changes_needed entries")
		stop_events = {c.split(":")[1] for c in changes if c.startswith("stop:")}
		reduce_events = {c.split(":")[1] for c in changes if c.startswith("reduce_to:")}
		overlap = stop_events & reduce_events
		if overlap:
			problems.append(f"{row.request_id}: stop and reduce_to reference the same event(s) {overlap}")

	return problems
