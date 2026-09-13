#!/usr/bin/env python3
"""Phase 10 Part 2: Deep-dive into specific forecast mechanics."""
import csv
import sys
import datetime as dt
from decimal import Decimal
from pathlib import Path
from collections import defaultdict
import statistics

REPO_ROOT = Path(r"c:\Users\gokul\hackerrank-orchestrate-september26")
sys.path.insert(0, str(REPO_ROOT / "code"))

from buyorwait.evidence import build_evidence
from buyorwait.fx import FxTable
from buyorwait.io_data import load_dataset
from buyorwait.llm_client import LLMClient
from buyorwait.state import build_financial_state, RecurringStream, FinancialState, _add_months, _bucket
from buyorwait.forecast import run_forecast, HORIZON_DAYS

dataset_dir = REPO_ROOT / "dataset"
cache_path = REPO_ROOT / "code" / "cache" / "evidence_cache.json"
dataset = load_dataset(dataset_dir)
fx = FxTable(dataset.rates)
evidence = build_evidence(dataset, dataset_dir, cache_path, llm_client=LLMClient())
samples = list(csv.DictReader(open(dataset_dir / "sample_requests.csv", encoding="utf-8")))

sample_map = {s["request_id"]: s for s in samples}

def get_state(rid):
    s = sample_map[rid]
    uid = s["user_id"]
    rdate = dt.date.fromisoformat(s["request_date"])
    req_amt = Decimal(s["requested_amount"])
    ref_safe = Decimal(s["amount_safe_to_pay"])
    state = build_financial_state(dataset, uid, rdate, fx, evidence)
    base = run_forecast(state, rdate, req_amt)
    return uid, rdate, req_amt, ref_safe, state, base


print("=" * 80)
print("STEP 4A: ANCHOR DATE ANALYSIS")
print("=" * 80)

# For each stream in each request, compare:
# 1. Current: next_date = last_date + interval
# 2. Alternative: align to observed calendar pattern
for rid in ["request_02", "request_03", "request_04", "request_07",
            "request_19", "request_20", "request_23", "request_24", "request_25"]:
    uid, rdate, req_amt, ref_safe, state, base = get_state(rid)
    our_safe = base.amount_safe_to_pay
    print(f"\n--- {rid} (Our={our_safe:.2f}, Ref={ref_safe:.2f}, Err={float(our_safe-ref_safe):.2f}) ---")
    
    raw_events = dataset.events_by_user.get(uid, [])
    history_by_cat = defaultdict(list)
    for e in raw_events:
        if e.status in ("cancelled", "failed", "unrealized"):
            continue
        if e.direction not in ("debit", "credit"):
            continue
        if e.linked_event_id:
            continue
        ref_date = e.settlement_date or e.event_date
        if ref_date:
            history_by_cat[e.category].append((e, ref_date))
    
    for cat, stream_list in state.streams.items():
        for stream in stream_list:
            hist = sorted(history_by_cat.get(cat, []), key=lambda t: t[1])
            hist_dates = [t[1] for t in hist]
            if len(hist_dates) < 2:
                continue
            last_date = hist_dates[-1]
            gaps = [(hist_dates[i+1] - hist_dates[i]).days for i in range(len(hist_dates)-1)]
            gaps = [g for g in gaps if g > 0]
            if not gaps:
                continue
            
            # Current next_date
            cur_next = stream.next_date
            
            # Alternative: last_date + last_gap
            alt_last_gap = last_date + dt.timedelta(days=gaps[-1])
            
            # Alternative: last_date + mode
            mode_gap = max(set(gaps), key=gaps.count)
            alt_mode = last_date + dt.timedelta(days=mode_gap)
            
            if cur_next != alt_last_gap or cur_next != alt_mode:
                print(f"  {cat:<20} {stream.direction:<6} cur_next={cur_next} "
                      f"last_date={last_date} interval={stream.interval_days} "
                      f"alt_last_gap={alt_last_gap}(gap={gaps[-1]}) "
                      f"alt_mode={alt_mode}(mode={mode_gap}) "
                      f"monthly={stream.monthly}")
                if len(gaps) <= 8:
                    print(f"    raw_gaps: {gaps}")


print("\n\n" + "=" * 80)
print("STEP 4B: DEDUPLICATION WINDOW ANALYSIS")
print("=" * 80)

# Check if the ±5-day dedup window is removing events it shouldn't,
# or failing to remove events it should
for rid in ["request_02", "request_03", "request_04", "request_05", "request_06",
            "request_07", "request_13", "request_17", "request_19",
            "request_21", "request_23", "request_24"]:
    uid, rdate, req_amt, ref_safe, state, base = get_state(rid)
    our_safe = base.amount_safe_to_pay
    
    horizon_end = rdate + dt.timedelta(days=HORIZON_DAYS)
    discrete_dates_by_cat = defaultdict(list)
    for de in state.discrete_events:
        discrete_dates_by_cat[de.category].append(de.date)
    
    dedup_events = []
    for cat, stream_list in state.streams.items():
        nearby = discrete_dates_by_cat.get(cat, [])
        if not nearby:
            continue
        for stream in stream_list:
            date = stream.next_date
            while date <= horizon_end:
                if stream.end_date and date > stream.end_date:
                    break
                if date >= rdate:
                    for d in nearby:
                        gap = abs((date - d).days)
                        if gap <= 5:
                            dedup_events.append({
                                "cat": cat, "stream_date": date, "discrete_date": d,
                                "gap_days": gap, "direction": stream.direction,
                                "amount": float(stream.amount),
                            })
                date = stream.advance(date)
    
    if dedup_events:
        print(f"\n--- {rid} (Our={our_safe:.2f}, Ref={ref_safe:.2f}) ---")
        for de in dedup_events:
            print(f"  DEDUPED: {de['cat']:<20} stream_on={de['stream_date']} "
                  f"discrete_on={de['discrete_date']} gap={de['gap_days']}d "
                  f"{de['direction']} amt={de['amount']:.2f}")


