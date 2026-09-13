#!/usr/bin/env python3
"""Phase 10 Part 4: FX conversion & salary amount verification for request_25."""
import csv
import sys
import datetime as dt
from decimal import Decimal
from pathlib import Path
from collections import defaultdict

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

print("=" * 80)
print("REQUEST_25: FX CONVERSION ANALYSIS")
print("=" * 80)

s = sample_map["request_25"]
uid = s["user_id"]
rdate = dt.date.fromisoformat(s["request_date"])  # 2024-03-06
req_amt = Decimal(s["requested_amount"])
ref_safe = Decimal(s["amount_safe_to_pay"])

state = build_financial_state(dataset, uid, rdate, fx, evidence)

# Salary is USD 1800, home is IDR
# Check all available FX rates for USD->IDR
print(f"\nFX rates USD->IDR around request dates:")
for rate in dataset.rates:
    if "USD" in (rate.from_currency, rate.to_currency) and "IDR" in (rate.from_currency, rate.to_currency):
        print(f"  {rate.rate_date}: {rate.from_currency}->{rate.to_currency} = {rate.rate}")

# Check how salary gets converted for each date
raw_events = dataset.events_by_user.get(uid, [])
salary_events = [e for e in raw_events if e.category == "salary"]
for e in salary_events:
    d = e.settlement_date or e.event_date
    try:
        converted = fx.convert(e.amount, e.currency, "IDR", d)
        print(f"\n  {e.event_id} on {d}: {e.amount} {e.currency} -> {converted:.2f} IDR")
    except Exception as ex:
        print(f"\n  {e.event_id} on {d}: {e.amount} {e.currency} -> ERROR: {ex}")

# Check: the stream uses amount=28,499,994 IDR
# This is the last settled salary converted: 1800 USD on 2024-02-15
# Let's verify
print(f"\nVerification: 1800 USD on 2024-02-15 -> IDR:")
conv_feb = fx.convert(Decimal("1800"), "USD", "IDR", dt.date(2024, 2, 15))
print(f"  {conv_feb:.2f} IDR")

print(f"\nVerification: 1800 USD on 2024-03-15 -> IDR:")
try:
    conv_mar = fx.convert(Decimal("1800"), "USD", "IDR", dt.date(2024, 3, 15))
    print(f"  {conv_mar:.2f} IDR")
except Exception as ex:
    print(f"  ERROR: {ex}")

# Current stream projects salary at the Feb-15 rate. But the reference
# might use a different rate for the scheduled salary on Mar 15.
print(f"\nCurrent salary stream: amount = {state.streams['salary'][0].amount}")

# The discrete event for Mar 15 salary:
for de in state.discrete_events:
    if de.category == "salary":
        print(f"Discrete salary event: date={de.date}, amount={de.signed_amount}")

# So the discrete event on Mar 15 is at the Mar-15 FX rate.
# The stream projects future salary at the Feb-15 FX rate.
# If the rates differ, there's a discrepancy.

print("\n\n" + "=" * 80)
print("REQUEST_25: WHAT IF WE USE DIFFERENT PROJECTED EXPENSES?")
print("=" * 80)

# Our min is on Mar 14 = 23,386,901.24
# Ref min would be 24,804,100
# Difference = 1,417,198.76
# The events between Mar 6 (request) and Mar 14 (our min):
# Let's trace them precisely
base = run_forecast(state, rdate, req_amt)
print(f"\nCheckpoints Mar 6 to Mar 14:")
for d, bal in base.checkpoints:
    if d <= dt.date(2024, 3, 14):
        print(f"  {d}: {bal:.2f}")

# Between Mar 6 and Mar 14 there are:
# dining, utilities, insurance, groceries, streaming, transport, cloud_storage, shopping, entertainment
# These are all projected at their latest historical amounts.
# If the reference uses slightly different amounts (earlier history), the difference accumulates.

# Key insight: The transport stream has interval=5 and projects multiple times.
# Between Mar 6 and Mar 14:
# Mar 10, and then? Let's check.
for cat, slist in state.streams.items():
    for s in slist:
        d = s.next_date
        horizon_end = rdate + dt.timedelta(days=HORIZON_DAYS)
        count_before_min = 0
        dates_before_min = []
        while d <= horizon_end:
            if s.end_date and d > s.end_date:
                break
            if rdate <= d <= dt.date(2024, 3, 14):
                count_before_min += 1
                dates_before_min.append(str(d))
            d = s.advance(d)
        if dates_before_min:
            print(f"  {cat:<20} {s.direction} x{count_before_min} on {dates_before_min}")


print("\n\n" + "=" * 80)
print("REQUEST_25: WHAT IF TRANSPORT USES DIFFERENT CADENCE?")
print("=" * 80)

# Transport has interval=5. What if it were 7 (weekly)?
transport_stream = None
for s in state.streams.get("transport", []):
    transport_stream = s

print(f"Current transport: interval={transport_stream.interval_days}, amount={transport_stream.amount}")

