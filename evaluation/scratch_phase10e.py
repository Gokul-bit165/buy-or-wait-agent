#!/usr/bin/env python3
"""Phase 10 Part 5: Systematic experiment - test ALL requests with 
candidate fixes to see which changes improve aggregate score."""
import csv
import sys
import datetime as dt
from decimal import Decimal
from pathlib import Path
from collections import defaultdict
import statistics
from copy import deepcopy

REPO_ROOT = Path(r"c:\Users\gokul\hackerrank-orchestrate-september26")
sys.path.insert(0, str(REPO_ROOT / "code"))

from buyorwait.evidence import build_evidence
from buyorwait.fx import FxTable
from buyorwait.io_data import load_dataset
from buyorwait.llm_client import LLMClient
from buyorwait.state import build_financial_state, RecurringStream, FinancialState, _add_months
from buyorwait.forecast import run_forecast, HORIZON_DAYS

dataset_dir = REPO_ROOT / "dataset"
cache_path = REPO_ROOT / "code" / "cache" / "evidence_cache.json"
dataset = load_dataset(dataset_dir)
fx = FxTable(dataset.rates)
evidence = build_evidence(dataset, dataset_dir, cache_path, llm_client=LLMClient())
samples = list(csv.DictReader(open(dataset_dir / "sample_requests.csv", encoding="utf-8")))

sample_map = {s["request_id"]: s for s in samples}


def score_safe_amounts(predictions):
    """Score: fraction of requests where our safe amount matches ref within 1%"""
    total = 0
    correct = 0
    for rid, our_safe in predictions.items():
        ref_safe = Decimal(sample_map[rid]["amount_safe_to_pay"])
        total += 1
        if ref_safe == 0:
            if our_safe == 0:
                correct += 1
        else:
            pct_err = abs(our_safe - ref_safe) / ref_safe
            if pct_err <= Decimal("0.01"):
                correct += 1
    return correct, total


def baseline_predictions():
    """Current production predictions."""
    preds = {}
    for s in samples:
        rid = s["request_id"]
        uid = s["user_id"]
        rdate = dt.date.fromisoformat(s["request_date"])
        req_amt = Decimal(s["requested_amount"])
        state = build_financial_state(dataset, uid, rdate, fx, evidence)
        base = run_forecast(state, rdate, req_amt)
        preds[rid] = base.amount_safe_to_pay
    return preds

# ── EXPERIMENT 1: Use conservative (lower) income amounts ────────────────
def experiment_conservative_income():
    """Use minimum of last 3 salary amounts instead of latest."""
    preds = {}
    for s in samples:
        rid = s["request_id"]
        uid = s["user_id"]
        rdate = dt.date.fromisoformat(s["request_date"])
        req_amt = Decimal(s["requested_amount"])
        state = build_financial_state(dataset, uid, rdate, fx, evidence)
        
        # Modify: for each income stream, use min of last 3 instead of latest
        for cat, slist in state.streams.items():
            for stream in slist:
                if stream.direction == "credit":
                    # Get raw history for this category
                    raw = dataset.events_by_user.get(uid, [])
                    amounts = []
                    for e in raw:
                        if e.category == cat and e.direction == "credit" and e.status not in ("cancelled", "failed", "unrealized"):
                            d = e.settlement_date or e.event_date
                            if d:
                                amt = fx.convert(e.amount, e.currency, state.profile.home_currency, d) if e.amount else None
                                if amt:
                                    amounts.append(amt)
                    if len(amounts) >= 3:
                        stream.amount = min(amounts[-3:])
        
        base = run_forecast(state, rdate, req_amt)
        preds[rid] = base.amount_safe_to_pay
    return preds


# ── EXPERIMENT 2: Use median for income (like expenses) ──────────────────
def experiment_median_income():
    """Use median of last 3 salary amounts instead of latest."""
    preds = {}
    for s in samples:
        rid = s["request_id"]
        uid = s["user_id"]
        rdate = dt.date.fromisoformat(s["request_date"])
        req_amt = Decimal(s["requested_amount"])
        state = build_financial_state(dataset, uid, rdate, fx, evidence)
        
        for cat, slist in state.streams.items():
            for stream in slist:
                if stream.direction == "credit":
                    raw = dataset.events_by_user.get(uid, [])
                    amounts = []
                    for e in raw:
                        if e.category == cat and e.direction == "credit" and e.status not in ("cancelled", "failed", "unrealized"):
                            d = e.settlement_date or e.event_date
                            if d and e.amount:
                                amt = fx.convert(e.amount, e.currency, state.profile.home_currency, d)
                                amounts.append(amt)
                    if len(amounts) >= 3:
                        stream.amount = statistics.median(amounts[-3:])
        
        base = run_forecast(state, rdate, req_amt)
        preds[rid] = base.amount_safe_to_pay
    return preds


