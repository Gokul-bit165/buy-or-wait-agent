"""Candidate plan generation, spending-change search, and the exact
ranking order from problem_statement.md §Choosing Between Safe Plans."""
from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .forecast import ForecastResult, plan_is_safe, run_forecast
from .io_data import PaymentOption, Request
from .state import FinancialState

MAX_SPENDING_CHANGES = 3


@dataclass
class Candidate:
    method: str  # full_payment | partial_payment | installments | wait | not_recommended
    payments: List[Tuple[dt.date, Decimal]]
    total_paid: Decimal
    completion_date: Optional[dt.date]
    spending_actions: List[dict]
    payment_option_id: Optional[str]
    deadline_met: bool


def _eligible_spending_actions(state: FinancialState) -> List[dict]:
    profile = state.profile
    actions = []
    for category, stream_list in state.streams.items():
        if category in profile.protect:
            continue
        for stream in stream_list:
            if stream.flexibility == "fixed":
                continue
            can_reduce = (stream.flexibility in ("reducible", "reducible_or_stoppable")
                          and category in profile.willing_reduce
                          and stream.minimum_allowed_amount is not None
                          and stream.minimum_allowed_amount < stream.amount)
            can_stop = (stream.flexibility in ("stoppable", "reducible_or_stoppable")
                        and category in profile.willing_stop)
            if can_reduce:
                actions.append({"event_id": stream.anchor_event_id, "category": category,
                                 "kind": "reduce", "new_amount": stream.minimum_allowed_amount,
                                 "description": _describe(stream)})
            if can_stop:
                actions.append({"event_id": stream.anchor_event_id, "category": category,
                                 "kind": "stop", "new_amount": Decimal(0),
                                 "description": _describe(stream)})
    # Deterministic order: reduce variants before stop variants, by category.
    actions.sort(key=lambda a: (a["category"], 0 if a["kind"] == "reduce" else 1))
    return actions


def _describe(stream) -> str:
    # Human phrase derived from category; overridden by caller with the
    # actual event description when available.
    return stream.category.replace("_", " ")


def find_minimal_spending_combo(state: FinancialState, request: Request,
                                 event_descriptions: Dict[str, str]
                                 ) -> Optional[Tuple[List[dict], ForecastResult]]:
    actions = _eligible_spending_actions(state)
    for a in actions:
        a["description"] = event_descriptions.get(a["event_id"], a["description"])
    for size in range(1, MAX_SPENDING_CHANGES + 1):
        for combo in itertools.combinations(actions, size):
            event_ids = [a["event_id"] for a in combo]
            if len(set(event_ids)) != len(event_ids):
                continue
            changes = {a["event_id"]: (a["new_amount"] if a["kind"] == "reduce" else "stop")
                       for a in combo}
            result = run_forecast(state, request.request_date, request.requested_amount,
                                   spending_changes=changes)
            if result.amount_safe_to_pay >= request.requested_amount:
                return list(combo), result
    return None


def build_candidates(state: FinancialState, request: Request,
                      options: List[PaymentOption],
                      event_descriptions: Dict[str, str]) -> List[Candidate]:
    profile = state.profile
    base = run_forecast(state, request.request_date, request.requested_amount)
    candidates: List[Candidate] = []

    # full_payment, no spending changes
    if "full_payment" in profile.payment_methods and base.amount_safe_to_pay >= request.requested_amount:
        payments = [(request.request_date, request.requested_amount)]
        candidates.append(Candidate(
            method="full_payment", payments=payments, total_paid=request.requested_amount,
            completion_date=request.request_date, spending_actions=[],
            payment_option_id=None, deadline_met=request.request_date <= request.desired_completion_date,
        ))
    elif "full_payment" in profile.payment_methods:
        combo_result = find_minimal_spending_combo(state, request, event_descriptions)
        if combo_result is not None:
            combo, _ = combo_result
            payments = [(request.request_date, request.requested_amount)]
            candidates.append(Candidate(
                method="full_payment", payments=payments, total_paid=request.requested_amount,
                completion_date=request.request_date, spending_actions=combo,
                payment_option_id=None,
                deadline_met=request.request_date <= request.desired_completion_date,
            ))

    # installments
    if "installments" in profile.payment_methods and profile.max_installment_months:
        for opt in options:
            if opt.payment_method != "installments":
                continue
            if opt.number_of_payments > profile.max_installment_months:
                continue
            payments = []
            d = opt.first_payment_date
            for i in range(opt.number_of_payments):
                payments.append((d, opt.payment_amount))
                if opt.payment_frequency_days:
                    d = d + dt.timedelta(days=opt.payment_frequency_days)
            if plan_is_safe(state, request.request_date, payments):
                completion = payments[-1][0]
                candidates.append(Candidate(
                    method="installments", payments=payments, total_paid=opt.total_payable_amount,
                    completion_date=completion, spending_actions=[],
                    payment_option_id=opt.payment_option_id,
                    deadline_met=completion <= request.desired_completion_date,
                ))

    # partial_payment
    if (request.allows_partial_payment and "partial_payment" in profile.payment_methods
            and 0 < base.amount_safe_to_pay < request.requested_amount
            and base.earliest_date_for_full_payment is not None
            and base.earliest_date_for_full_payment <= request.desired_completion_date):
        remainder = request.requested_amount - base.amount_safe_to_pay
        payments = [(request.request_date, base.amount_safe_to_pay),
                    (base.earliest_date_for_full_payment, remainder)]
        candidates.append(Candidate(
            method="partial_payment", payments=payments, total_paid=request.requested_amount,
            completion_date=base.earliest_date_for_full_payment, spending_actions=[],
            payment_option_id=None, deadline_met=True,
        ))

    # wait
    if ("full_payment" in profile.payment_methods
            and base.earliest_date_for_full_payment is not None
            and base.earliest_date_for_full_payment > request.request_date):
        payments = [(base.earliest_date_for_full_payment, request.requested_amount)]
        candidates.append(Candidate(
            method="wait", payments=payments, total_paid=request.requested_amount,
            completion_date=base.earliest_date_for_full_payment, spending_actions=[],
            payment_option_id=None,
            deadline_met=base.earliest_date_for_full_payment <= request.desired_completion_date,
        ))

    return candidates


def rank(candidates: List[Candidate]) -> Optional[Candidate]:
    if not candidates:
        return None

    def option_num(opt_id: Optional[str]) -> int:
        if not opt_id:
            return -1
        try:
            return int(opt_id.rsplit("_", 1)[-1])
        except ValueError:
            return 10 ** 9

    def key(c: Candidate):
        return (
            0 if c.deadline_met else 1,
            0 if not c.spending_actions else 1,
            c.total_paid,
            c.payments[0][0] if c.payments else dt.date.max,
            len(c.payments),
            option_num(c.payment_option_id),
        )

    return sorted(candidates, key=key)[0]
