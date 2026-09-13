"""Headroom-ai powered token accounting and context optimization.

Every request consumes a financial context (profile, events, messages,
payment options, request text) and produces an output row.  This module
measures both with headroom's TiktokenCounter (o200k_base) and shows
the headroom SmartCrusher lossless-crush savings for the events payload
(columnar-table strategy).  The pipeline itself is deterministic (regex
message parsing + tesseract OCR); no LLM calls are made, so the
estimated cost of the run is zero unless an LLM provider is configured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from headroom import SmartCrusher
from headroom.tokenizers.tiktoken_counter import TiktokenCounter

ACCOUNTING_TOKENIZER = "headroom TiktokenCounter (o200k_base, gpt-4o-mini vocab)"
PIPELINE_PROVIDER = "none (deterministic: regex message parser + tesseract 5.5.0 OCR)"

_PRICE_PER_1M = {"input": 0.15, "output": 0.60}  # gpt-4o-mini, only if LLM were used


@dataclass
class RequestUsage:
    request_id: str
    input_tokens: int
    output_tokens: int
    crushed_tokens: int
    savings_pct: float
    provider: str = PIPELINE_PROVIDER
    model_calls: int = 0


@dataclass
class UsageTracker:
    counter: TiktokenCounter = field(default_factory=lambda: TiktokenCounter(model="gpt-4o-mini"))
    crusher: SmartCrusher = field(default_factory=SmartCrusher)
    rows: list = field(default_factory=list)

    def record(self, request_id: str, context_items: list, output_row) -> RequestUsage:
        """Count context tokens for one request (crushed with SmartCrusher)."""
        payload = json.dumps(context_items, default=str, sort_keys=True)
        result = self.crusher.crush(payload)
        comp = result.compressed if isinstance(result.compressed, str) else \
            json.dumps(result.compressed, default=str)
        orig_tokens = self.counter.count_text(payload)
        crushed = self.counter.count_text(comp) if result.was_modified else orig_tokens
        out_text = json.dumps(output_row, default=str, sort_keys=True)
        usage = RequestUsage(
            request_id=request_id,
            input_tokens=orig_tokens,
            output_tokens=self.counter.count_text(out_text),
            crushed_tokens=crushed,
            savings_pct=100.0 * (1.0 - crushed / orig_tokens) if orig_tokens else 0.0,
        )
        self.rows.append(usage)
        return usage

    def totals(self) -> dict:
        n = len(self.rows)
        return {
            "requests": n,
            "input_tokens": sum(r.input_tokens for r in self.rows),
            "output_tokens": sum(r.output_tokens for r in self.rows),
            "crushed_tokens": sum(r.crushed_tokens for r in self.rows),
            "model_calls": sum(r.model_calls for r in self.rows),
            "avg_input": (sum(r.input_tokens for r in self.rows) / n) if n else 0,
            "avg_output": (sum(r.output_tokens for r in self.rows) / n) if n else 0,
            "avg_savings_pct": (sum(r.savings_pct for r in self.rows) / n) if n else 0,
        }

    def cost_estimate(self) -> dict:
        t = self.totals()
        return {
            "input": t["input_tokens"] / 1e6 * _PRICE_PER_1M["input"],
            "output": t["output_tokens"] / 1e6 * _PRICE_PER_1M["output"],
    def write_report(self, path) -> None:
        """Write evaluation/usage_report.md per the §6.5 contract."""
        t = self.totals()
        est = self.cost_estimate()
        lines = [
            "# Token Usage Report (final full-dataset run)",
            "",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            "",
            "## Model providers and names",
            f"- Inference provider: {PIPELINE_PROVIDER}",
            "- Accounting tokenizer: " + ACCOUNTING_TOKENIZER,
            "- Optional LLM verification layer: not configured (no API keys present)",
            "",
            "## Model calls",
            f"- LLM/OCR-vision model calls: **{t['model_calls']}** "
            "(the run is fully deterministic and offline)",
            "",
            "## Tokens (measured with headroom TiktokenCounter, o200k_base)",
            f"- Requests processed: {t['requests']}",
            f"- Input (context) tokens, total: {t['input_tokens']:,}",
            f"- Output (decision row) tokens, total: {t['output_tokens']:,}",
            f"- Average input tokens per request: {t['avg_input']:.1f}",
            f"- Average output tokens per request: {t['avg_output']:.1f}",
            "",
            "## Headroom context optimization",
            f"- Average SmartCrusher lossless-crush savings on the events payload: "
            f"{t['avg_savings_pct']:.1f}% "
            f"({t['input_tokens']:,} -> {t['crushed_tokens']:,} tokens)",
            "",
            "## Estimated cost",
            f"- Total: $0.00 (no model calls; deterministic pipeline)",
            f"- Per request: $0.00",
            f"- Hypothetical cost at gpt-4o-mini list prices if every request context were "
            f"an LLM call: ${est['input'] + est['output']:.2f} "
            f"(input ${est['input']:.2f} / output ${est['output']:.2f})",
            "",
            "## Per-request detail (top 20 by input tokens)",
            "",
            "| request_id | input | crushed | savings% | output |",
            "|---|---:|---:|---:|---:|",
        ]
        for r in sorted(self.rows, key=lambda r: -r.input_tokens)[:20]:
            lines.append(
                f"| {r.request_id} | {r.input_tokens:,} | {r.crushed_tokens:,} "
                f"| {r.savings_pct:.1f} | {r.output_tokens:,} |")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