# ── EXPERIMENT 3: Cap sub-monthly frequencies at monthly ──────────────────
def experiment_cap_at_monthly():
    """For expenses with interval < 30, cap projected count at monthly equivalent."""
    preds = {}
    for s in samples:
        rid = s["request_id"]
        uid = s["user_id"]
        rdate = dt.date.fromisoformat(s["request_date"])
        req_amt = Decimal(s["requested_amount"])
        state = build_financial_state(dataset, uid, rdate, fx, evidence)
        
        # For sub-monthly expenses, replace with monthly total
        for cat, slist in state.streams.items():
            for i, stream in enumerate(slist):
                if stream.direction == "debit" and stream.interval_days < 27:
                    monthly_count = round(30 / stream.interval_days)
                    monthly_total = stream.amount * Decimal(monthly_count)
                    slist[i] = RecurringStream(
                        category=stream.category,
                        direction="debit",
                        amount=monthly_total,
                        interval_days=30,
                        monthly=True,
                        next_date=stream.next_date,
                        anchor_event_id=stream.anchor_event_id,
                        flexibility=stream.flexibility,
                        minimum_allowed_amount=stream.minimum_allowed_amount,
                        end_date=stream.end_date,
                    )
        
        base = run_forecast(state, rdate, req_amt)
        preds[rid] = base.amount_safe_to_pay
    return preds


# ── EXPERIMENT 4: Use max of last 3 for expenses ──────────────────────────
def experiment_max_expenses():
    """Use max of last 3 expense amounts (more conservative)."""
    preds = {}
    for s in samples:
        rid = s["request_id"]
        uid = s["user_id"]
        rdate = dt.date.fromisoformat(s["request_date"])
        req_amt = Decimal(s["requested_amount"])
        state = build_financial_state(dataset, uid, rdate, fx, evidence)
        
        for cat, slist in state.streams.items():
            for stream in slist:
                if stream.direction == "debit":
                    raw = dataset.events_by_user.get(uid, [])
                    amounts = []
                    for e in raw:
                        if e.category == cat and e.direction == "debit" and e.status not in ("cancelled", "failed", "unrealized"):
                            d = e.settlement_date or e.event_date
                            if d and e.amount:
                                amt = fx.convert(e.amount, e.currency, state.profile.home_currency, d)
                                amounts.append(amt)
                    if len(amounts) >= 3:
                        stream.amount = max(amounts[-3:])
        
        base = run_forecast(state, rdate, req_amt)
        preds[rid] = base.amount_safe_to_pay
    return preds


# ── RUN ALL EXPERIMENTS ──────────────────────────────────────────────────

print("Computing baseline...")
bl = baseline_predictions()
bl_score = score_safe_amounts(bl)
print(f"Baseline: {bl_score[0]}/{bl_score[1]}")

experiments = {
    "conservative_income (min last 3)": experiment_conservative_income,
    "median_income (median last 3)": experiment_median_income,
    "cap_at_monthly": experiment_cap_at_monthly,
    "max_expenses (max last 3)": experiment_max_expenses,
}

for name, func in experiments.items():
    print(f"\nComputing {name}...")
    preds = func()
    score = score_safe_amounts(preds)
    
    # Detail for each request
    improved = []
    regressed = []
    for rid in sorted(preds.keys()):
        ref = Decimal(sample_map[rid]["amount_safe_to_pay"])
        bl_err = abs(bl[rid] - ref)
        new_err = abs(preds[rid] - ref)
        if new_err < bl_err - Decimal("1"):
            improved.append(f"  + {rid}: {bl[rid]:.2f} -> {preds[rid]:.2f} (ref={ref:.2f})")
        elif new_err > bl_err + Decimal("1"):
            regressed.append(f"  - {rid}: {bl[rid]:.2f} -> {preds[rid]:.2f} (ref={ref:.2f})")
    
    print(f"  Score: {score[0]}/{score[1]} (baseline: {bl_score[0]}/{bl_score[1]})")
    if improved:
        print("  Improvements:")
        for line in improved:
            print(line)
    if regressed:
        print("  Regressions:")
        for line in regressed[:5]:
            print(line)
        if len(regressed) > 5:
            print(f"    ... and {len(regressed)-5} more regressions")
