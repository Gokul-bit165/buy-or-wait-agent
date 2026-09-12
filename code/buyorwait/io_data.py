"""Typed CSV loading for the Buy or Wait? dataset."""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional


def _dec(value: str) -> Optional[Decimal]:
    value = (value or "").strip()
    if value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _date(value: str) -> Optional[dt.date]:
    value = (value or "").strip()
    if value == "":
        return None
    return dt.date.fromisoformat(value[:10])


def _bool(value: str) -> bool:
    return (value or "").strip().lower() == "true"


@dataclass
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: list
    protect: set
    willing_reduce: set
    willing_stop: set
    payment_methods: set
    max_installment_months: Optional[int]


@dataclass
class Event:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Optional[Decimal]
    currency: str
    event_date: Optional[dt.date]
    settlement_date: Optional[dt.date]
    status: str
    linked_event_id: str
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]
    amount_resolved: bool = False
    amount_source: str = "csv"


@dataclass
class ExchangeRate:
    rate_date: dt.date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: dt.date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: dt.date
    allows_partial_payment: bool
    request_text: str


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: Optional[dt.date]
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass
class Message:
    message_id: str
    user_id: str
    request_id: str
    related_event_id: str
    sent_at: str
    source_type: str
    message_text: str


@dataclass
class ImageRef:
    image_id: str
    user_id: str
    request_id: str
    related_event_id: str


@dataclass
class Dataset:
    profiles: dict
    events: dict
    events_by_user: dict
    rates: list
    requests: list
    payment_options_by_request: dict
    messages: list
    images: list


def _split_pipe(value: str) -> list:
    value = (value or "").strip()
    if value == "":
        return []
    return [v.strip() for v in value.split("|") if v.strip()]


def load_dataset(dataset_dir: Path) -> Dataset:
    profiles = {}
    with open(dataset_dir / "financial_profiles.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mim = (row["max_installment_months"] or "").strip()
            profiles[row["user_id"]] = Profile(
                user_id=row["user_id"],
                home_currency=row["home_currency"],
                current_available_balance=_dec(row["current_available_balance"]) or Decimal(0),
                minimum_balance_to_keep=_dec(row["minimum_balance_to_keep"]) or Decimal(0),
                financial_priorities=_split_pipe(row["financial_priorities"]),
                protect=set(_split_pipe(row["expense_categories_to_protect"])),
                willing_reduce=set(_split_pipe(row["expense_categories_user_is_willing_to_reduce"])),
                willing_stop=set(_split_pipe(row["expense_categories_user_is_willing_to_stop"])),
                payment_methods=set(_split_pipe(row["payment_methods_user_will_consider"])),
                max_installment_months=int(mim) if mim else None,
            )

    events = {}
    events_by_user = {}
    with open(dataset_dir / "financial_events.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ev = Event(
                event_id=row["event_id"],
                user_id=row["user_id"],
                event_type=row["event_type"],
                description=row["description"],
                category=row["category"],
                direction=row["direction"],
                amount=_dec(row["amount"]),
                currency=row["currency"],
                event_date=_date(row["event_date"]),
                settlement_date=_date(row["settlement_date"]),
                status=row["status"],
                linked_event_id=row["linked_event_id"],
                flexibility=row["flexibility"],
                minimum_allowed_amount=_dec(row["minimum_allowed_amount"]),
            )
            events[ev.event_id] = ev
            events_by_user.setdefault(ev.user_id, []).append(ev)

    for lst in events_by_user.values():
        lst.sort(key=lambda e: e.settlement_date or e.event_date or dt.date.min)

    rates = []
    with open(dataset_dir / "exchange_rates.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rates.append(ExchangeRate(
                rate_date=_date(row["rate_date"]),
                from_currency=row["from_currency"],
                to_currency=row["to_currency"],
                rate=_dec(row["rate"]),
            ))

    requests = []
    with open(dataset_dir / "requests.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            requests.append(Request(
                request_id=row["request_id"],
                user_id=row["user_id"],
                request_date=_date(row["request_date"]),
                request_type=row["request_type"],
                requested_amount=_dec(row["requested_amount"]) or Decimal(0),
                desired_completion_date=_date(row["desired_completion_date"]),
                allows_partial_payment=_bool(row["allows_partial_payment"]),
                request_text=row["request_text"],
            ))

    payment_options_by_request = {}
    with open(dataset_dir / "request_payment_options.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            freq = (row["payment_frequency_days"] or "").strip()
            opt = PaymentOption(
                payment_option_id=row["payment_option_id"],
                request_id=row["request_id"],
                payment_method=row["payment_method"],
                payment_amount=_dec(row["payment_amount"]) or Decimal(0),
                number_of_payments=int(row["number_of_payments"]),
                first_payment_date=_date(row["first_payment_date"]),
                payment_frequency_days=int(freq) if freq else None,
                financing_fee=_dec(row["financing_fee"]) or Decimal(0),
                total_payable_amount=_dec(row["total_payable_amount"]) or Decimal(0),
            )
            payment_options_by_request.setdefault(opt.request_id, []).append(opt)

    messages = []
    with open(dataset_dir / "messages.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            messages.append(Message(
                message_id=row["message_id"],
                user_id=row["user_id"],
                request_id=row["request_id"],
                related_event_id=row["related_event_id"],
                sent_at=row["sent_at"],
                source_type=row["source_type"],
                message_text=row["message_text"],
            ))

    images = []
    with open(dataset_dir / "images.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            images.append(ImageRef(
                image_id=row["image_id"],
                user_id=row["user_id"],
                request_id=row["request_id"],
                related_event_id=row["related_event_id"],
            ))

    return Dataset(
        profiles=profiles,
        events=events,
        events_by_user=events_by_user,
        rates=rates,
        requests=requests,
        payment_options_by_request=payment_options_by_request,
        messages=messages,
        images=images,
    )
