"""Deterministic pre-write validation for a completed output row."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import List, Optional

VALID_STATUS = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
VALID_METHOD = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}


class ValidationError(Exception):
    pass


def validate_row(row: dict, requested_amount: Decimal) -> List[str]:
    errors = []
    amt = Decimal(str(row["amount_safe_to_pay"]))
    if not (Decimal(0) <= amt <= requested_amount):
        errors.append(f"amount_safe_to_pay {amt} out of bounds [0, {requested_amount}]")
    if row["affordability_status"] not in VALID_STATUS:
        errors.append(f"invalid affordability_status {row['affordability_status']}")
    if row["recommended_payment_method"] not in VALID_METHOD:
        errors.append(f"invalid recommended_payment_method {row['recommended_payment_method']}")

    plan = row["payment_plan"]
    if plan != "none":
        entries = plan.split("|")
        dates = []
        total = Decimal(0)
        for entry in entries:
            date_str, amount_str = entry.split(":")
            dates.append(dt.date.fromisoformat(date_str))
            total += Decimal(amount_str)
        if dates != sorted(dates):
            errors.append("payment_plan dates not chronological")
        if row["recommended_payment_method"] == "partial_payment":
            if len(entries) != 2:
                errors.append("partial_payment must have exactly 2 payments")
            if total != requested_amount:
                errors.append(f"partial_payment total {total} != requested_amount {requested_amount}")

    sc = row["spending_changes_needed"]
    if sc != "none":
        seen_events = set()
        for entry in sc.split("|"):
            parts = entry.split(":")
            kind = parts[0]
            event_id = parts[1]
            if event_id in seen_events:
                errors.append(f"event {event_id} referenced by more than one spending change")
            seen_events.add(event_id)
            if kind not in ("stop", "reduce_to"):
                errors.append(f"invalid spending change kind {kind}")
        if len(sc.split("|")) > 3:
            errors.append("more than 3 spending changes")

    return errors
