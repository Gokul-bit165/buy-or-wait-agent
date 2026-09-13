#!/usr/bin/env python3
"""Phase 10 — Forecast Event-by-Event Differential Analysis.

Read-only scratchpad: does NOT modify production code.
Dumps every projected event, traces minimums, performs leave-one-out
experiments, and tests anchor/cadence/horizon mechanics.
"""
import csv
import sys
import datetime as dt
import json
import statistics
from decimal import Decimal
from pathlib import Path
from copy import deepcopy
from collections import defaultdict

REPO_ROOT = Path(r"c:\Users\gokul\hackerrank-orchestrate-september26")
sys.path.insert(0, str(REPO_ROOT / "code"))

from buyorwait.evidence import build_evidence
from buyorwait.fx import FxTable
from buyorwait.io_data import Request, load_dataset
from buyorwait.llm_client import LLMClient
from buyorwait.state import build_financial_state, RecurringStream, _add_months
from buyorwait.forecast import run_forecast, HORIZON_DAYS, _stream_occurrences, _checkpoints

# ── helpers ──────────────────────────────────────────────────────────────

dataset_dir = REPO_ROOT / "dataset"
cache_path = REPO_ROOT / "code" / "cache" / "evidence_cache.json"
dataset = load_dataset(dataset_dir)
fx = FxTable(dataset.rates)
evidence = build_evidence(dataset, dataset_dir, cache_path, llm_client=LLMClient())
samples = list(csv.DictReader(open(dataset_dir / "sample_requests.csv", encoding="utf-8")))

def build_sample(sample):
    """Returns (request, state, base_forecast, profile)."""
    rid = sample["request_id"]
    uid = sample["user_id"]
    rdate = dt.date.fromisoformat(sample["request_date"])
    cdate = dt.date.fromisoformat(sample["desired_completion_date"])
    req_amt = Decimal(sample["requested_amount"])
    profile = dataset.profiles[uid]
    state = build_financial_state(dataset, uid, rdate, fx, evidence)
    base = run_forecast(state, rdate, req_amt)
    return rid, uid, rdate, cdate, req_amt, profile, state, base

# ── STEP 1: dump all projected events ────────────────────────────────────

def dump_all_events(state, rdate):
    """Return list of dicts describing every event in the forecast window."""
    horizon_end = rdate + dt.timedelta(days=HORIZON_DAYS)
    events = []

    # Discrete events
    for de in state.discrete_events:
        if rdate <= de.date <= horizon_end:
            events.append({
                "date": str(de.date),
                "category": de.category,
                "direction": "credit" if de.signed_amount > 0 else "debit",
                "amount": float(abs(de.signed_amount)),
                "signed": float(de.signed_amount),
                "source": "discrete",
                "event_id": de.event_id,
                "stream_anchor": None,
                "interval": None,
                "monthly": None,
            })

    # Recurring stream projections
    discrete_dates_by_category = defaultdict(list)
    for de in state.discrete_events:
        discrete_dates_by_category[de.category].append(de.date)

    for category, stream_list in state.streams.items():
        nearby = discrete_dates_by_category.get(category, [])
        for stream in stream_list:
            date = stream.next_date
            while date <= horizon_end:
                if stream.end_date is not None and date > stream.end_date:
                    break
                if date >= rdate:
                    skip = any(abs((date - d).days) <= 5 for d in nearby)
                    signed = float(stream.amount if stream.direction == "credit" else -stream.amount)
                    events.append({
                        "date": str(date),
                        "category": category,
                        "direction": stream.direction,
                        "amount": float(stream.amount),
                        "signed": signed,
                        "source": "recurring",
                        "event_id": stream.anchor_event_id,
                        "stream_anchor": stream.anchor_event_id,
                        "interval": stream.interval_days,
                        "monthly": stream.monthly,
                        "deduped": skip,
                        "next_date": str(stream.next_date),
                    })
                date = stream.advance(date)

    events.sort(key=lambda e: e["date"])
    return events

# ── STEP 2/3: minimum trace ─────────────────────────────────────────────

