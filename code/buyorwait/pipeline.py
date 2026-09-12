"""Orchestrates one request -> one output row."""
from __future__ import annotations

from decimal import Decimal
from typing import Dict

from . import format_utils as fmt
from .evidence import EvidenceStore
from .forecast import run_forecast
from .fx import FxTable
from .io_data import Dataset, Request
from .planner import build_candidates, rank
from .state import build_financial_state


def process_request(dataset: Dataset, request: Request, fx: FxTable,
                     evidence: EvidenceStore) -> dict:
    profile = dataset.profiles[request.user_id]
    home = profile.home_currency
    state = build_financial_state(dataset, request.user_id, request.request_date, fx, evidence)

    base = run_forecast(state, request.request_date, request.requested_amount)

    event_descriptions: Dict[str, str] = {
        e.event_id: e.description for e in dataset.events_by_user.get(request.user_id, [])
    }
    options = dataset.payment_options_by_request.get(request.request_id, [])
    candidates = build_candidates(state, request, options, event_descriptions)
    chosen = rank(candidates)

    amount_safe_to_pay = base.amount_safe_to_pay
    earliest = base.earliest_date_for_full_payment

    if chosen is None:
        return {
            "request_id": request.request_id,
            "amount_safe_to_pay": fmt.format_amount(amount_safe_to_pay),
            "affordability_status": "not_affordable",
            "recommended_payment_method": "not_recommended",
            "payment_plan": "none",
            "earliest_date_for_full_payment": fmt.format_date(earliest) if earliest else "",
            "spending_changes_needed": "none",
            "decision_explanation": fmt.explain_not_recommended(
                home, request.requested_amount, amount_safe_to_pay),
        }

    if chosen.method == "full_payment" and not chosen.spending_actions:
        status = "affordable_now"
    elif chosen.method in ("full_payment", "partial_payment", "installments"):
        status = "affordable_with_plan"
    elif chosen.method == "wait":
        status = "affordable_later"
    else:
        status = "not_affordable"

    min_balance = profile.minimum_balance_to_keep

    if chosen.method == "full_payment" and not chosen.spending_actions:
        explanation = fmt.explain_full_payment_now(home, request.requested_amount, min_balance)
    elif chosen.method == "full_payment":
        explanation = fmt.explain_full_payment_with_changes(
            home, request.requested_amount, min_balance, chosen.spending_actions)
    elif chosen.method == "installments":
        n = len(chosen.payments)
        per_amount = chosen.payments[0][1]
        explanation = fmt.explain_installments(home, n, per_amount, chosen.payments[0][0],
                                                min_balance)
    elif chosen.method == "partial_payment":
        explanation = fmt.explain_partial(home, chosen.payments[0][1], chosen.payments[1][1],
                                           chosen.payments[1][0], min_balance)
    elif chosen.method == "wait":
        explanation = fmt.explain_wait(home, request.requested_amount, chosen.payments[0][0],
                                        min_balance)
    else:
        explanation = fmt.explain_not_recommended(home, request.requested_amount, amount_safe_to_pay)

    return {
        "request_id": request.request_id,
        "amount_safe_to_pay": fmt.format_amount(amount_safe_to_pay),
        "affordability_status": status,
        "recommended_payment_method": chosen.method,
        "payment_plan": fmt.format_payment_plan(chosen.payments),
        "earliest_date_for_full_payment": fmt.format_date(earliest) if earliest else "",
        "spending_changes_needed": fmt.format_spending_changes(chosen.spending_actions),
        "decision_explanation": explanation,
    }
