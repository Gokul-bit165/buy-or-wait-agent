#!/usr/bin/env python3
"""Buy or Wait? — entry point.

Usage:
    python3 code/main.py [--dataset-dir dataset] [--output output.csv]

Reads the dataset, produces one prediction per request, writes output.csv,
and refreshes evaluation/usage_report.md with the LLM usage of this run
(usage is typically zero calls, since the 16 blank-amount images and all
216 messages are covered by the pre-built evidence cache — see
docs/architecture.md, "On LLM/VLM use").
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buyorwait.evidence import build_evidence  # noqa: E402
from buyorwait.fx import FxTable  # noqa: E402
from buyorwait.io_data import load_dataset  # noqa: E402
from buyorwait.llm_client import LLMClient  # noqa: E402
from buyorwait.pipeline import process_request  # noqa: E402
from buyorwait.validate import validate_row  # noqa: E402

OUTPUT_COLUMNS = [
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment",
    "spending_changes_needed", "decision_explanation",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", default=str(REPO_ROOT / "dataset"))
    parser.add_argument("--output", default=str(REPO_ROOT / "output.csv"))
    parser.add_argument("--cache", default=str(Path(__file__).resolve().parent / "cache" / "evidence_cache.json"))
    parser.add_argument("--usage-report", default=str(REPO_ROOT / "evaluation" / "usage_report.md"))
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir)
    output_path = Path(args.output)
    cache_path = Path(args.cache)

    t0 = time.time()
    dataset = load_dataset(dataset_dir)
    fx = FxTable(dataset.rates)
    llm_client = LLMClient()
    evidence = build_evidence(dataset, dataset_dir, cache_path, llm_client=llm_client)

    rows = []
    warnings = []
    for request in dataset.requests:
        try:
            row = process_request(dataset, request, fx, evidence)
        except Exception as exc:  # pragma: no cover - defensive fallback
            row = {
                "request_id": request.request_id,
                "amount_safe_to_pay": "0",
                "affordability_status": "not_affordable",
                "recommended_payment_method": "not_recommended",
                "payment_plan": "none",
                "earliest_date_for_full_payment": "",
                "spending_changes_needed": "none",
                "decision_explanation": "Unable to compute a safe recommendation for this request.",
            }
            warnings.append(f"{request.request_id}: {exc!r}")
        errors = validate_row(row, request.requested_amount)
        if errors:
            warnings.append(f"{request.request_id}: {errors}")
        rows.append(row)

    seen_ids = set()
    for row in rows:
        if row["request_id"] in seen_ids:
            warnings.append(f"duplicate request_id {row['request_id']}")
        seen_ids.add(row["request_id"])
    expected_ids = {r.request_id for r in dataset.requests}
    if seen_ids != expected_ids:
        warnings.append("output request_id set does not match requests.csv")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    elapsed = time.time() - t0
    _write_usage_report(Path(args.usage_report), llm_client, len(dataset.requests), elapsed)

    print(f"Wrote {len(rows)} rows to {output_path}")
    if warnings:
        print(f"{len(warnings)} validation warning(s):")
        for w in warnings[:20]:
            print(f"  - {w}")
    return 0


def _write_usage_report(path: Path, llm_client: LLMClient, num_requests: int, elapsed: float) -> None:
    usage = llm_client.usage
    path.parent.mkdir(parents=True, exist_ok=True)
    avg_in = usage.input_tokens / usage.calls if usage.calls else 0
    avg_out = usage.output_tokens / usage.calls if usage.calls else 0
    cost = usage.estimated_cost()
    lines = [
        "# Usage Report — Buy or Wait?",
        "",
        f"Final full-dataset run: {num_requests} requests processed in {elapsed:.2f}s.",
        "",
        "## Model calls",
        "",
        f"- Provider: {usage.provider}",
        f"- Model: {usage.model}",
        f"- Calls made this run: {usage.calls}",
        f"- Total input tokens: {usage.input_tokens}",
        f"- Total output tokens: {usage.output_tokens}",
        f"- Total tokens: {usage.input_tokens + usage.output_tokens}",
        f"- Average input tokens / request: {avg_in:.2f}",
        f"- Average output tokens / request: {avg_out:.2f}",
        f"- Estimated total cost (USD): ${cost:.4f}",
        f"- Estimated cost per request (USD): ${(cost / num_requests if num_requests else 0):.6f}",
        "",
        "## Notes",
        "",
        "- The 16 blank-amount financial-event images and all 216 messages in "
        "the shipped dataset are covered by the pre-built extraction cache "
        "(`code/cache/evidence_cache.json`), produced once during development "
        "(see docs/architecture.md). A run against this exact dataset therefore "
        "makes 0 live model calls, which is why the totals above are 0 unless "
        "`ANTHROPIC_API_KEY` is set and the hidden evaluation set contains "
        "evidence outside the cache.",
        "- No model call ever computes a balance, payment amount, or date; "
        "model output is restricted to extracting a fact (an amount+currency "
        "from an image, or a structured fact from an unmatched message), which "
        "the deterministic engine in `code/buyorwait/{state,forecast,planner}.py` "
        "then consumes exactly like any other input row.",
        "- Pricing reference: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`), "
        "$1.00 / MTok input, $5.00 / MTok output.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