def trace_minimum(state, rdate, req_amt, ref_safe):
    """Detailed day-by-day trace around the minimum."""
    base = run_forecast(state, rdate, req_amt)
    our_safe = base.amount_safe_to_pay
    min_bal = state.profile.minimum_balance_to_keep
    min_proj = min(bal for _, bal in base.checkpoints)
    ref_implied_min = min_proj - ref_safe

    # Find the minimum date
    min_date = rdate
    for d, bal in base.checkpoints:
        if bal == min_proj:
            min_date = d
            break

    # Get all individual flows
    horizon_end = rdate + dt.timedelta(days=HORIZON_DAYS)
    all_flows = []
    for de in state.discrete_events:
        if rdate <= de.date <= horizon_end:
            all_flows.append((de.date, de.signed_amount, f"discrete:{de.category}:{de.event_id}"))
    
    for category, stream_list in state.streams.items():
        discrete_dates = [de.date for de in state.discrete_events if de.category == category]
        for stream in stream_list:
            date = stream.next_date
            while date <= horizon_end:
                if stream.end_date is not None and date > stream.end_date:
                    break
                if date >= rdate:
                    skip = any(abs((date - d).days) <= 5 for d in discrete_dates)
                    if not skip and stream.amount != 0:
                        signed = stream.amount if stream.direction == "credit" else -stream.amount
                        all_flows.append((date, signed, f"recurring:{category}:{stream.anchor_event_id}"))
                date = stream.advance(date)

    # Group by date
    flows_by_date = defaultdict(list)
    for d, amt, desc in all_flows:
        flows_by_date[d].append((amt, desc))

    # Build daily trace around minimum (±10 days)
    trace_start = max(rdate, min_date - dt.timedelta(days=10))
    trace_end = min(horizon_end, min_date + dt.timedelta(days=10))
    
    running = state.starting_balance
    trace = []
    # First accumulate up to trace_start
    for d, bal in base.checkpoints:
        if d < trace_start:
            running = bal
    
    # Now trace day by day
    running = state.starting_balance
    day_checkpoints = {d: bal for d, bal in base.checkpoints}
    
    return {
        "our_safe": float(our_safe),
        "ref_safe": float(ref_safe),
        "diff": float(our_safe - ref_safe),
        "min_proj_bal": float(min_proj),
        "min_date": str(min_date),
        "min_bal_conf": float(min_bal),
        "ref_implied_min": float(ref_implied_min),
        "imp_ratio": float(ref_implied_min / min_bal) if min_bal > 0 else 0.0,
        "checkpoints_around_min": [
            {"date": str(d), "balance": float(bal)}
            for d, bal in base.checkpoints
            if trace_start <= d <= trace_end
        ],
        "flows_around_min": {
            str(d): [{"amount": float(a), "desc": desc} for a, desc in flows]
            for d, flows in flows_by_date.items()
            if trace_start <= d <= trace_end
        },
    }


# ── STEP 5: leave-one-stream-out ─────────────────────────────────────────

def leave_one_stream_out(state, rdate, req_amt):
    """For each recurring stream, remove it and recompute safe amount."""
    base = run_forecast(state, rdate, req_amt)
    baseline_safe = base.amount_safe_to_pay
    results = []

    for category, stream_list in state.streams.items():
        for i, stream in enumerate(stream_list):
            # Create modified state without this stream
            mod_streams = {}
            for cat2, slist2 in state.streams.items():
                if cat2 == category:
                    mod_streams[cat2] = [s for j, s in enumerate(slist2) if j != i]
                    if not mod_streams[cat2]:
                        del mod_streams[cat2]
                else:
                    mod_streams[cat2] = slist2

            from buyorwait.state import FinancialState
            mod_state = FinancialState(
                user_id=state.user_id,
                profile=state.profile,
                starting_balance=state.starting_balance,
                discrete_events=state.discrete_events,
                streams=mod_streams,
            )
            mod_forecast = run_forecast(mod_state, rdate, req_amt)
            mod_safe = mod_forecast.amount_safe_to_pay

            # Count projected events for this stream
            horizon_end = rdate + dt.timedelta(days=HORIZON_DAYS)
            proj_count = 0
            proj_total = Decimal(0)
            date = stream.next_date
            while date <= horizon_end:
                if stream.end_date is not None and date > stream.end_date:
                    break
                if date >= rdate:
                    proj_count += 1
                    proj_total += stream.amount
                date = stream.advance(date)

            # Monthly equivalent
            if stream.monthly:
                m_equiv = stream.amount
            else:
                m_equiv = stream.amount * Decimal("30") / Decimal(stream.interval_days) if stream.interval_days else stream.amount

            results.append({
                "category": category,
                "direction": stream.direction,
                "amount": float(stream.amount),
                "interval": stream.interval_days,
                "monthly": stream.monthly,
                "anchor": stream.anchor_event_id,
                "next_date": str(stream.next_date),
                "end_date": str(stream.end_date) if stream.end_date else None,
                "projected_events": proj_count,
                "projected_total": float(proj_total),
                "monthly_equiv": float(m_equiv),
                "baseline_safe": float(baseline_safe),
                "modified_safe": float(mod_safe),
                "impact": float(mod_safe - baseline_safe),
            })

    results.sort(key=lambda r: abs(r["impact"]), reverse=True)
    return results


