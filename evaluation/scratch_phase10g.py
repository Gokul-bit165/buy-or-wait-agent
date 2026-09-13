#!/usr/bin/env python3
"""Phase 10 Part 7b: Fixed percentile sweep (deep copies to avoid mutation)."""
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
from buyorwait.state import build_financial_state, RecurringStream, FinancialState
from buyorwait.forecast import run_forecast, HORIZON_DAYS

dataset_dir = REPO_ROOT / "dataset"
cache_path = REPO_ROOT / "code" / "cache" / "evidence_cache.json"
dataset = load_dataset(dataset_dir)
fx = FxTable(dataset.rates)
evidence = build_evidence(dataset, dataset_dir, cache_path, llm_client=LLMClient())
samples = list(csv.DictReader(open(dataset_dir / "sample_requests.csv", encoding="utf-8")))
sample_map = {s["request_id"]: s for s in samples}


def precompute_expense_amounts():
    """For each (user, category), precompute sorted chain amounts."""
    expense_amounts = {}
    for s in samples:
        uid = s["user_id"]
        rdate = dt.date.fromisoformat(s["request_date"])
        state = build_financial_state(dataset, uid, rdate, fx, evidence)
        home = state.profile.home_currency
        
        for cat, slist in state.streams.items():
            for stream in slist:
                if stream.direction == "debit":
                    raw = dataset.events_by_user.get(uid, [])
                    amounts = []
                    for e in raw:
                        if (e.category == cat and e.direction == "debit"
                            and e.status not in ("cancelled", "failed", "unrealized")
                            and not e.linked_event_id):
                            d = e.settlement_date or e.event_date
                            if d and e.amount:
                                amt = fx.convert(e.amount, e.currency, home, d)
                                amounts.append(float(amt))
                    amounts.sort()
                    expense_amounts[(s["request_id"], cat, stream.anchor_event_id)] = amounts
    return expense_amounts

expense_amounts = precompute_expense_amounts()


def run_with_percentile(p):
    """Run with Pth percentile for expenses. Rebuilds state from scratch."""
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
                    key = (rid, cat, stream.anchor_event_id)
                    amounts = expense_amounts.get(key, [])
                    if len(amounts) >= 3:
                        idx = int(len(amounts) * p / 100)
                        idx = min(idx, len(amounts) - 1)
                        stream.amount = Decimal(str(amounts[idx]))
        
        base = run_forecast(state, rdate, req_amt)
        preds[rid] = base.amount_safe_to_pay
    return preds


def run_baseline():
    preds = {}
    for s in samples:
        uid = s["user_id"]
        rdate = dt.date.fromisoformat(s["request_date"])
        req_amt = Decimal(s["requested_amount"])
        state = build_financial_state(dataset, uid, rdate, fx, evidence)
        base = run_forecast(state, rdate, req_amt)
        preds[s["request_id"]] = base.amount_safe_to_pay
    return preds


def score(preds):
    correct = 0
    for rid, our in preds.items():
        ref = Decimal(sample_map[rid]["amount_safe_to_pay"])
        if ref == 0:
            if our == 0: correct += 1
        else:
            if abs(our - ref) / ref <= Decimal("0.01"):
                correct += 1
    return correct


print("=== BASELINE ===")
bl = run_baseline()
bl_score = score(bl)
print(f"Score: {bl_score}/25")

# Show baseline detail
print(f"\n{'ReqID':<12} | {'Our':<14} | {'Ref':<14} | {'Err%':<8}")
print("-" * 60)
for rid in sorted(bl.keys()):
    ref = Decimal(sample_map[rid]["amount_safe_to_pay"])
    pct = float(abs(bl[rid] - ref) / ref * 100) if ref > 0 else float(abs(bl[rid] - ref))
    marker = " *" if pct <= 1.0 else ""
    print(f"{rid:<12} | {float(bl[rid]):<14.2f} | {float(ref):<14.2f} | {pct:<8.2f}{marker}")


print("\n=== PERCENTILE SWEEP ===\n")
print(f"{'P%':<5} | {'Score':<8} | Gained / Lost")
print("-" * 60)

for p in range(50, 80):
    preds = run_with_percentile(p)
    s = score(preds)
    
    gained = []
    lost = []
    for rid in sorted(preds.keys()):
        ref = Decimal(sample_map[rid]["amount_safe_to_pay"])
        bl_pct = float(abs(bl[rid] - ref) / ref * 100) if ref > 0 else 99999
        new_pct = float(abs(preds[rid] - ref) / ref * 100) if ref > 0 else 99999
        if bl_pct > 1.0 and new_pct <= 1.0:
            gained.append(rid)
        elif bl_pct <= 1.0 and new_pct > 1.0:
            lost.append(rid)
    
    g_str = "+(" + ",".join(g.replace("request_","r") for g in gained) + ")" if gained else ""
    l_str = " -(" + ",".join(l.replace("request_","r") for l in lost) + ")" if lost else ""
    print(f"{p:<5} | {s}/25    | {g_str}{l_str}")


# ── Detailed P=67 vs baseline ───────────────────────────────────────────
print("\n\n=== DETAILED P=67 ===")
p67 = run_with_percentile(67)
print(f"\n{'ReqID':<12} | {'Baseline':<14} | {'P67':<14} | {'Ref':<14} | {'BL%':<8} | {'P67%':<8} | {'Verdict'}")
print("-" * 100)

for rid in sorted(p67.keys()):
    ref = Decimal(sample_map[rid]["amount_safe_to_pay"])
    b = bl[rid]
    p = p67[rid]
    bl_pct = float(abs(b - ref) / ref * 100) if ref > 0 else float(abs(b - ref))
    p67_pct = float(abs(p - ref) / ref * 100) if ref > 0 else float(abs(p - ref))
    
    if bl_pct <= 1.0 and p67_pct <= 1.0:
        v = "OK"
    elif bl_pct > 1.0 and p67_pct <= 1.0:
        v = "GAINED *"
    elif bl_pct <= 1.0 and p67_pct > 1.0:
        v = "LOST X"
    elif p67_pct < bl_pct * 0.95:
        v = "improved"
    elif p67_pct > bl_pct * 1.05:
        v = "worsened"
    else:
        v = "similar"
    
    print(f"{rid:<12} | {float(b):<14.2f} | {float(p):<14.2f} | {float(ref):<14.2f} | {bl_pct:<8.2f} | {p67_pct:<8.2f} | {v}")
