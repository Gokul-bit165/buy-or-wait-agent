"""Per-user financial state reconstruction: currency-normalized discrete
future events plus detected recurring streams, with evidence-fact overrides
applied on top.
"""
from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from .evidence import EvidenceStore
from .fx import FxTable
from .io_data import Dataset, Event, ImageRef, Profile

EXCLUDED_STATUS = {"cancelled", "failed", "unrealized"}
MIN_OCCURRENCES = 2


def _bucket(days: int) -> Optional[int]:
    if days <= 3:
        return 1
    if 5 <= days <= 10:
        return 7
    if 11 <= days <= 20:
        return 14
    if 25 <= days <= 35:
        return 30
    if 55 <= days <= 70:
        return 60
    if 80 <= days <= 100:
        return 90
    return None


def _detect_backbone(items: List[tuple], prefer_long: bool = False):
    """Finds the dominant recurrence cadence for a (user, category) event
    sequence and reconstructs a clean chain of occurrences that follow it,
    dropping one-off insertions (a bonus, a duplicate payslip record, an
    ad-hoc extra purchase) that would otherwise corrupt a plain median/last-
    event reading. Returns (chain, interval_days) or None if no stable
    cadence with >=2 occurrences exists."""
    if len(items) < MIN_OCCURRENCES:
        return None
    dates = [t[2] for t in items]
    diffs = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    buckets = [_bucket(d) for d in diffs if d > 0]
    counts = {}
    for b in buckets:
        if b is not None:
            counts[b] = counts.get(b, 0) + 1
    if not counts:
        return None
    if prefer_long:
        strong = [b for b, c in counts.items() if c >= 2]
        dominant = max(strong) if strong else max(counts, key=lambda k: counts[k])
    else:
        dominant = max(counts, key=lambda k: counts[k])

    chain = [items[0]]
    for item in items[1:]:
        gap = (item[2] - chain[-1][2]).days
        if gap <= 0:
            continue
        if _bucket(gap) == dominant or (dominant * 0.7 <= gap <= dominant * 1.4):
            chain.append(item)
        # else: treat as a one-off insertion; keep the current chain end and
        # keep scanning forward without extending the chain from this item.
    if len(chain) < MIN_OCCURRENCES:
        return None
    return chain, dominant


def _add_months(date: dt.date, months: int) -> dt.date:
    month_index = date.month - 1 + months
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    import calendar
    day = min(date.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


@dataclass
class DiscreteEvent:
    date: dt.date
    signed_amount: Decimal  # positive = credit, negative = debit
    event_id: str
    category: str


@dataclass
class RecurringStream:
    category: str
    direction: str  # debit | credit
    amount: Decimal  # home currency, positive magnitude
    interval_days: int
    monthly: bool
    next_date: dt.date
    anchor_event_id: str
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]
    end_date: Optional[dt.date] = None  # stop projecting on/after this date

    def advance(self, date: dt.date) -> dt.date:
        return _add_months(date, 1) if self.monthly else date + dt.timedelta(days=self.interval_days)


@dataclass
class FinancialState:
    user_id: str
    profile: Profile
    starting_balance: Decimal
    discrete_events: List[DiscreteEvent]
    streams: Dict[str, RecurringStream]


def _resolve_amount(event: Event, images: List[ImageRef], evidence: EvidenceStore) -> Optional[tuple]:
    if event.amount is not None:
        return event.amount, event.currency
    resolved = evidence.resolve_event_amount(event.event_id, images)
    return resolved


