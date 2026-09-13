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


def _is_duplicate_representation(event: Event, events_by_id: Dict[str, Event]) -> bool:
    """A pending event that is merely a second representation of an
    already-settled transaction (same linked pair, identical amount and
    currency) is not a real future obligation -- it must be excluded from
    cash flow. This is a structural check (status + link + amount), not a
    text match, so it generalizes to any pending/settled duplicate pair.
    A `failed` -> `scheduled` retry is NOT a duplicate: the earlier side has
    status `failed`, not `settled`, so it never matches this rule and the
    retry is correctly kept as a real future debit.
    """
    if event.status != "pending" or not event.linked_event_id:
        return False
    earlier = events_by_id.get(event.linked_event_id)
    if earlier is None or earlier.status != "settled":
        return False
    if earlier.currency != event.currency:
        return False
    if earlier.amount is None or event.amount is None:
        return False
    return earlier.amount == event.amount


# The relative jump in magnitude (new_amount / prev_amount - 1, on values
# sorted ascending) that must exist at a candidate split point before two
# amount tiers are even considered as potentially independent streams. A
# genuine single stream's natural month-to-month variance (a slightly bigger
# grocery bill, a small salary increase) stays well under this; two
# distinctly different cash flows sharing a category label (a fixed base
# salary vs. a smaller variable commission) typically differ by multiples.
AMOUNT_SPLIT_RATIO = Decimal("0.5")


def _amount_gap_split(items: List[tuple]) -> Optional[tuple]:
    """Looks for the single largest relative jump in magnitude across a
    category's occurrences and, if it is large enough and leaves at least
    MIN_OCCURRENCES items on each side, proposes splitting there. This is a
    *candidate* split only -- the caller still requires each side to
    independently reconstruct its own stable cadence before trusting it, so
    a one-off large or small outlier (which would fail that cadence check on
    its own) can never masquerade as a second stream.
    """
    if len(items) < 2 * MIN_OCCURRENCES:
        return None
    ordered = sorted(items, key=lambda t: t[1])
    best_ratio = None
    best_idx = None
    for i in range(1, len(ordered)):
        prev_amt, amt = ordered[i - 1][1], ordered[i][1]
        if prev_amt <= 0:
            continue
        ratio = (amt - prev_amt) / prev_amt
        if best_ratio is None or ratio > best_ratio:
            best_ratio, best_idx = ratio, i
    if best_ratio is None or best_ratio < AMOUNT_SPLIT_RATIO:
        return None
    low, high = ordered[:best_idx], ordered[best_idx:]
    if len(low) < MIN_OCCURRENCES or len(high) < MIN_OCCURRENCES:
        return None
    return low, high


def _split_independent_streams(items: List[tuple]) -> List[tuple]:
    """Finds every independent recurring cadence within one (user, category)
    history, using temporal cadence + amount structure -- never description
    text, so this generalizes to any unseen user/category.

    A category ordinarily has exactly one genuine recurring stream (handled
    by a single `_detect_backbone` call, which already drops one-off
    insertions on its own). But a category can legitimately contain two
    independently timed, independently valued cash flows sharing one label
    (e.g. a fixed monthly base payment and a separately-dated, differently-
    scaled commission). Pure gap-based cadence detection cannot safely tell
    those apart when interleaved (the *cross*-stream gaps can coincidentally
    look more regular than either stream's own gaps), so the split is
    proposed by amount tier instead and only accepted when BOTH resulting
    tiers independently pass the normal cadence-detection bar -- otherwise
    this falls back to the original single-stream-per-category behavior.
    """
    items_sorted = sorted(items, key=lambda t: t[2])
    split = _amount_gap_split(items_sorted)
    if split is not None:
        low, high = split
        low_detected = _detect_backbone(sorted(low, key=lambda t: t[2]))
        high_detected = _detect_backbone(sorted(high, key=lambda t: t[2]))
        if low_detected is not None and high_detected is not None:
            return [low_detected, high_detected]
    detected = _detect_backbone(items_sorted)
    return [detected] if detected is not None else []


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


