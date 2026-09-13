#!/usr/bin/env python3
"""Phase 10 Part 6: Refined experiments on expense amount selection."""
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


def score_detail(predictions):
    """Returns (correct, total, detail_dict)."""
    correct = 0
    total = len(predictions)
    detail = {}
    for rid, our_safe in sorted(predictions.items()):
        ref_safe = Decimal(sample_map[rid]["amount_safe_to_pay"])
        err = our_safe - ref_safe
        if ref_safe == 0:
            pct = float(abs(err)) if err != 0 else 0
        else:
            pct = float(abs(err) / ref_safe * 100)
        ok = (ref_safe == 0 and our_safe == 0) or (ref_safe > 0 and pct <= 1.0)
        if ok:
            correct += 1
        detail[rid] = {"our": float(our_safe), "ref": float(ref_safe), "err": float(err), "pct": pct, "ok": ok}
    return correct, total, detail


def build_states():
    """Pre-build all states."""
    states = {}
    for s in samples:
        rid = s["request_id"]
        uid = s["user_id"]
        rdate = dt.date.fromisoformat(s["request_date"])
        req_amt = Decimal(s["requested_amount"])
        state = build_financial_state(dataset, uid, rdate, fx, evidence)
        states[rid] = (uid, rdate, req_amt, state)
    return states

states = build_states()


def run_with_expense_max_n(n):
    """Use max of last N expense amounts instead of median."""
    preds = {}
    for rid, (uid, rdate, req_amt, orig_state) in states.items():
        state = FinancialState(
            user_id=orig_state.user_id, profile=orig_state.profile,
            starting_balance=orig_state.starting_balance,
            discrete_events=orig_state.discrete_events,
            streams={cat: list(slist) for cat, slist in orig_state.streams.items()},
        )
        
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
                                amt = fx.convert(e.amount, e.currency, state.profile.home_currency, d)
                                amounts.append(amt)
                    if len(amounts) >= n:
                        stream.amount = max(amounts[-n:])
        
        base = run_forecast(state, rdate, req_amt)
        preds[rid] = base.amount_safe_to_pay
    return preds


def run_with_expense_percentile(p):
    """Use Pth percentile of all expense amounts."""
    preds = {}
    for rid, (uid, rdate, req_amt, orig_state) in states.items():
        state = FinancialState(
            user_id=orig_state.user_id, profile=orig_state.profile,
            starting_balance=orig_state.starting_balance,
            discrete_events=orig_state.discrete_events,
            streams={cat: list(slist) for cat, slist in orig_state.streams.items()},
        )
        
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
                                amt = fx.convert(e.amount, e.currency, state.profile.home_currency, d)
                                amounts.append(float(amt))
                    if len(amounts) >= 3:
                        # manual percentile
                        amounts.sort()
                        idx = int(len(amounts) * p / 100)
                        idx = min(idx, len(amounts) - 1)
                        stream.amount = Decimal(str(amounts[idx]))
        
        base = run_forecast(state, rdate, req_amt)
        preds[rid] = base.amount_safe_to_pay
    return preds


# ── Baseline ─────────────────────────────────────────────────────────────
print("=== BASELINE ===")
bl_preds = {}
for rid, (uid, rdate, req_amt, state) in states.items():
    bl_preds[rid] = run_forecast(state, rdate, req_amt).amount_safe_to_pay
bl_correct, bl_total, bl_detail = score_detail(bl_preds)
print(f"Score: {bl_correct}/{bl_total}")

# ── Max of last N ────────────────────────────────────────────────────────
for n in [2, 3, 4, 5]:
    print(f"\n=== MAX OF LAST {n} EXPENSES ===")
    preds = run_with_expense_max_n(n)
    c, t, detail = score_detail(preds)
    improved = sum(1 for rid in detail if abs(detail[rid]["err"]) < abs(bl_detail[rid]["err"]) - 1)
    regressed = sum(1 for rid in detail if abs(detail[rid]["err"]) > abs(bl_detail[rid]["err"]) + 1)
    print(f"Score: {c}/{t}  |  Improved: {improved}  |  Regressed: {regressed}")
    
    # Show detail for borderline requests
    for rid in sorted(detail.keys()):
        d = detail[rid]
        b = bl_detail[rid]
        if d["ok"] != b["ok"]:
            change = "GAINED" if d["ok"] else "LOST"
            print(f"  {change}: {rid} pct={d['pct']:.2f}% (was {b['pct']:.2f}%)")

# ── Percentile ───────────────────────────────────────────────────────────
for p in [60, 67, 70, 75, 80, 90, 100]:
    print(f"\n=== {p}TH PERCENTILE EXPENSES ===")
    preds = run_with_expense_percentile(p)
    c, t, detail = score_detail(preds)
    improved = sum(1 for rid in detail if abs(detail[rid]["err"]) < abs(bl_detail[rid]["err"]) - 1)
    regressed = sum(1 for rid in detail if abs(detail[rid]["err"]) > abs(bl_detail[rid]["err"]) + 1)
    print(f"Score: {c}/{t}  |  Improved: {improved}  |  Regressed: {regressed}")
    
    for rid in sorted(detail.keys()):
        d = detail[rid]
        b = bl_detail[rid]
        if d["ok"] != b["ok"]:
            change = "GAINED" if d["ok"] else "LOST"
            print(f"  {change}: {rid} pct={d['pct']:.2f}% (was {b['pct']:.2f}%)")
