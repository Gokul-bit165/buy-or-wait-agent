"""Deterministic 90-day cash-flow forecast, safe-amount and earliest-date
calculation, and safety checking for a specific candidate plan."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .state import FinancialState

# The forecast horizon is request_date .. request_date + HORIZON_DAYS,
# INCLUSIVE of both endpoints (91 distinct calendar checkpoints when events
# land on every day). This is the one interpretation of "the next 90 days"
# implemented here; a request-day event is always included, since a payment
# made on request_date must itself be validated against the same horizon.
HORIZON_DAYS = 90


def _stream_occurrences(state: FinancialState, request_date: dt.date,
                         horizon_end: dt.date,
                         spending_changes: Optional[Dict[str, object]] = None
                         ) -> List[Tuple[dt.date, Decimal]]:
    spending_changes = spending_changes or {}
    occurrences: List[Tuple[dt.date, Decimal]] = []
    discrete_dates_by_category: Dict[str, List[dt.date]] = {}
    for de in state.discrete_events:
        discrete_dates_by_category.setdefault(de.category, []).append(de.date)

    for category, stream_list in state.streams.items():
        nearby = discrete_dates_by_category.get(category, [])
        for stream in stream_list:
            override = spending_changes.get(stream.anchor_event_id)
            amount = stream.amount
            if override == "stop":
                amount = Decimal(0)
            elif isinstance(override, Decimal):
                amount = override

            date = stream.next_date
            while date <= horizon_end:
                if stream.end_date is not None and date > stream.end_date:
                    break
                if date >= request_date:
                    skip = any(abs((date - d).days) <= 5 for d in nearby)
                    if not skip and amount != 0:
                        signed = amount if stream.direction == "credit" else -amount
                        occurrences.append((date, signed))
                date = stream.advance(date)
    return occurrences


def _checkpoints(state: FinancialState, request_date: dt.date,
                  spending_changes: Optional[Dict[str, object]] = None,
                  extra_flows: Optional[List[Tuple[dt.date, Decimal]]] = None
                  ) -> List[Tuple[dt.date, Decimal]]:
    """Returns sorted (date, cumulative_balance) checkpoints from
    request_date to request_date+HORIZON_DAYS inclusive, always including a
    checkpoint at request_date itself. Same-day flows are netted before the
    checkpoint is computed (date-granularity accounting): this was
    deliberately re-verified against a debit-before-credit conservative
    ordering during Phase 1 hardening (see engineering_report.md, "same-day
    event ordering") and reverted after concrete evidence from a real,
    non-tuned sample (request_23: an income settlement and an expense dated
    the same day) showed the reference implementation nets same-day flows
    rather than assuming an unfavorable intraday clearing order. The dataset
    provides no intra-day timestamps, so there is no ordering signal to
    apply conservatism to beyond what "counted on settlement date" already
    states: a date's flows are all available as of that date.
    """
    horizon_end = request_date + dt.timedelta(days=HORIZON_DAYS)
    flows: List[Tuple[dt.date, Decimal]] = []
    for de in state.discrete_events:
        if request_date <= de.date <= horizon_end:
            flows.append((de.date, de.signed_amount))
    flows.extend(_stream_occurrences(state, request_date, horizon_end, spending_changes))
    if extra_flows:
        flows.extend([(d, a) for d, a in extra_flows if request_date <= d <= horizon_end])

    by_date: Dict[dt.date, Decimal] = {request_date: Decimal(0)}
    for date, amount in flows:
        by_date[date] = by_date.get(date, Decimal(0)) + amount

    dates = sorted(by_date.keys())
    checkpoints = []
    running = state.starting_balance
    for d in dates:
        running = running + by_date[d]
        checkpoints.append((d, running))
    return checkpoints


def _suffix_min(checkpoints: List[Tuple[dt.date, Decimal]]) -> List[Decimal]:
    n = len(checkpoints)
    out = [Decimal(0)] * n
    running_min = None
    for i in range(n - 1, -1, -1):
        bal = checkpoints[i][1]
        running_min = bal if running_min is None else min(running_min, bal)
        out[i] = running_min
    return out


@dataclass
class ForecastResult:
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: Optional[dt.date]
    checkpoints: List[Tuple[dt.date, Decimal]]
    suffix_min: List[Decimal]


def run_forecast(state: FinancialState, request_date: dt.date, requested_amount: Decimal,
                  spending_changes: Optional[Dict[str, object]] = None) -> ForecastResult:
    checkpoints = _checkpoints(state, request_date, spending_changes=spending_changes)
    smin = _suffix_min(checkpoints)
    min_bal = state.profile.minimum_balance_to_keep

    def max_safe_at(idx: int) -> Decimal:
        return smin[idx] - min_bal

    x0 = max_safe_at(0)
    amount_safe_to_pay = max(Decimal(0), min(requested_amount, x0))

    earliest = None
    for i, (d, _) in enumerate(checkpoints):
        if max_safe_at(i) >= requested_amount:
            earliest = d
            break

    return ForecastResult(
        amount_safe_to_pay=amount_safe_to_pay,
        earliest_date_for_full_payment=earliest,
        checkpoints=checkpoints,
        suffix_min=smin,
    )


def plan_is_safe(state: FinancialState, request_date: dt.date,
                  payments: List[Tuple[dt.date, Decimal]],
                  spending_changes: Optional[Dict[str, object]] = None) -> bool:
    """Re-simulate with the plan's own outflows (as negative signed amounts)
    injected on top of the baseline cash flow and check the minimum-balance
    constraint holds at every checkpoint."""
    extra = [(d, -amt) for d, amt in payments]
    checkpoints = _checkpoints(state, request_date, spending_changes=spending_changes,
                                extra_flows=extra)
    min_bal = state.profile.minimum_balance_to_keep
    return all(bal >= min_bal for _, bal in checkpoints)
