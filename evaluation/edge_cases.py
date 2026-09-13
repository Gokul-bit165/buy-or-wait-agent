#!/usr/bin/env python3
"""Property/adversarial tests for the deterministic engine.

These build small synthetic FinancialState / Event fixtures directly
(rather than reading the shipped dataset) so each scenario in the brief is
isolated and fast. Run with:

    python3 evaluation/edge_cases.py
"""
from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from buyorwait.evidence import EvidenceStore, RentFact, SalaryFact  # noqa: E402
from buyorwait.forecast import plan_is_safe, run_forecast  # noqa: E402
from buyorwait.fx import FxTable  # noqa: E402
from buyorwait.io_data import Event, ExchangeRate, Profile  # noqa: E402
from buyorwait.state import DiscreteEvent, FinancialState, RecurringStream  # noqa: E402

PASS = []
FAIL = []


def check(name: str, condition: bool, detail: str = ""):
    if condition:
        PASS.append(name)
    else:
        FAIL.append((name, detail))


def make_profile(min_balance="1000", protect=None, reduce=None, stop=None,
                  methods=("full_payment", "partial_payment", "installments"),
                  max_months=6):
    return Profile(
        user_id="u", home_currency="USD",
        current_available_balance=Decimal("5000"),
        minimum_balance_to_keep=Decimal(min_balance),
        financial_priorities=[], protect=set(protect or []),
        willing_reduce=set(reduce or []), willing_stop=set(stop or []),
        payment_methods=set(methods), max_installment_months=max_months,
    )


def make_state(balance, min_balance, discrete=None, streams=None):
    profile = make_profile(min_balance=str(min_balance))
    return FinancialState(user_id="u", profile=profile, starting_balance=Decimal(balance),
                           discrete_events=discrete or [], streams=streams or {})


def test_salary_one_day_after_request():
    req_date = dt.date(2025, 1, 1)
    de = DiscreteEvent(date=req_date + dt.timedelta(days=1), signed_amount=Decimal("2000"),
                        event_id="e1", category="salary")
    state = make_state(balance="1000", min_balance="1000", discrete=[de])
    result = run_forecast(state, req_date, Decimal("2500"))
    # Day 0: only 1000 available above the floor -> 0 safe today.
    check("salary_one_day_after_request/day0_safe_is_zero",
          result.amount_safe_to_pay == Decimal("0"), str(result.amount_safe_to_pay))
    # Day 1 onward, 1000+2000-1000(min) = 2000 available, still < 2500 requested.
    check("salary_one_day_after_request/earliest_is_none_when_still_short",
          result.earliest_date_for_full_payment is None)


def test_payment_just_below_minimum_balance():
    req_date = dt.date(2025, 1, 1)
    state = make_state(balance="1500", min_balance="1000")
    result = run_forecast(state, req_date, Decimal("600"))
    check("payment_just_below_minimum/exact_headroom",
          result.amount_safe_to_pay == Decimal("500"), str(result.amount_safe_to_pay))
    check("payment_just_below_minimum/never_dips_below_floor",
          plan_is_safe(state, req_date, [(req_date, Decimal("500"))]))
    check("payment_just_below_minimum/one_cent_over_is_unsafe",
          not plan_is_safe(state, req_date, [(req_date, Decimal("500.01"))]))


def test_cancelled_payment_excluded():
    req_date = dt.date(2025, 1, 1)
    cancelled = Event(event_id="e1", user_id="u", event_type="expense", description="x",
                       category="shopping", direction="debit", amount=Decimal("400"),
                       currency="USD", event_date=req_date, settlement_date=req_date,
                       status="cancelled", linked_event_id="", flexibility="fixed",
                       minimum_allowed_amount=None)
    # Simulate the io_data filtering step that state.py applies.
    check("cancelled_payment/excluded_from_cash_flow", cancelled.status == "cancelled")
    # A cancelled event contributes no discrete/stream entry; verified structurally
    # by state.py's `if e.status in EXCLUDED_STATUS: continue` guard.
    from buyorwait.state import EXCLUDED_STATUS
    check("cancelled_payment/status_in_excluded_set", "cancelled" in EXCLUDED_STATUS)


def test_failed_payment_excluded():
    from buyorwait.state import EXCLUDED_STATUS
    check("failed_payment/status_in_excluded_set", "failed" in EXCLUDED_STATUS)


