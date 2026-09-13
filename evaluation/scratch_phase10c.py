#!/usr/bin/env python3
"""Phase 10 Part 3: Critical deep-dives into request_13 and request_25."""
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
from buyorwait.state import build_financial_state, RecurringStream, FinancialState, _add_months, _bucket, _detect_backbone, _split_independent_streams
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
print("REQUEST_13: TWO-STREAM SALARY ANALYSIS")
print("=" * 80)
uid, rdate, req_amt, ref_safe, state, base = get_state("request_13")

# There are clearly TWO salary streams:
# 1. ~1343.54 on the 15th of each month
# 2. Variable amounts (~993, 771, 948, 881) on the 20th of each month
# Our system detects these as a single stream with interval=31 (median of all gaps).
# But the reference may treat them as two separate streams.

raw_events = dataset.events_by_user.get("user_13", [])
salary_events = sorted([e for e in raw_events if e.category == "salary" 
                        and e.status not in ("cancelled", "failed", "unrealized")
                        and e.direction == "credit"],
                       key=lambda e: e.settlement_date or e.event_date)

print(f"\nSalary events for user_13:")
items = []
for e in salary_events:
    d = e.settlement_date or e.event_date
    home_amt = fx.convert(e.amount, e.currency, state.profile.home_currency, d)
    items.append((e, home_amt, d))
    print(f"  {d} | {home_amt:>10.2f} | {e.status} | {e.event_id} | {e.description}")

print(f"\nCurrent backbone detection result:")
result = _detect_backbone(items)
if result:
    chain, interval = result
    print(f"  Chain length: {len(chain)}, interval: {interval}")
    for e, amt, d in chain:
        print(f"    {d} | {amt:.2f} | {e.event_id}")

# Now test what happens if we split into independent streams
print(f"\nIndependent stream splitting result:")
for chain, interval in _split_independent_streams(items):
    print(f"\n  Stream (interval={interval}, len={len(chain)}):")
    for e, amt, d in chain:
        print(f"    {d} | {amt:.2f} | {e.event_id}")

# Simulate with two distinct salary streams
print(f"\nSimulation: What if salary was split into 15th and 20th streams?")
stream_15 = RecurringStream(
    category="salary",
    direction="credit",
    amount=Decimal("1343.54"),
    interval_days=30,
    monthly=True,
    next_date=dt.date(2024, 4, 15),
    anchor_event_id="event_1161",
    flexibility="fixed",
    minimum_allowed_amount=None,
    end_date=None,
)

# 20th stream: last seen was 2024-01-20 at 881.45, and Feb+Mar didn't have a 20th entry
# (Feb 15 is there as event_1087, Mar 15 as event_1161, but no 20th in Feb or Mar)
# So this stream may have ended.
amt_20 = statistics.median([Decimal("993.88"), Decimal("771.17"), Decimal("948.46"), Decimal("881.45")])
print(f"  20th stream median amount: {amt_20}")

stream_20 = RecurringStream(
    category="salary",
    direction="credit",
    amount=amt_20,
    interval_days=30,
    monthly=True,
    next_date=dt.date(2024, 2, 20),
    anchor_event_id="event_1080",
    flexibility="fixed",
    minimum_allowed_amount=None,
    end_date=None,
)

# Test scenario: both streams active
mod_state_both = FinancialState(
    user_id=state.user_id,
    profile=state.profile,
    starting_balance=state.starting_balance,
    discrete_events=state.discrete_events,
    streams={cat: list(slist) for cat, slist in state.streams.items()},
)
mod_state_both.streams["salary"] = [stream_15, stream_20]
f_both = run_forecast(mod_state_both, rdate, req_amt)
print(f"  Both streams active: safe={f_both.amount_safe_to_pay:.2f} (ref={ref_safe})")

# Test scenario: only 15th stream (20th ended after Jan 20)
stream_20_ended = RecurringStream(
    category="salary",
    direction="credit",
    amount=amt_20,
    interval_days=30,
    monthly=True,
    next_date=dt.date(2024, 2, 20),
    anchor_event_id="event_1080",
    flexibility="fixed",
    minimum_allowed_amount=None,
    end_date=dt.date(2024, 1, 20),
)

mod_state_15only = FinancialState(
    user_id=state.user_id,
    profile=state.profile,
    starting_balance=state.starting_balance,
    discrete_events=state.discrete_events,
    streams={cat: list(slist) for cat, slist in state.streams.items()},
)
mod_state_15only.streams["salary"] = [stream_15]
f_15only = run_forecast(mod_state_15only, rdate, req_amt)
print(f"  Only 15th stream: safe={f_15only.amount_safe_to_pay:.2f} (ref={ref_safe})")