def build_financial_state(dataset: Dataset, user_id: str, request_date: dt.date,
                           fx: FxTable, evidence: EvidenceStore) -> FinancialState:
    profile = dataset.profiles[user_id]
    home = profile.home_currency
    raw_events = dataset.events_by_user.get(user_id, [])

    resolved: List[tuple] = []  # (Event, home_amount_signed or None for non-cash, ref_date)
    for e in raw_events:
        if e.status in EXCLUDED_STATUS:
            continue
        if e.direction not in ("debit", "credit"):
            continue
        amt_cur = _resolve_amount(e, dataset.images, evidence)
        if amt_cur is None:
            continue
        amount, currency = amt_cur
        ref_date = e.settlement_date or e.event_date
        if ref_date is None:
            continue
        home_amount = fx.convert(amount, currency, home, ref_date)
        resolved.append((e, home_amount, ref_date))

    # ---- discrete confirmed future events (scheduled / pending) --------
    discrete: List[DiscreteEvent] = []
    for e, home_amount, ref_date in resolved:
        if e.status not in ("scheduled", "pending"):
            continue
        if ref_date < request_date:
            continue
        if e.direction == "credit" and e.status == "pending":
            continue  # pending credits never counted
        signed = home_amount if e.direction == "credit" else -home_amount
        discrete.append(DiscreteEvent(date=ref_date, signed_amount=signed,
                                       event_id=e.event_id, category=e.category))

    # ---- recurring-stream detection ------------------------------------
    # Uses every non-cancelled/failed/unrealized occurrence (settled history
    # plus any already-confirmed scheduled/pending occurrence, e.g. the
    # "next confirmed salary" the dataset ships alongside 1-2 months of
    # settled history) to detect the interval and anchor the projected
    # amount. Discrete future occurrences are still emitted once via
    # `discrete` above; the ±5-day de-dup in forecast.py prevents a stream
    # projection from double-counting that same confirmed occurrence.
    history: Dict[str, List[tuple]] = {}
    for e, home_amount, ref_date in resolved:
        if e.direction == "credit" and e.status == "pending":
            continue
        history.setdefault(e.category, []).append((e, home_amount, ref_date))

    streams: Dict[str, RecurringStream] = {}
    for category, items in history.items():
        items.sort(key=lambda t: t[2])
        detected = _detect_backbone(items)
        if detected is None:
            continue
        chain, median_diff = detected
        last3 = chain[-3:]
        direction = last3[-1][0].direction
        amounts = [t[1] for t in last3]
        # Expenses: a representative recent occurrence (median of the last
        # few), not the historical maximum -- that would compound into a
        # large overstatement once projected forward at a short (e.g.
        # weekly) cadence across a 90-day horizon.
        # Income: the latest confirmed/settled figure wins over an older one
        # (conflict-resolution rule: a newer record from the same source
        # supersedes an older one), not an average or minimum.
        amount = statistics.median(amounts) if direction == "debit" else amounts[-1]
        last_event, _, last_date = chain[-1]
        monthly = 27 <= median_diff <= 31
        next_date = _add_months(last_date, 1) if monthly else last_date + dt.timedelta(days=median_diff)
        # A description explicitly marked as the final/last occurrence (e.g.
        # "Final employer payroll") ends the stream outright, independent of
        # any message evidence.
        terminated = any(w in last_event.description.lower() for w in
                          ("final ", "last ", " final", " closing", "closing "))
        end_date = last_date if terminated else None
        streams[category] = RecurringStream(
            category=category,
            direction=direction,
            amount=amount,
            interval_days=median_diff,
            monthly=monthly,
            next_date=next_date,
            end_date=end_date,
            anchor_event_id=last_event.event_id,
            flexibility=last_event.flexibility,
            minimum_allowed_amount=(
                fx.convert(last_event.minimum_allowed_amount, last_event.currency, home, last_date)
                if last_event.minimum_allowed_amount is not None else None
            ),
        )

    _apply_evidence(streams, evidence, user_id, home, fx, request_date)

    return FinancialState(
        user_id=user_id,
        profile=profile,
        starting_balance=profile.current_available_balance,
        discrete_events=discrete,
        streams=streams,
    )


def _apply_evidence(streams: Dict[str, RecurringStream], evidence: EvidenceStore,
                     user_id: str, home: str, fx: FxTable, request_date: dt.date) -> None:
    facts = sorted(evidence.salary_facts.get(user_id, []), key=lambda f: f.sent_at)
    if "salary" in streams:
        stream = streams["salary"]
        for fact in facts:
            if fact.kind == "ended":
                stream.end_date = fact.effective_date
            elif fact.kind in ("increase", "decrease", "resumed", "new_job", "base_confirmed"):
                if fact.amount is not None:
                    conv_date = fact.effective_date or request_date
                    stream.amount = fx.convert(fact.amount, fact.currency or home, home, conv_date)
                    stream.end_date = None  # a later income fact supersedes an earlier "ended"
                if fact.effective_date is not None and fact.kind in ("resumed", "new_job"):
                    stream.next_date = fact.effective_date
            elif fact.kind == "date_shift" and fact.effective_date is not None:
                stream.next_date = fact.effective_date

    rent_facts = sorted(evidence.rent_facts.get(user_id, []), key=lambda f: f.sent_at)
    if rent_facts and "rent" in streams:
        latest = rent_facts[-1]
        streams["rent"].amount = streams["rent"].amount * (Decimal(1) + latest.value)