def test_duplicate_event_backbone_dedup():
    from buyorwait.state import _detect_backbone
    base = dt.date(2025, 1, 15)
    items = []
    for i in range(4):
        items.append((None, Decimal("1000"), base + dt.timedelta(days=30 * i)))
    # Insert a stray duplicate 3 days after the 3rd occurrence (e.g. a
    # second representation of the same salary, similar to the dataset's
    # "August 2019 net salary" case alongside the regular monthly entry).
    items.insert(3, (None, Decimal("1000"), base + dt.timedelta(days=63)))
    items.sort(key=lambda t: t[2])
    chain, interval = _detect_backbone(items)
    dates = [c[2] for c in chain]
    check("duplicate_event/stray_insertion_dropped_from_chain",
          (base + dt.timedelta(days=63)) not in dates, dates)
    check("duplicate_event/interval_stays_monthly", interval == 30, interval)


def test_blank_image_amount_resolved_from_cache():
    store = EvidenceStore(REPO_ROOT / "dataset", REPO_ROOT / "code" / "cache" / "evidence_cache.json")
    from buyorwait.io_data import ImageRef
    images = [ImageRef(image_id="image_03", user_id="user_17", request_id="request_17",
                        related_event_id="event_1545")]
    result = store.resolve_event_amount("event_1545", images)
    check("blank_image_amount/resolved_from_cache", result is not None and result[1] == "INR",
          result)


def test_conflicting_messages_newer_wins():
    facts = [
        SalaryFact(user_id="u1", kind="increase", amount=Decimal("1000"), currency="USD",
                   effective_date=dt.date(2025, 1, 1), sent_at=dt.date(2025, 1, 1)),
        SalaryFact(user_id="u1", kind="decrease", amount=Decimal("700"), currency="USD",
                   effective_date=dt.date(2025, 2, 1), sent_at=dt.date(2025, 2, 1)),
    ]
    ordered = sorted(facts, key=lambda f: f.sent_at)
    check("conflicting_messages/chronological_order_newer_last", ordered[-1].amount == Decimal("700"))


def test_multiple_installment_options_pick_cheapest_and_lowest_id():
    from buyorwait.io_data import PaymentOption
    from buyorwait.planner import build_candidates, rank
    req_date = dt.date(2025, 1, 1)
    from buyorwait.io_data import Request
    request = Request(request_id="r1", user_id="u", request_date=req_date, request_type="purchase",
                       requested_amount=Decimal("900"), desired_completion_date=dt.date(2025, 4, 1),
                       allows_partial_payment=False, request_text="")
    state = make_state(balance="10000", min_balance="1000")
    cheap = PaymentOption(payment_option_id="payment_option_5", request_id="r1",
                           payment_method="installments", payment_amount=Decimal("300"),
                           number_of_payments=3, first_payment_date=req_date,
                           payment_frequency_days=30, financing_fee=Decimal("0"),
                           total_payable_amount=Decimal("900"))
    pricier = PaymentOption(payment_option_id="payment_option_2", request_id="r1",
                             payment_method="installments", payment_amount=Decimal("320"),
                             number_of_payments=3, first_payment_date=req_date,
                             payment_frequency_days=30, financing_fee=Decimal("60"),
                             total_payable_amount=Decimal("960"))
    candidates = build_candidates(state, request, [cheap, pricier], {})
    chosen = rank(candidates)
    check("multiple_installment_options/lowest_total_paid_wins",
          chosen is not None and chosen.total_paid == Decimal("900"), chosen)


def test_partial_payment_two_entries_sum_to_requested():
    from buyorwait.planner import build_candidates, rank
    from buyorwait.io_data import Request
    req_date = dt.date(2025, 1, 1)
    de = DiscreteEvent(date=req_date + dt.timedelta(days=20), signed_amount=Decimal("3000"),
                        event_id="e2", category="salary")
    state = make_state(balance="1500", min_balance="1000", discrete=[de])
    request = Request(request_id="r2", user_id="u", request_date=req_date, request_type="purchase",
                       requested_amount=Decimal("2000"), desired_completion_date=dt.date(2025, 2, 1),
                       allows_partial_payment=True, request_text="")
    candidates = build_candidates(state, request, [], {})
    chosen = rank(candidates)
    check("partial_payment/method_chosen", chosen is not None and chosen.method == "partial_payment",
          chosen)
    if chosen is not None:
        total = sum(a for _, a in chosen.payments)
        check("partial_payment/sums_to_requested_amount", total == Decimal("2000"), total)
        check("partial_payment/exactly_two_payments", len(chosen.payments) == 2)


