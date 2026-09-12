"""Entry point: dataset/*.csv -> output.csv (repo root).

Run from the repo root:
    python code/main.py
"""

from __future__ import annotations

from pathlib import Path

from context import build_user_context
from decision import build_candidates, build_output_row, filter_eligible, rank_candidates
from forecast import amount_safe_to_pay, earliest_date_for_full_payment, is_safe
from loaders import load_all
from models import OutputRow
from output import validate, write_output

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "output.csv"


def process_request(req, bundle) -> OutputRow:
	ctx = build_user_context(req.user_id, bundle, as_of=req.request_date)

	safe_amount = amount_safe_to_pay(ctx, req.request_date, req.requested_amount)
	full_date = earliest_date_for_full_payment(
		ctx, req.request_date, req.requested_amount, req.desired_completion_date
	)

	options = bundle.payment_options.get(req.request_id, [])
	candidates = build_candidates(req, ctx, options, safe_amount, full_date)

	# Keep only candidates that are actually verified-safe against the
	# forecast (build_candidates only assembles shapes; safety must be
	# checked per-candidate here, especially for installments).
	safe_candidates = []
	for c in candidates:
		if is_safe(ctx, req.request_date, extra_payments=c.payments):
			safe_candidates.append(c)

	eligible = filter_eligible(safe_candidates, ctx.payment_methods_user_will_consider)
	ranked = rank_candidates(eligible, req)
	chosen = ranked[0] if ranked else None

	explanation = _build_explanation(req, ctx, safe_amount, full_date, chosen)
	return build_output_row(req, ctx, safe_amount, full_date, chosen, explanation)


def _build_explanation(req, ctx, safe_amount, full_date, chosen) -> str:
	# TODO: template a short, specific explanation referencing the actual
	# balance/commitments that drove the decision, e.g.:
	# "Balance after essentials and minimum buffer supports {safe_amount}
	#  today; full amount clears the 90-day safety check on {full_date}."
	if chosen is None:
		return "No eligible payment method keeps the balance above the minimum threshold within the forecast window."
	return f"Recommended {chosen.method} based on the 90-day balance forecast."


def main() -> None:
	bundle = load_all(DATASET_DIR)
	rows = [process_request(req, bundle) for req in bundle.requests.values()]

	write_output(rows, OUTPUT_PATH)

	problems = validate(rows, expected_request_ids=set(bundle.requests.keys()))
	if problems:
		print(f"[validate] {len(problems)} issue(s) found:")
		for p in problems:
			print(f"  - {p}")
	else:
		print(f"[validate] clean. Wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
	main()