# ── STEP 4: test anchor/cadence mechanics ────────────────────────────────

def test_cadence_variants(state, rdate, req_amt, uid):
    """For each stream, compute projected events under different interval
    choices and report the effect on safe amount."""
    from buyorwait.state import FinancialState
    results = []
    
    # Get raw history to compute alternative intervals
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
    
    for category, stream_list in state.streams.items():
        hist = sorted(history_by_cat.get(category, []), key=lambda t: t[1])
        hist_dates = [t[1] for t in hist]
        if len(hist_dates) >= 2:
            gaps = [(hist_dates[i+1] - hist_dates[i]).days for i in range(len(hist_dates)-1)]
            gaps = [g for g in gaps if g > 0]
        else:
            gaps = []
        
        for stream in stream_list:
            if not gaps:
                continue
            
            current_interval = stream.interval_days
            candidates = {
                "current_median": current_interval,
                "mode": max(set(gaps), key=gaps.count) if gaps else current_interval,
                "mean": round(sum(gaps) / len(gaps)) if gaps else current_interval,
                "last_interval": gaps[-1] if gaps else current_interval,
                "median_last3": round(statistics.median(gaps[-3:])) if len(gaps) >= 3 else current_interval,
            }
            
            variant_results = {}
            for name, interval in candidates.items():
                if interval == current_interval and name != "current_median":
                    variant_results[name] = "same_as_current"
                    continue
                
                # Build modified stream
                mod_stream = RecurringStream(
                    category=stream.category,
                    direction=stream.direction,
                    amount=stream.amount,
                    interval_days=interval,
                    monthly=27 <= interval <= 31,
                    next_date=stream.next_date if name != "last_event_plus_interval" else (hist_dates[-1] + dt.timedelta(days=interval)),
                    anchor_event_id=stream.anchor_event_id,
                    flexibility=stream.flexibility,
                    minimum_allowed_amount=stream.minimum_allowed_amount,
                    end_date=stream.end_date,
                )
                
                # Build modified state
                mod_streams = {}
                for cat2, slist2 in state.streams.items():
                    if cat2 == category:
                        mod_streams[cat2] = [mod_stream if s is stream else s for s in slist2]
                    else:
                        mod_streams[cat2] = slist2
                
                mod_state = FinancialState(
                    user_id=state.user_id,
                    profile=state.profile,
                    starting_balance=state.starting_balance,
                    discrete_events=state.discrete_events,
                    streams=mod_streams,
                )
                mod_forecast = run_forecast(mod_state, rdate, req_amt)
                variant_results[name] = float(mod_forecast.amount_safe_to_pay)
            
            results.append({
                "category": category,
                "direction": stream.direction,
                "current_interval": current_interval,
                "raw_gaps": gaps[:10],
                "gap_count": len(gaps),
                "variants": variant_results,
            })
    
    return results


# ── MAIN ─────────────────────────────────────────────────────────────────