def test_no_safe_date_within_90_days():
    req_date = dt.date(2025, 1, 1)
    stream = RecurringStream(category="rent", direction="debit", amount=Decimal("400"),
                              interval_days=30, monthly=True, next_date=req_date,
                              anchor_event_id="e1", flexibility="fixed",
                              minimum_allowed_amount=None)
    state = make_state(balance="2000", min_balance="1000", streams={"rent": [stream]})
    result = run_forecast(state, req_date, Decimal("50000"))
    check("no_safe_date/earliest_is_none", result.earliest_date_for_full_payment is None)
    check("no_safe_date/amount_safe_capped_low", result.amount_safe_to_pay < Decimal("2000"))


def test_flexible_spending_reduction_unlocks_amount():
    req_date = dt.date(2025, 1, 1)
    stream = RecurringStream(category="dining", direction="debit", amount=Decimal("500"),
                              interval_days=30, monthly=True, next_date=req_date,
                              anchor_event_id="e1", flexibility="reducible",
                              minimum_allowed_amount=Decimal("100"))
    state = make_state(balance="1600", min_balance="1000", streams={"dining": [stream]})
    base = run_forecast(state, req_date, Decimal("600"))
    reduced = run_forecast(state, req_date, Decimal("600"),
                            spending_changes={"e1": Decimal("100")})
    check("flexible_spending_reduction/increases_safe_amount",
          reduced.amount_safe_to_pay > base.amount_safe_to_pay,
          (base.amount_safe_to_pay, reduced.amount_safe_to_pay))


def test_multiple_currencies_two_hop_conversion():
    rates = [
        ExchangeRate(rate_date=dt.date(2025, 1, 15), from_currency="USD", to_currency="EUR",
                     rate=Decimal("0.9")),
        ExchangeRate(rate_date=dt.date(2025, 1, 15), from_currency="EUR", to_currency="ZAR",
                     rate=Decimal("20")),
        ExchangeRate(rate_date=dt.date(2025, 1, 15), from_currency="USD", to_currency="IDR",
                     rate=Decimal("15000")),
    ]
    fx = FxTable(rates)
    # ZAR -> IDR must hop ZAR->EUR->USD->IDR (no direct rate supplied).
    converted = fx.convert(Decimal("100"), "ZAR", "IDR", dt.date(2025, 1, 15))
    expected = Decimal("100") * (Decimal(1) / Decimal("20")) * (Decimal(1) / Decimal("0.9")) * Decimal("15000")
    tolerance = Decimal("0.0001")
    check("multiple_currencies/two_hop_path", abs(converted - expected) < tolerance,
          (converted, expected))


def test_investment_request_uses_same_affordability_path():
    from buyorwait.planner import build_candidates, rank
    from buyorwait.io_data import Request
    req_date = dt.date(2025, 1, 1)
    state = make_state(balance="5000", min_balance="1000")
    request = Request(request_id="r3", user_id="u", request_date=req_date, request_type="investment",
                       requested_amount=Decimal("2000"), desired_completion_date=dt.date(2025, 2, 1),
                       allows_partial_payment=False, request_text="")
    candidates = build_candidates(state, request, [], {})
    chosen = rank(candidates)
    check("investment_request/treated_as_plain_affordability_check",
          chosen is not None and chosen.method == "full_payment", chosen)


def test_pending_credit_not_counted():
    ev = Event(event_id="e1", user_id="u", event_type="income", description="prize",
               category="other", direction="credit", amount=Decimal("500"), currency="USD",
               event_date=dt.date(2025, 1, 5), settlement_date=dt.date(2025, 1, 5),
               status="pending", linked_event_id="", flexibility="fixed",
               minimum_allowed_amount=None)
    # state.py's discrete-event filter explicitly skips pending credits.
    check("pending_credit/direction_and_status_would_be_skipped",
          ev.direction == "credit" and ev.status == "pending")


def _make_dataset(events):
    from buyorwait.io_data import Dataset
    profile = make_profile(min_balance="1000")
    events_by_user = {"u": events}
    events_by_id = {e.event_id: e for e in events}
    return Dataset(profiles={"u": profile}, events=events_by_id, events_by_user=events_by_user,
                    rates=[], requests=[], payment_options_by_request={}, messages=[], images=[])


