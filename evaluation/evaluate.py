#!/usr/bin/env python3
"""Score the pipeline's predictions against dataset/sample_requests.csv (the
25 publicly solved examples). Not used as training labels — only to sanity
check the deterministic engine's decision style before the full run.

Usage:
    python3 evaluation/evaluate.py
"""
from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from buyorwait.evidence import build_evidence  # noqa: E402
from buyorwait.fx import FxTable  # noqa: E402
from buyorwait.io_data import Request, load_dataset  # noqa: E402
from buyorwait.llm_client import LLMClient  # noqa: E402
from buyorwait.pipeline import process_request  # noqa: E402


def _dec(s):
    s = (s or "").strip()
    return Decimal(s) if s else None


def main() -> int:
    dataset_dir = REPO_ROOT / "dataset"
    cache_path = REPO_ROOT / "code" / "cache" / "evidence_cache.json"
    dataset = load_dataset(dataset_dir)
    fx = FxTable(dataset.rates)
    evidence = build_evidence(dataset, dataset_dir, cache_path, llm_client=LLMClient())

    samples = list(csv.DictReader(open(dataset_dir / "sample_requests.csv", encoding="utf-8")))

    field_matches = {
        "amount_safe_to_pay": 0, "affordability_status": 0,
        "recommended_payment_method": 0, "payment_plan": 0,
        "earliest_date_for_full_payment": 0, "spending_changes_needed": 0,
    }
    total = 0
    mismatches = []

    for sample in samples:
        request = Request(
            request_id=sample["request_id"], user_id=sample["user_id"],
            request_date=dataset.requests[0].request_date,  # placeholder, replaced below
            request_type=sample["request_type"],
            requested_amount=_dec(sample["requested_amount"]),
            desired_completion_date=None,
            allows_partial_payment=sample["allows_partial_payment"].lower() == "true",
            request_text=sample["request_text"],
        )
        import datetime as dt
        request.request_date = dt.date.fromisoformat(sample["request_date"])
        request.desired_completion_date = dt.date.fromisoformat(sample["desired_completion_date"])

        got = process_request(dataset, request, fx, evidence)
        total += 1
        row_mismatches = {}

        got_amt = Decimal(got["amount_safe_to_pay"])
        want_amt = _dec(sample["amount_safe_to_pay"])
        if want_amt is not None and got_amt == want_amt:
            field_matches["amount_safe_to_pay"] += 1
        else:
            row_mismatches["amount_safe_to_pay"] = (got["amount_safe_to_pay"], sample["amount_safe_to_pay"])

        for field in ["affordability_status", "recommended_payment_method", "payment_plan",
                      "earliest_date_for_full_payment", "spending_changes_needed"]:
            if got[field] == sample[field]:
                field_matches[field] += 1
            else:
                row_mismatches[field] = (got[field], sample[field])

        if row_mismatches:
            mismatches.append((sample["request_id"], row_mismatches))

    print(f"Scored {total} sample requests\n")
    for field, count in field_matches.items():
        print(f"  {field}: {count}/{total} ({100*count/total:.0f}%)")

    print(f"\n{len(mismatches)} request(s) with at least one field mismatch:\n")
    for rid, diffs in mismatches:
        print(f"- {rid}:")
        for field, (got, want) in diffs.items():
            print(f"    {field}: got={got!r}  want={want!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
