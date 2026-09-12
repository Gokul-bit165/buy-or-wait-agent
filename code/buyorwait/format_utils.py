"""Amount formatting and explanation templates."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple


def format_amount(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == q.to_integral_value():
        return str(int(q))
    s = f"{q:.2f}"
    return s


def format_date(d: dt.date) -> str:
    return d.isoformat()


def format_payment_plan(payments: List[Tuple[dt.date, Decimal]]) -> str:
    if not payments:
        return "none"
    return "|".join(f"{format_date(d)}:{format_amount(a)}" for d, a in payments)


def format_spending_changes(actions: List[dict]) -> str:
    if not actions:
        return "none"
    stops = sorted([a for a in actions if a["kind"] == "stop"], key=lambda a: a["event_id"])
    reduces = sorted([a for a in actions if a["kind"] == "reduce"], key=lambda a: a["event_id"])
    parts = [f"stop:{a['event_id']}" for a in stops]
    parts += [f"reduce_to:{a['event_id']}:{format_amount(a['new_amount'])}" for a in reduces]
    return "|".join(parts)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def spending_change_phrase(actions: List[dict]) -> str:
    stops = sorted([a for a in actions if a["kind"] == "stop"], key=lambda a: a["event_id"])
    reduces = sorted([a for a in actions if a["kind"] == "reduce"], key=lambda a: a["event_id"])
    phrases = [f"Stop the {_lower_first(a['description'])}" for a in stops]
    phrases += [f"reduce the {_lower_first(a['description'])} to {format_amount(a['new_amount'])}"
                for a in reduces]
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    return " and ".join(phrases)


def explain_full_payment_now(currency: str, amount: Decimal, min_balance: Decimal) -> str:
    return (f"Pay {currency} {format_amount(amount)} today. This leaves at least "
            f"{currency} {format_amount(min_balance)} available over the next 90 days.")


def explain_full_payment_with_changes(currency: str, amount: Decimal, min_balance: Decimal,
                                       actions: List[dict]) -> str:
    phrase = spending_change_phrase(actions)
    return (f"{phrase}, then pay {currency} {format_amount(amount)} today. This leaves at "
            f"least {currency} {format_amount(min_balance)} available.")


def explain_installments(currency: str, n: int, amount: Decimal, start: dt.date,
                          min_balance: Decimal) -> str:
    return (f"Use {n} installments of {currency} {format_amount(amount)}, starting "
            f"{format_date(start)}. "
            f"This leaves at least {currency} {format_amount(min_balance)} available.")


def explain_partial(currency: str, amt1: Decimal, amt2: Decimal, date2: dt.date,
                     min_balance: Decimal) -> str:
    return (f"Pay {currency} {format_amount(amt1)} today and the remaining {currency} "
            f"{format_amount(amt2)} on {format_date(date2)}. This completes the full "
            f"request and keeps the {currency} {format_amount(min_balance)} minimum protected.")


def explain_wait(currency: str, amount: Decimal, date: dt.date, min_balance: Decimal) -> str:
    return (f"Pay {currency} {format_amount(amount)} in full on {format_date(date)}. "
            f"Paying earlier would take the balance below the {currency} "
            f"{format_amount(min_balance)} minimum.")


def explain_not_recommended(currency: str, requested: Decimal, safe: Decimal) -> str:
    return (f"Do not proceed with the {currency} {format_amount(requested)} request. "
            f"Although {currency} {format_amount(safe)} is available today, the full "
            f"amount cannot be completed safely within 90 days.")