# Let's see what happens with various transport intervals
for test_int in [5, 7, 10, 14]:
    mod_stream = RecurringStream(
        category="transport", direction="debit",
        amount=transport_stream.amount,
        interval_days=test_int,
        monthly=27 <= test_int <= 31,
        next_date=transport_stream.next_date,
        anchor_event_id=transport_stream.anchor_event_id,
        flexibility=transport_stream.flexibility,
        minimum_allowed_amount=transport_stream.minimum_allowed_amount,
        end_date=transport_stream.end_date,
    )
    mod_streams = {}
    for cat, slist in state.streams.items():
        if cat == "transport":
            mod_streams[cat] = [mod_stream]
        else:
            mod_streams[cat] = slist
    mod_state = FinancialState(
        user_id=state.user_id, profile=state.profile,
        starting_balance=state.starting_balance,
        discrete_events=state.discrete_events,
        streams=mod_streams,
    )
    f = run_forecast(mod_state, rdate, req_amt)
    print(f"  transport interval={test_int}: safe={f.amount_safe_to_pay:.2f}")

# What about dining interval (currently 7)?
dining_stream = state.streams.get("dining", [None])[0]
if dining_stream:
    print(f"\nCurrent dining: interval={dining_stream.interval_days}, amount={dining_stream.amount}")
    for test_int in [7, 10, 14, 30]:
        mod_stream = RecurringStream(
            category="dining", direction="debit",
            amount=dining_stream.amount,
            interval_days=test_int,
            monthly=27 <= test_int <= 31,
            next_date=dining_stream.next_date,
            anchor_event_id=dining_stream.anchor_event_id,
            flexibility=dining_stream.flexibility,
            minimum_allowed_amount=dining_stream.minimum_allowed_amount,
            end_date=dining_stream.end_date,
        )
        mod_streams = {}
        for cat, slist in state.streams.items():
            if cat == "dining":
                mod_streams[cat] = [mod_stream]
            else:
                mod_streams[cat] = slist
        mod_state = FinancialState(
            user_id=state.user_id, profile=state.profile,
            starting_balance=state.starting_balance,
            discrete_events=state.discrete_events,
            streams=mod_streams,
        )
        f = run_forecast(mod_state, rdate, req_amt)
        print(f"  dining interval={test_int}: safe={f.amount_safe_to_pay:.2f}")

# What about groceries (currently 10)?
groc_stream = state.streams.get("groceries", [None])[0]
if groc_stream:
    print(f"\nCurrent groceries: interval={groc_stream.interval_days}, amount={groc_stream.amount}")
    for test_int in [7, 10, 14, 30]:
        mod_stream = RecurringStream(
            category="groceries", direction="debit",
            amount=groc_stream.amount,
            interval_days=test_int,
            monthly=27 <= test_int <= 31,
            next_date=groc_stream.next_date,
            anchor_event_id=groc_stream.anchor_event_id,
            flexibility=groc_stream.flexibility,
            minimum_allowed_amount=groc_stream.minimum_allowed_amount,
            end_date=groc_stream.end_date,
        )
        mod_streams = {}
        for cat, slist in state.streams.items():
            if cat == "groceries":
                mod_streams[cat] = [mod_stream]
            else:
                mod_streams[cat] = slist
        mod_state = FinancialState(
            user_id=state.user_id, profile=state.profile,
            starting_balance=state.starting_balance,
            discrete_events=state.discrete_events,
            streams=mod_streams,
        )
        f = run_forecast(mod_state, rdate, req_amt)
        print(f"  groceries interval={test_int}: safe={f.amount_safe_to_pay:.2f}")


print("\n\n" + "=" * 80)
print("REQUEST_10: MASSIVE SALARY OVERCOUNT INVESTIGATION")
print("=" * 80)

s = sample_map["request_10"]
uid = s["user_id"]
rdate = dt.date.fromisoformat(s["request_date"])
req_amt = Decimal(s["requested_amount"])
ref_safe = Decimal(s["amount_safe_to_pay"])

state = build_financial_state(dataset, uid, rdate, fx, evidence)
base = run_forecast(state, rdate, req_amt)

print(f"Our safe: {base.amount_safe_to_pay}, Ref safe: {ref_safe}, Err: {base.amount_safe_to_pay - ref_safe}")
print(f"Starting balance: {state.starting_balance}")
print(f"Min bal: {state.profile.minimum_balance_to_keep}")

# Salary stream
print(f"\nSalary stream(s):")
for s in state.streams.get("salary", []):
    print(f"  interval={s.interval_days}, amount={s.amount}, next={s.next_date}, monthly={s.monthly}")

# Raw salary events
raw_events = dataset.events_by_user.get(uid, [])
salary_events = sorted([e for e in raw_events if e.category == "salary"
                         and e.status not in ("cancelled", "failed", "unrealized")
                         and e.direction == "credit"],
                       key=lambda e: e.settlement_date or e.event_date)

print(f"\nSalary history for {uid} ({len(salary_events)} events):")
dates = []
for e in salary_events:
    d = e.settlement_date or e.event_date
    dates.append(d)
    print(f"  {d} | {e.amount:>12.2f} {e.currency} | {e.status} | {e.event_id} | {e.description}")

if len(dates) >= 2:
    gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
    print(f"\nGaps: {gaps}")