print("\n\n" + "=" * 80)
print("STEP 4C: HORIZON BOUNDARY EVENTS")
print("=" * 80)

for rid in ["request_02", "request_04", "request_05", "request_07",
            "request_19", "request_20", "request_23", "request_24", "request_25"]:
    uid, rdate, req_amt, ref_safe, state, base = get_state(rid)
    horizon_end = rdate + dt.timedelta(days=HORIZON_DAYS)
    
    boundary_events = []
    for cat, stream_list in state.streams.items():
        for stream in stream_list:
            date = stream.next_date
            prev_date = None
            while date <= horizon_end + dt.timedelta(days=5):
                if stream.end_date and date > stream.end_date:
                    break
                if date >= rdate:
                    if abs((date - horizon_end).days) <= 3:
                        inside = date <= horizon_end
                        boundary_events.append({
                            "cat": cat, "date": date, "inside": inside,
                            "direction": stream.direction, "amount": float(stream.amount),
                        })
                prev_date = date
                date = stream.advance(date)
    
    if boundary_events:
        print(f"\n--- {rid} horizon_end={horizon_end} ---")
        for be in boundary_events:
            marker = "INCLUDED" if be["inside"] else "EXCLUDED"
            print(f"  {marker}: {be['cat']:<20} date={be['date']} {be['direction']} amt={be['amount']:.2f}")


print("\n\n" + "=" * 80)
print("STEP 4D: REQUEST_13 SALARY DEEP DIVE (5+26 pattern)")
print("=" * 80)

uid, rdate, req_amt, ref_safe, state, base = get_state("request_13")
raw_events = dataset.events_by_user.get("user_13", [])
salary_events = sorted([e for e in raw_events if e.category == "salary" 
                        and e.status not in ("cancelled", "failed", "unrealized")
                        and e.direction == "credit"],
                       key=lambda e: e.settlement_date or e.event_date)

print(f"User 13 salary history ({len(salary_events)} events):")
dates = []
for e in salary_events:
    d = e.settlement_date or e.event_date
    dates.append(d)
    amt_str = str(e.amount) if e.amount else "BLANK"
    print(f"  {d} | {amt_str:>10} | {e.status} | {e.event_id}")

if len(dates) >= 2:
    gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
    print(f"\nGaps: {gaps}")
    print(f"Median gap: {statistics.median(gaps)}")
    print(f"Buckets: {[_bucket(g) for g in gaps]}")

print(f"\nCurrent salary stream(s):")
for s in state.streams.get("salary", []):
    print(f"  interval={s.interval_days}, monthly={s.monthly}, next_date={s.next_date}, "
          f"amount={s.amount}, end_date={s.end_date}")

# What does the reference show?
# ref_safe = 433.40. Our = 640.88. Err = 207.48.
# Our min_proj_bal = 1940.88. min_bal = 1300.
# Our safe = 1940.88 - 1300 = 640.88
# Ref safe = 433.40 means ref_min = 1300 + 433.40 = 1733.40
# Difference from our min: 1940.88 - 1733.40 = 207.48
# So the reference projected ~207.48 MORE expenses (or less income) before the minimum.
print(f"\nRef implies 207.48 more expense (or less income) before minimum")
print(f"Our min at {base.checkpoints[0][0] if base.checkpoints else 'N/A'}")
# Check events around min
print(f"Min projected bal: {min(bal for _, bal in base.checkpoints):.2f}")


print("\n\n" + "=" * 80)
print("STEP 7: SMALL ERROR REQUESTS - EXACT EVENT DIFFERENCES")
print("=" * 80)

small_requests = {
    "request_08": -3.24,
    "request_14": 41.31,
    "request_17": -139.88,
    "request_18": 68.21,
    "request_21": 31.05,
    "request_22": -6.16,
}

for rid, expected_err in small_requests.items():
    uid, rdate, req_amt, ref_safe, state, base = get_state(rid)
    our_safe = base.amount_safe_to_pay
    err = float(our_safe - ref_safe)
    
    print(f"\n--- {rid} (Our={our_safe:.2f}, Ref={ref_safe:.2f}, Err={err:.2f}) ---")
    
    # The error is directly attributable to projected events.
    # If err > 0, we have MORE projected balance than ref (fewer expenses or more income).
    # If err < 0, we have LESS projected balance (more expenses or less income).
    
    # Which single event amounts match the error?
    for cat, stream_list in state.streams.items():
        for stream in stream_list:
            if abs(float(stream.amount) - abs(err)) < 1.0:
                print(f"  *** AMOUNT MATCH: {cat} {stream.direction} amt={stream.amount:.2f} ~= |err|={abs(err):.2f}")
            # Also check projected count differences
            horizon_end = rdate + dt.timedelta(days=HORIZON_DAYS)
            count = 0
            d = stream.next_date
            while d <= horizon_end:
                if stream.end_date and d > stream.end_date:
                    break
                if d >= rdate:
                    count += 1
                d = stream.advance(d)
            
            # What if we had one less or one more occurrence?
            if abs(float(stream.amount) - abs(err)) > 1.0:
                if abs(float(stream.amount * 2) - abs(err)) < max(1.0, abs(err) * 0.05):
                    print(f"  *** 2x AMOUNT MATCH: {cat} {stream.direction} 2*{stream.amount:.2f}={2*stream.amount:.2f} ~= |err|={abs(err):.2f}")