def _detect_backbone(items: List[tuple], prefer_long: bool = False,
                      min_occurrences: int = MIN_OCCURRENCES):
    """Finds the dominant recurrence cadence for a (user, category) event
    sequence and reconstructs a clean chain of occurrences that follow it,
    dropping one-off insertions (a bonus, a duplicate payslip record, an
    ad-hoc extra purchase) that would otherwise corrupt a plain median/last-
    event reading. Returns (chain, interval_days) or None if no stable
    cadence with >=min_occurrences occurrences exists."""
    if len(items) < min_occurrences:
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
    if len(chain) < min_occurrences:
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
    streams: Dict[str, List[RecurringStream]]


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
        if _is_duplicate_representation(e, dataset.events):
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
    # Recurrence-evidence semantics vs. cash/ledger semantics: an event that
    # is the *target* of a linked_event_id (i.e. carries a non-empty
    # linked_event_id itself) is a lifecycle fact about a single transaction
    # (a reversal, a reimbursement, a sale of a prior investment, a possible
    # duplicate, a retry) -- it must never be allowed to establish or extend
    # a recurring cadence, even though it still participates normally in the
    # discrete one-off cash flow above. Excluding it here is what stops a
    # category that happens to contain exactly one such pair (and nothing
    # else) from being misread as a 2-occurrence recurring stream.
    history: Dict[str, List[tuple]] = {}
    for e, home_amount, ref_date in resolved:
        if e.direction == "credit" and e.status == "pending":
            continue
        if e.linked_event_id:
            continue
        history.setdefault(e.category, []).append((e, home_amount, ref_date))

    streams: Dict[str, List[RecurringStream]] = {}
    for category, items in history.items():
        items.sort(key=lambda t: t[2])
        for chain, median_diff in _split_independent_streams(items):
            last3 = chain[-3:]
            direction = last3[-1][0].direction
            amounts = [t[1] for t in last3]
            # Expenses: a representative recent occurrence (median of the
            # last few), not the historical maximum -- that would compound
            # into a large overstatement once projected forward at a short
            # (e.g. weekly) cadence across a 90-day horizon.
            # Income: the latest confirmed/settled figure wins over an older
            # one (conflict-resolution rule: a newer record from the same
            # source supersedes an older one), not an average or minimum.
            amount = statistics.median(amounts) if direction == "debit" else amounts[-1]
            last_event, _, last_date = chain[-1]
            monthly = 27 <= median_diff <= 31
            next_date = (_add_months(last_date, 1) if monthly
                         else last_date + dt.timedelta(days=median_diff))
            # A description explicitly marked as the final/last occurrence
            # (e.g. "Final employer payroll") ends the stream outright,
            # independent of any message evidence.
            terminated = any(w in last_event.description.lower() for w in
                              ("final ", "last ", " final", " closing", "closing "))
            end_date = last_date if terminated else None
            stream = RecurringStream(
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
            streams.setdefault(category, []).append(stream)

    _apply_evidence(streams, evidence, user_id, home, fx, request_date)

    return FinancialState(
        user_id=user_id,
        profile=profile,
        starting_balance=profile.current_available_balance,
        discrete_events=discrete,
        streams=streams,
    )


# Conflict-resolution priority (problem_statement.md): an explicit
# settlement/confirmation must not be silently overwritten by a later but
# less certain (e.g. still-estimated) record. Every fact kind our template
# matcher currently emits represents an explicit, confirmed statement, so
# all default to "confirmed"; the rank check below is a no-op for today's
# marker set but is what makes a future "estimate" fact kind behave
# correctly without further changes here.
_CERTAINTY_RANK = {"estimate": 1, "confirmed": 2}


def _apply_evidence(streams: Dict[str, List[RecurringStream]], evidence: EvidenceStore,
                     user_id: str, home: str, fx: FxTable, request_date: dt.date) -> None:
    facts = sorted(evidence.salary_facts.get(user_id, []), key=lambda f: f.sent_at)
    salary_streams = streams.get("salary", [])
    if salary_streams:
        # A message about "salary" doesn't say which independent sub-stream
        # (e.g. base vs. commission) it refers to. An end/date-shift fact
        # describes the employment relationship as a whole and safely
        # applies to every sub-stream; an amount override is only applied
        # when the category is unambiguous (exactly one detected stream) to
        # avoid misattributing a confirmed figure to the wrong sub-stream.
        applied_rank = {id(s): _CERTAINTY_RANK["confirmed"] for s in salary_streams}
        for fact in facts:
            rank = _CERTAINTY_RANK.get(getattr(fact, "certainty", "confirmed"), 2)
            if fact.kind == "ended":
                for s in salary_streams:
                    if rank < applied_rank[id(s)]:
                        continue
                    s.end_date = fact.effective_date
                    applied_rank[id(s)] = rank
            elif fact.kind in ("increase", "decrease", "resumed", "new_job", "base_confirmed"):
                if len(salary_streams) != 1:
                    continue
                s = salary_streams[0]
                if rank < applied_rank[id(s)]:
                    continue
                if fact.amount is not None:
                    conv_date = fact.effective_date or request_date
                    s.amount = fx.convert(fact.amount, fact.currency or home, home, conv_date)
                    s.end_date = None  # a later income fact supersedes an earlier "ended"
                if fact.effective_date is not None and fact.kind in ("resumed", "new_job"):
                    s.next_date = fact.effective_date
                applied_rank[id(s)] = rank
            elif fact.kind == "date_shift" and fact.effective_date is not None:
                for s in salary_streams:
                    if rank < applied_rank[id(s)]:
                        continue
                    s.next_date = fact.effective_date
                    applied_rank[id(s)] = rank

    rent_facts = sorted(evidence.rent_facts.get(user_id, []), key=lambda f: f.sent_at)
    rent_streams = streams.get("rent", [])
    if rent_facts and rent_streams:
        latest = rent_facts[-1]
        for s in rent_streams:
            s.amount = s.amount * (Decimal(1) + latest.value)