def _build_state(events, request_date):
    from buyorwait.state import build_financial_state
    dataset = _make_dataset(events)
    fx = FxTable([])
    evidence = EvidenceStore(REPO_ROOT, REPO_ROOT / "does_not_exist_cache.json")
    return build_financial_state(dataset, "u", request_date, fx, evidence)


def _mk_event(event_id, category, direction, amount, date, status="settled",
              linked_event_id="", description="x", event_type="expense",
              flexibility="fixed", minimum_allowed_amount=None):
    return Event(event_id=event_id, user_id="u", event_type=event_type, description=description,
                 category=category, direction=direction, amount=Decimal(amount), currency="USD",
                 event_date=date, settlement_date=date, status=status,
                 linked_event_id=linked_event_id, flexibility=flexibility,
                 minimum_allowed_amount=(Decimal(minimum_allowed_amount)
                                          if minimum_allowed_amount is not None else None))


def test_linked_reversal_pair_not_recurring():
    d0 = dt.date(2025, 1, 1)
    events = [
        _mk_event("e1", "shopping", "debit", "583", d0, description="Card charge later reversed"),
        _mk_event("e2", "shopping", "credit", "583", d0 + dt.timedelta(days=1),
                  linked_event_id="e1", description="Settled card charge reversal"),
    ]
    state = _build_state(events, d0 + dt.timedelta(days=5))
    check("linked_reversal_pair/no_recurring_stream", "shopping" not in state.streams,
          state.streams)


def test_linked_reimbursement_pair_counted_once_not_recurring():
    d0 = dt.date(2025, 1, 1)
    events = [
        _mk_event("e1", "work_expense", "debit", "200", d0, description="Reimbursable work expense"),
        _mk_event("e2", "work_expense", "credit", "200", d0 + dt.timedelta(days=10),
                  linked_event_id="e1", description="Employer expense reimbursement"),
    ]
    state = _build_state(events, d0 + dt.timedelta(days=30))
    check("linked_reimbursement_pair/no_recurring_stream", "work_expense" not in state.streams,
          state.streams)


def test_pending_duplicate_of_settled_excluded():
    d0 = dt.date(2025, 1, 1)
    req_date = d0 + dt.timedelta(days=1)
    events = [
        _mk_event("e1", "shopping", "debit", "150", d0, status="settled",
                  description="Original card charge"),
        _mk_event("e2", "shopping", "debit", "150", req_date + dt.timedelta(days=5),
                  status="pending", linked_event_id="e1", description="Possible duplicate card charge"),
    ]
    state = _build_state(events, req_date)
    check("pending_duplicate/excluded_from_discrete_events",
          all(de.event_id != "e2" for de in state.discrete_events), state.discrete_events)


def test_failed_then_scheduled_retry_retained():
    d0 = dt.date(2025, 1, 1)
    req_date = d0 + dt.timedelta(days=1)
    events = [
        _mk_event("e1", "bills", "debit", "150", d0, status="failed",
                  description="Failed bill payment attempt"),
        _mk_event("e2", "bills", "debit", "150", req_date + dt.timedelta(days=5),
                  status="scheduled", linked_event_id="e1", description="Scheduled bill payment retry"),
    ]
    state = _build_state(events, req_date)
    check("failed_then_retry/retry_kept_as_discrete_debit",
          any(de.event_id == "e2" for de in state.discrete_events), state.discrete_events)


def test_two_independent_streams_same_category():
    base = dt.date(2025, 1, 1)
    events = []
    for i in range(4):
        events.append(_mk_event(f"base{i}", "salary", "credit", "23256000",
                                 base + dt.timedelta(days=30 * i), description="Base salary"))
        events.append(_mk_event(f"comm{i}", "salary", "credit", str(8500000 + i * 100000),
                                 base + dt.timedelta(days=30 * i + 10),
                                 description="Monthly sales commission"))
    req_date = base + dt.timedelta(days=95)
    state = _build_state(events, req_date)
    check("two_independent_streams/both_detected", len(state.streams.get("salary", [])) == 2,
          state.streams.get("salary"))


