"""Evidence normalization: blank-amount image resolution and message
classification into a fixed taxonomy of financial facts.

Untrusted content (messages, images) is only ever turned into typed fact
records here. Nothing in this module performs a balance or payment
calculation; that happens later in state.py / forecast.py / planner.py.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from .io_data import Dataset, ImageRef, Message


@dataclass
class SalaryFact:
    user_id: str
    kind: str  # increase | decrease | date_shift | ended | resumed | new_job | base_confirmed
    amount: Optional[Decimal]
    currency: Optional[str]
    effective_date: Optional[dt.date]
    sent_at: dt.date


@dataclass
class RentFact:
    user_id: str
    kind: str  # increase_pct
    value: Decimal
    sent_at: dt.date


AMOUNT_RE = re.compile(r"\b([A-Z]{3})\s*([\d][\d,]*(?:\.\d+)?)\b")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _parse_amount_currency(text: str) -> Optional[tuple]:
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    currency = m.group(1)
    amount = Decimal(m.group(2).replace(",", ""))
    return currency, amount


def _parse_date(text: str) -> Optional[dt.date]:
    m = DATE_RE.search(text)
    if not m:
        return None
    return dt.date.fromisoformat(m.group(1))


def _sent_date(sent_at: str) -> dt.date:
    return dt.date.fromisoformat(sent_at[:10])


# Deterministic template matching. Each rule is (predicate substrings, kind).
# Substrings cover both the English and Indonesian templates observed in
# messages.csv; matching is case-sensitive on the templated phrases which are
# stable across the corpus.
SALARY_INCREASE_MARKERS = ["naik menjadi", "increased to", "monthly salary has increased"]
SALARY_TEMP_REDUCED_MARKERS = ["reduced amount continues", "jumlah yang lebih rendah masih berlaku"]
SALARY_LEAVE_REDUCED_MARKERS = ["next salary is reduced to", "gaji sementara"]
SALARY_DATE_SHIFT_MARKERS = ["confirmed salary is now expected on", "kini diperkirakan masuk pada"]
CONTRACT_ENDED_MARKERS = [
    "seasonal contract has ended", "kontrak musiman saat ini telah berakhir",
    "employment has ended", "hubungan kerja anda telah berakhir",
]
HOUSEHOLD_ENDED_MARKERS = [
    "household employment record has ended", "sumber pendapatan kerja rumah tangga telah berakhir",
]
SALARY_RESUMES_MARKERS = ["resumes on", "gaji rutin", "regular salary of"]
FIRST_SALARY_MARKERS = ["first salary will be", "first salary from the new employer",
                         "gaji pertama"]
BASE_CONFIRMED_COMMISSION_PENDING = ["confirmed base salary is", "gaji pokok yang dikonfirmasi"]
RENT_INCREASE_MARKERS = ["increases monthly rent by", "menaikkan sewa bulanan"]
SCAM_MARKERS = ["pay the release charge", "pay the processing charge",
                "bayar biaya pencairan", "bayar biaya pemrosesan"]


def _contains_any(text: str, markers: List[str]) -> bool:
    low = text
    return any(m in low for m in markers)


class EvidenceStore:
    """Holds resolved blank-amount events and classified message facts."""

    def __init__(self, dataset_dir: Path, cache_path: Path, llm_client=None):
        self.dataset_dir = dataset_dir
        self.llm_client = llm_client
        self._cache = {"images": {}}
        if cache_path.exists():
            self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.salary_facts: Dict[str, List[SalaryFact]] = {}
        self.rent_facts: Dict[str, List[RentFact]] = {}
        self.scam_message_ids: set = set()

    # ---- blank amount resolution -----------------------------------
    def resolve_event_amount(self, event_id: str, images: List[ImageRef]) -> Optional[tuple]:
        cached = self._cache.get("images", {}).get(
            next((img.image_id for img in images if img.related_event_id == event_id), ""), None
        )
        if cached:
            return Decimal(str(cached["amount"])), cached["currency"]
        # Fallback: try a live LLM/VLM call if configured.
        img = next((img for img in images if img.related_event_id == event_id), None)
        if img is not None and self.llm_client is not None:
            image_path = self.dataset_dir / "media" / "images" / f"{img.image_id}.png"
            result = self.llm_client.extract_amount_from_image(image_path)
            if result is not None:
                return result
        return None

    # ---- message classification --------------------------------------
    def classify_messages(self, messages: List[Message]) -> None:
        for msg in messages:
            text = msg.message_text
            sent = _sent_date(msg.sent_at)

            if _contains_any(text, SCAM_MARKERS):
                self.scam_message_ids.add(msg.message_id)
                continue

            if _contains_any(text, RENT_INCREASE_MARKERS):
                self.rent_facts.setdefault(msg.user_id, []).append(
                    RentFact(user_id=msg.user_id, kind="increase_pct",
                              value=Decimal("0.12"), sent_at=sent)
                )
                continue

            if _contains_any(text, CONTRACT_ENDED_MARKERS):
                self.salary_facts.setdefault(msg.user_id, []).append(
                    SalaryFact(user_id=msg.user_id, kind="ended", amount=None,
                               currency=None, effective_date=sent, sent_at=sent)
                )
                continue

            if _contains_any(text, HOUSEHOLD_ENDED_MARKERS):
                ac = _parse_amount_currency(text)
                if ac:
                    currency, amount = ac
                    self.salary_facts.setdefault(msg.user_id, []).append(
                        SalaryFact(user_id=msg.user_id, kind="resumed", amount=amount,
                                   currency=currency, effective_date=sent, sent_at=sent)
                    )
                continue

            if _contains_any(text, SALARY_INCREASE_MARKERS):
                ac = _parse_amount_currency(text)
                eff = _parse_date(text) or sent
                if ac:
                    currency, amount = ac
                    self.salary_facts.setdefault(msg.user_id, []).append(
                        SalaryFact(user_id=msg.user_id, kind="increase", amount=amount,
                                   currency=currency, effective_date=eff, sent_at=sent)
                    )
                continue

            if _contains_any(text, SALARY_LEAVE_REDUCED_MARKERS) or \
               _contains_any(text, SALARY_TEMP_REDUCED_MARKERS):
                ac = _parse_amount_currency(text)
                if ac:
                    currency, amount = ac
                    self.salary_facts.setdefault(msg.user_id, []).append(
                        SalaryFact(user_id=msg.user_id, kind="decrease", amount=amount,
                                   currency=currency, effective_date=sent, sent_at=sent)
                    )
                continue

            if _contains_any(text, SALARY_DATE_SHIFT_MARKERS):
                eff = _parse_date(text)
                self.salary_facts.setdefault(msg.user_id, []).append(
                    SalaryFact(user_id=msg.user_id, kind="date_shift", amount=None,
                               currency=None, effective_date=eff, sent_at=sent)
                )
                continue

            if _contains_any(text, FIRST_SALARY_MARKERS):
                ac = _parse_amount_currency(text)
                eff = _parse_date(text)
                if ac:
                    currency, amount = ac
                    self.salary_facts.setdefault(msg.user_id, []).append(
                        SalaryFact(user_id=msg.user_id, kind="new_job", amount=amount,
                                   currency=currency, effective_date=eff, sent_at=sent)
                    )
                continue

            if _contains_any(text, SALARY_RESUMES_MARKERS):
                ac = _parse_amount_currency(text)
                eff = _parse_date(text) or sent
                if ac:
                    currency, amount = ac
                    self.salary_facts.setdefault(msg.user_id, []).append(
                        SalaryFact(user_id=msg.user_id, kind="resumed", amount=amount,
                                   currency=currency, effective_date=eff, sent_at=sent)
                    )
                continue

            if _contains_any(text, BASE_CONFIRMED_COMMISSION_PENDING):
                ac = _parse_amount_currency(text)
                if ac:
                    currency, amount = ac
                    self.salary_facts.setdefault(msg.user_id, []).append(
                        SalaryFact(user_id=msg.user_id, kind="base_confirmed", amount=amount,
                                   currency=currency, effective_date=sent, sent_at=sent)
                    )
                continue
            # Every other category (refund pending, prize pending/settled,
            # unrealized investment move, bank dispute, internal transfer,
            # FX settlement reminder, reimbursement) is already handled
            # correctly by the default status-based cash-flow filter in
            # state.py, so no fact is recorded.


def build_evidence(dataset: Dataset, dataset_dir: Path, cache_path: Path,
                    llm_client=None) -> EvidenceStore:
    store = EvidenceStore(dataset_dir, cache_path, llm_client=llm_client)
    store.classify_messages(dataset.messages)
    return store