def main():
    all_results = {}
    
    for sample in samples:
        rid, uid, rdate, cdate, req_amt, profile, state, base = build_sample(sample)
        ref_safe = Decimal(sample["amount_safe_to_pay"])
        our_safe = base.amount_safe_to_pay
        err = our_safe - ref_safe
        pct_err = float(abs(err) / ref_safe * 100) if ref_safe > 0 else float(abs(err))

        result = {
            "request_id": rid,
            "user_id": uid,
            "currency": profile.home_currency,
            "requested_amount": float(req_amt),
            "starting_balance": float(state.starting_balance),
            "min_bal": float(state.profile.minimum_balance_to_keep),
            "our_safe": float(our_safe),
            "ref_safe": float(ref_safe),
            "err": float(err),
            "pct_err": pct_err,
        }

        # Step 1: dump events
        result["events"] = dump_all_events(state, rdate)

        # Step 2/3: minimum trace
        result["trace"] = trace_minimum(state, rdate, req_amt, ref_safe)

        # Step 5: leave-one-stream-out (only for mismatched requests)
        if abs(err) > Decimal("0.01"):
            result["leave_one_out"] = leave_one_stream_out(state, rdate, req_amt)
        
        # Step 4: cadence variants (only for significant mismatches)
        if pct_err > 1.0:
            result["cadence_variants"] = test_cadence_variants(state, rdate, req_amt, uid)
        
        # Stream summary
        stream_summary = []
        for cat, slist in state.streams.items():
            for s in slist:
                stream_summary.append({
                    "category": cat,
                    "direction": s.direction,
                    "amount": float(s.amount),
                    "interval": s.interval_days,
                    "monthly": s.monthly,
                    "next_date": str(s.next_date),
                    "end_date": str(s.end_date) if s.end_date else None,
                    "anchor": s.anchor_event_id,
                })
        result["streams"] = stream_summary
        
        # Discrete events summary
        result["discrete_events"] = [
            {"date": str(de.date), "amount": float(de.signed_amount), "category": de.category, "event_id": de.event_id}
            for de in state.discrete_events
        ]

        all_results[rid] = result

    # Dump full results
    out_path = REPO_ROOT / "evaluation" / "phase10_dump.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    
    # ── PRINT SUMMARY ────────────────────────────────────────────────────
    print("=== PHASE 10: FORECAST DIFFERENTIAL SUMMARY ===\n")
    
    print(f"{'ReqID':<12} | {'Our Safe':<12} | {'Ref Safe':<12} | {'Err':<12} | {'%Err':<8} | {'MinProjBal':<12} | {'MinDate':<12} | {'Streams':<6} | {'Discrete':<8} | {'ProjEvents':<10}")
    print("-" * 130)
    
    for rid in sorted(all_results.keys()):
        r = all_results[rid]
        t = r["trace"]
        n_streams = len(r["streams"])
        n_discrete = len(r["discrete_events"])
        n_proj = sum(1 for e in r["events"] if e["source"] == "recurring" and not e.get("deduped", False))
        print(f"{rid:<12} | {r['our_safe']:<12.2f} | {r['ref_safe']:<12.2f} | {r['err']:<12.2f} | {r['pct_err']:<8.1f} | {t['min_proj_bal']:<12.2f} | {t['min_date']:<12} | {n_streams:<6} | {n_discrete:<8} | {n_proj:<10}")
    
    # ── LEAVE-ONE-OUT TOP IMPACTS ────────────────────────────────────────
    print("\n\n=== LEAVE-ONE-STREAM-OUT: TOP IMPACTS PER REQUEST ===\n")
    
    focus_requests = [
        "request_02", "request_03", "request_04", "request_05", "request_06",
        "request_07", "request_10", "request_11", "request_13",
        "request_17", "request_19", "request_20", "request_21",
        "request_23", "request_24", "request_25",
    ]
    
    for rid in focus_requests:
        r = all_results.get(rid, {})
        loo = r.get("leave_one_out", [])
        if not loo:
            continue
        print(f"\n--- {rid} (Our={r['our_safe']:.2f}, Ref={r['ref_safe']:.2f}, Err={r['err']:.2f}) ---")
        for entry in loo[:5]:  # top 5 by impact
            print(f"  {entry['category']:<20} {entry['direction']:<6} amt={entry['amount']:<10.2f} int={entry['interval']:<4} "
                  f"proj={entry['projected_events']:<3} impact={entry['impact']:<+12.2f} "
                  f"(safe without: {entry['modified_safe']:.2f})")

    # ── CADENCE VARIANT EFFECTS ──────────────────────────────────────────
    print("\n\n=== CADENCE VARIANT EFFECTS (streams where variants differ) ===\n")
    
    for rid in focus_requests:
        r = all_results.get(rid, {})
        cv = r.get("cadence_variants", [])
        if not cv:
            continue
        interesting = [c for c in cv if any(v != "same_as_current" and v != c["variants"].get("current_median") for v in c["variants"].values())]
        if not interesting:
            continue
        print(f"\n--- {rid} (Our={r['our_safe']:.2f}, Ref={r['ref_safe']:.2f}) ---")
        for c in interesting[:5]:
            print(f"  {c['category']:<20} {c['direction']:<6} cur_int={c['current_interval']} gaps={c['raw_gaps'][:5]}")
            for vname, vval in c["variants"].items():
                if vval != "same_as_current":
                    marker = " <<< closer to ref" if isinstance(vval, float) and abs(vval - r['ref_safe']) < abs(r['our_safe'] - r['ref_safe']) else ""
                    print(f"    {vname:<20}: safe={vval}{marker}")
    
    # ── SMALL-ERROR ANALYSIS ─────────────────────────────────────────────
    print("\n\n=== SMALL-ERROR REQUESTS: FLOW-BY-FLOW AROUND MINIMUM ===\n")
    
    small_err_requests = ["request_08", "request_14", "request_17", "request_21", "request_22"]
    
    for rid in small_err_requests:
        r = all_results.get(rid, {})
        t = r.get("trace", {})
        print(f"\n--- {rid} (Our={r['our_safe']:.2f}, Ref={r['ref_safe']:.2f}, Err={r['err']:.2f}) ---")
        print(f"  Min date: {t['min_date']}, Min bal: {t['min_proj_bal']:.2f}, Configured min: {t['min_bal_conf']:.2f}")
        print(f"  Ref implied min: {t['ref_implied_min']:.2f}, Ratio: {t['imp_ratio']:.4f}")
        print(f"  Checkpoints around min:")
        for cp in t.get("checkpoints_around_min", []):
            flows = t.get("flows_around_min", {}).get(cp["date"], [])
            flow_str = ", ".join(f"{f['desc']}={f['amount']:.2f}" for f in flows) if flows else "no events"
            print(f"    {cp['date']}: bal={cp['balance']:.2f}  [{flow_str}]")

    # ── CROSS-REQUEST PATTERN SEARCH ─────────────────────────────────────
    print("\n\n=== CROSS-REQUEST DOMINANT-STREAM PATTERN ===\n")
    
    # For each request, find the stream whose removal most reduces the error
    stream_patterns = defaultdict(list)
    for rid in focus_requests:
        r = all_results.get(rid, {})
        loo = r.get("leave_one_out", [])
        if not loo:
            continue
        ref_safe = r["ref_safe"]
        our_safe = r["our_safe"]
        err = our_safe - ref_safe
        
        for entry in loo:
            mod_safe = entry["modified_safe"]
            mod_err = mod_safe - ref_safe
            if abs(mod_err) < abs(err):  # removing this stream improved things
                improvement = abs(err) - abs(mod_err)
                stream_patterns[entry["category"]].append({
                    "request": rid,
                    "direction": entry["direction"],
                    "interval": entry["interval"],
                    "improvement": improvement,
                    "err_before": err,
                    "err_after": mod_err,
                })
    
    print("Categories where removal improves error (sorted by frequency):")
    for cat, entries in sorted(stream_patterns.items(), key=lambda x: -len(x[1])):
        if len(entries) >= 2:
            print(f"\n  {cat} (affects {len(entries)} requests):")
            for e in entries:
                print(f"    {e['request']}: improvement={e['improvement']:.2f}, err {e['err_before']:.2f} -> {e['err_after']:.2f}")


if __name__ == "__main__":
    main()
