"""Buy or Wait? pipeline runner.

Usage:
  python3 code/main.py                       # full 250-request run -> output.csv
  python3 code/main.py --sample              # calibrate vs dataset/sample_requests.csv
  python3 code/main.py --limit 50            # partial run (smoke test)

Reads dataset/, writes output.csv (exact 8 columns) plus the headroom
token-usage report for the run.  Deterministic; no LLM calls.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from loaders import load_all
from fx import RateTable
from context import build_user_context
from forecast import amount_safe_to_pay, earliest_date_for_full_payment
from decision import select_plan, build_explanation, build_output_row, _expand_installment
from output import validate_output_row, write_output_csv
from usage import UsageTracker
from models import fmt_amount

COMPARE_FIELDS = ("amount_safe_to_pay", "affordability_status",
                  "recommended_payment_method", "payment_plan",
                  "earliest_date_for_full_payment", "spending_changes_needed")


def decide_request(req, bundle, rates):
    ctx = build_user_context(req, bundle, rates)
    options = bundle.payment_options.get(req.request_id, [])
    safe = amount_safe_to_pay(ctx, req.request_date, req.requested_amount)
    full_date = earliest_date_for_full_payment(ctx, req.request_date, req.requested_amount)
    chosen = select_plan(req, ctx, options, safe, full_date)
    explanation = build_explanation(req, ctx, chosen, safe)
    row = build_output_row(req, ctx, safe, full_date, chosen, explanation)

    # installment plans must follow a supplied option exactly
    if chosen is not None and chosen.method == "installments":
        opt = next((o for o in options if o.payment_option_id == chosen.payment_option_id), None)
        if opt is not None:
            sched = _expand_installment(opt)
            want = [(d.isoformat(), fmt_amount(a)) for d, a in sched]
            got = [(d.isoformat(), fmt_amount(a)) for d, a in chosen.payments]
            if want != got:
                row.payment_plan = "|".join(f"{d}:{a}" for d, a in want)
    return ctx, chosen, row


def run(requests_csv, output_path, limit=None, tracker=None):
    bundle = load_all("dataset")
    rates = RateTable(bundle.rates)
    with open(requests_csv, newline="", encoding="utf-8") as fh:
        ids = [r["request_id"] for r in csv.DictReader(fh)]
    reqs = [bundle.requests[i] for i in ids if i in bundle.requests]
    if limit:
        reqs = reqs[:limit]

    rows, all_errors = [], []
    t0 = time.time()
    for i, req in enumerate(reqs):
        ctx, chosen, row = decide_request(req, bundle, rates)
        options = bundle.payment_options.get(req.request_id, [])
        errors = validate_output_row(row, req, options, ctx)
        if chosen is not None and chosen.method == "installments":
            opt = next((o for o in options
                        if o.payment_option_id == chosen.payment_option_id), None)
            if opt is not None:
                sched = [(d.isoformat(), fmt_amount(a))
                         for d, a in _expand_installment(opt)]
                if row.payment_plan != "|".join(f"{d}:{a}" for d, a in sched):
                    errors.append("installment plan != supplied option schedule")
        if errors:
            all_errors.append((req.request_id, errors))
        if tracker is not None:
            items = [vars(req),
                     {"balance": ctx.available_balance, "floor": ctx.minimum_balance_to_keep},
                     [vars(e) for e in ctx.events],
                     [vars(o) for o in options],
                     [vars(m) for m in bundle.messages_by_user.get(req.user_id, [])]]
            tracker.record(req.request_id, items, row.as_row())
        rows.append(row)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(reqs)} decided ({time.time() - t0:.0f}s)", flush=True)

    write_output_csv(rows, output_path)
    print(f"wrote {len(rows)} rows -> {output_path}")
    if all_errors:
        print(f"VALIDATION ERRORS in {len(all_errors)}/{len(rows)} rows:")
        for rid, errs in all_errors[:20]:
            print(f"  {rid}: {errs}")
    else:
        print("validation: all rows clean")
    return rows, all_errors

# __PART2__


def calibrate(sample_csv):
    """Run the pipeline on the 25 sample scenarios and diff vs ground truth."""
    bundle = load_all("dataset")
    rates = RateTable(bundle.rates)
    truth = {r["request_id"]: r for r in csv.DictReader(open(sample_csv, encoding="utf-8"))}

    field_hits = {f: 0 for f in COMPARE_FIELDS}
    n = 0
    mismatches = []
    for rid in sorted(truth):
        t = truth[rid]
        req = bundle.requests.get(rid)
        if req is None:
            mismatches.append((rid, ["<missing from requests.csv>"]))
            continue
        _, _, row = decide_request(req, bundle, rates)
        got = {
            "amount_safe_to_pay": fmt_amount(row.amount_safe_to_pay),
            "affordability_status": row.affordability_status,
            "recommended_payment_method": row.recommended_payment_method,
            "payment_plan": row.payment_plan,
            "earliest_date_for_full_payment": row.earliest_date_for_full_payment,
            "spending_changes_needed": row.spending_changes_needed,
        }
        n += 1
        for f in COMPARE_FIELDS:
            want = (t[f] or "").strip()
            g = (got[f] or "").strip()
            if f == "amount_safe_to_pay":
                try:
                    ok = abs(float(g) - float(want)) < 0.005
                except ValueError:
                    ok = g == want
            elif f == "spending_changes_needed":
                ok = sorted(g.split("|")) == sorted(want.split("|"))
            else:
                ok = g == want
            if ok:
                field_hits[f] += 1
            else:
                for m in mismatches:
                    if m[0] == rid:
                        break
                else:
                    mismatches.append((rid, []))
                for m in mismatches:
                    if m[0] == rid:
                        m[1].append(f"{f}: want={want!r} got={g!r}")
    print(f"CALIBRATION vs {sample_csv} ({n} samples)")
    for f in COMPARE_FIELDS:
        print(f"  {f:<32} {field_hits[f]}/{n}")
    for rid, errs in mismatches:
        if errs:
            print(f"  {rid}:")
            for e in errs:
                print(f"     {e}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Buy or Wait? decision pipeline")
    ap.add_argument("--requests", default="dataset/requests.csv")
    ap.add_argument("--output", default="output.csv")
    ap.add_argument("--usage-report", default="evaluation/usage_report.md")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sample", action="store_true",
                    help="calibrate against dataset/sample_requests.csv")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)

    if args.sample:
        calibrate("dataset/sample_requests.csv")
        return

    tracker = None if args.no_report else UsageTracker()
    rows, _ = run(args.requests, args.output, args.limit, tracker)
    if tracker is not None:
        tracker.write_report(args.usage_report)
        print(f"usage report -> {args.usage_report}")


if __name__ == "__main__":
    main()