# Test scenario: only 15th stream with different amounts
for test_amt in [Decimal("1343.54"), Decimal("1300"), Decimal("1250"), Decimal("1200")]:
    stream_test = RecurringStream(
        category="salary", direction="credit", amount=test_amt,
        interval_days=30, monthly=True, next_date=dt.date(2024, 4, 15),
        anchor_event_id="event_1161", flexibility="fixed",
        minimum_allowed_amount=None, end_date=None,
    )
    mod = FinancialState(
        user_id=state.user_id, profile=state.profile,
        starting_balance=state.starting_balance,
        discrete_events=state.discrete_events,
        streams={cat: list(slist) for cat, slist in state.streams.items()},
    )
    mod.streams["salary"] = [stream_test]
    f = run_forecast(mod, rdate, req_amt)
    print(f"  15th stream at {test_amt}: safe={f.amount_safe_to_pay:.2f}")


print("\n\n" + "=" * 80)
print("REQUEST_25: MASSIVE ERROR INVESTIGATION")
print("=" * 80)
uid, rdate, req_amt, ref_safe, state, base = get_state("request_25")

# Error: -1,417,198.76. Our safe = 7,801.24. Ref safe = 1,425,000.
# This is a massive discrepancy.
print(f"User: {uid}, Request date: {rdate}, Req amount: {req_amt}")
print(f"Our safe: {base.amount_safe_to_pay}, Ref safe: {ref_safe}")
print(f"Starting balance: {state.starting_balance}")
print(f"Min balance: {state.profile.minimum_balance_to_keep}")

# Dump all streams
print(f"\nStreams ({len(state.streams)} categories):")
total_monthly_debit = Decimal(0)
total_monthly_credit = Decimal(0)
for cat, slist in sorted(state.streams.items()):
    for s in slist:
        m_equiv = s.amount * Decimal("30") / Decimal(s.interval_days) if s.interval_days else s.amount
        if s.direction == "debit":
            total_monthly_debit += m_equiv
        else:
            total_monthly_credit += m_equiv
        print(f"  {cat:<20} {s.direction:<6} amt={s.amount:<15.2f} int={s.interval_days:<4} "
              f"monthly={s.monthly} next={s.next_date} end={s.end_date} "
              f"monthly_equiv={m_equiv:.2f}")

print(f"\nTotal monthly debit: {total_monthly_debit:.2f}")
print(f"Total monthly credit: {total_monthly_credit:.2f}")
print(f"Net monthly: {total_monthly_credit - total_monthly_debit:.2f}")

# Dump discrete events
print(f"\nDiscrete events ({len(state.discrete_events)}):")
for de in state.discrete_events:
    print(f"  {de.date} | {de.signed_amount:>15.2f} | {de.category} | {de.event_id}")

# Financial events for user_25
raw_events_25 = sorted([e for e in dataset.events_by_user.get(uid, []) 
                         if e.status not in ("cancelled", "failed")],
                       key=lambda e: e.settlement_date or e.event_date)

print(f"\n\nRaw financial events for {uid} ({len(raw_events_25)} events):")
for e in raw_events_25:
    d = e.settlement_date or e.event_date
    print(f"  {d} | {e.amount:>15.2f} {e.currency} | {e.direction:<6} | {e.category:<20} | "
          f"{e.status:<12} | {e.event_id} | linked={e.linked_event_id}")

# Checkpoints
print(f"\nCheckpoints (first 20):")
for i, (d, bal) in enumerate(base.checkpoints[:20]):
    print(f"  {d}: {bal:.2f}")

print(f"\nCheckpoints (last 10):")
for d, bal in base.checkpoints[-10:]:
    print(f"  {d}: {bal:.2f}")

print(f"\nMin projected balance: {min(bal for _, bal in base.checkpoints):.2f}")
min_date = min(base.checkpoints, key=lambda x: x[1])
print(f"Min date: {min_date[0]}, Min bal: {min_date[1]:.2f}")

# Check: what is the reference expecting?
# ref_safe = 1,425,000. min_bal = 15,585,678.
# min_proj = min_bal + ref_safe = 15,585,678 + 1,425,000 = 17,010,678.
# Our min_proj = 23,386,901.24.
# Difference: 23,386,901.24 - 17,010,678 = 6,376,223.24
# That means the reference projects 6.3M MORE expenses or LESS income.
print(f"\nRef implied min_proj: {state.profile.minimum_balance_to_keep + ref_safe:.2f}")
print(f"Our min_proj: {min(bal for _, bal in base.checkpoints):.2f}")
print(f"Difference: {min(bal for _, bal in base.checkpoints) - (state.profile.minimum_balance_to_keep + ref_safe):.2f}")

# Could it be a currency conversion issue?
print(f"\nProfile currency: {state.profile.home_currency}")
for e in raw_events_25:
    if e.currency != state.profile.home_currency:
        d = e.settlement_date or e.event_date
        print(f"  FOREIGN: {d} | {e.amount} {e.currency} -> home | {e.category} | {e.event_id}")