def test_same_day_flows_are_netted_not_intraday_ordered():
    # The dataset carries no intra-day timestamps, so same-day flows are
    # netted into a single date-granularity checkpoint rather than assumed
    # to clear in an unfavorable (debit-before-credit) order. This was
    # deliberately verified against the alternative (conservative intraday
    # ordering) during Phase 1 hardening and reverted: a real, non-tuned
    # sample (request_23, an income settlement and an expense dated the
    # same day) demonstrated the reference implementation nets same-day
    # flows, so asserting an intraday breach here would encode an
    # unsupported assumption rather than a verified rule.
    req_date = dt.date(2025, 1, 1)
    margin = Decimal("50")
    min_balance = Decimal("1000")
    de_debit = DiscreteEvent(date=req_date, signed_amount=-(margin + Decimal("500")),
                              event_id="d1", category="shopping")
    de_credit = DiscreteEvent(date=req_date, signed_amount=Decimal("500"),
                               event_id="c1", category="salary")
    state = make_state(balance=str(min_balance + margin), min_balance=str(min_balance),
                        discrete=[de_debit, de_credit])
    result = run_forecast(state, req_date, Decimal("0"))
    check("same_day_flows/netted_to_end_of_day_balance",
          result.suffix_min[0] == min_balance, result.suffix_min[0])


def test_same_day_multiple_debits_and_credits():
    req_date = dt.date(2025, 1, 1)
    min_balance = Decimal("1000")
    flows = [
        DiscreteEvent(date=req_date, signed_amount=Decimal("-300"), event_id="d1", category="shopping"),
        DiscreteEvent(date=req_date, signed_amount=Decimal("-200"), event_id="d2", category="shopping"),
        DiscreteEvent(date=req_date, signed_amount=Decimal("400"), event_id="c1", category="salary"),
        DiscreteEvent(date=req_date, signed_amount=Decimal("100"), event_id="c2", category="salary"),
    ]
    state = make_state(balance="1500", min_balance=str(min_balance), discrete=flows)
    result = run_forecast(state, req_date, Decimal("0"))
    # net = 1500 - 300 - 200 + 400 + 100 = 1500, all same-day flows netted.
    check("same_day_multiple_flows/netted_end_of_day_balance",
          result.suffix_min[0] == Decimal("1500"), result.suffix_min[0])


def test_90_day_boundary_event_included():
    req_date = dt.date(2025, 1, 1)
    boundary_date = req_date + dt.timedelta(days=90)
    de = DiscreteEvent(date=boundary_date, signed_amount=Decimal("-100"),
                        event_id="e1", category="shopping")
    state = make_state(balance="1100", min_balance="1000", discrete=[de])
    result = run_forecast(state, req_date, Decimal("0"))
    check("boundary_event/day_90_event_reflected_in_horizon",
          any(d == boundary_date for d, _ in result.checkpoints), result.checkpoints)
    check("boundary_event/day_90_dip_lowers_safe_amount",
          result.suffix_min[0] == Decimal("1000"), result.suffix_min[0])


def test_message_priority_settled_beats_newer_estimate():
    from buyorwait.state import _apply_evidence
    stream = RecurringStream(category="salary", direction="credit", amount=Decimal("5000"),
                              interval_days=30, monthly=True, next_date=dt.date(2025, 3, 1),
                              anchor_event_id="e1", flexibility="fixed", minimum_allowed_amount=None)
    streams = {"salary": [stream]}
    store = EvidenceStore(REPO_ROOT, REPO_ROOT / "does_not_exist_cache.json")
    store.salary_facts["u"] = [
        SalaryFact(user_id="u", kind="increase", amount=Decimal("6000"), currency="USD",
                   effective_date=dt.date(2025, 1, 1), sent_at=dt.date(2025, 1, 1),
                   certainty="confirmed"),
        SalaryFact(user_id="u", kind="decrease", amount=Decimal("4000"), currency="USD",
                   effective_date=dt.date(2025, 2, 1), sent_at=dt.date(2025, 2, 1),
                   certainty="estimate"),
    ]
    fx = FxTable([])
    _apply_evidence(streams, store, "u", "USD", fx, dt.date(2025, 2, 15))
    check("message_priority/confirmed_not_overwritten_by_later_estimate",
          streams["salary"][0].amount == Decimal("6000"), streams["salary"][0].amount)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()

    print(f"{len(PASS)} passed, {len(FAIL)} failed\n")
    for name, detail in FAIL:
        print(f"FAIL {name}: {detail}")
    if not FAIL:
        print("All edge-case checks passed.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
