"""Dated currency conversion using only the fixed rates supplied in
exchange_rates.csv. No live/interpolated rates are ever used."""
from __future__ import annotations

import datetime as dt
from collections import deque
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .io_data import ExchangeRate


class FxError(Exception):
    pass


class FxTable:
    def __init__(self, rates: List[ExchangeRate]):
        # graph[date][currency] -> {other_currency: rate}
        self._graph: Dict[dt.date, Dict[str, Dict[str, Decimal]]] = {}
        for r in rates:
            g = self._graph.setdefault(r.rate_date, {})
            g.setdefault(r.from_currency, {})[r.to_currency] = r.rate
            g.setdefault(r.to_currency, {})[r.from_currency] = Decimal(1) / r.rate
        self._dates = sorted(self._graph.keys())

    def _nearest_date(self, date: dt.date) -> Optional[dt.date]:
        if date in self._graph:
            return date
        # Fall back to the most recent earlier rate date, else the earliest
        # later one. This only triggers for evidence-derived dates that fall
        # off the exact monthly grid; every native dataset event settles on
        # a date with an exact rate row.
        earlier = [d for d in self._dates if d <= date]
        if earlier:
            return earlier[-1]
        later = [d for d in self._dates if d > date]
        return later[0] if later else None

    def convert(self, amount: Decimal, from_currency: str, to_currency: str,
                date: dt.date) -> Decimal:
        if from_currency == to_currency:
            return amount
        rate_date = self._nearest_date(date)
        if rate_date is None:
            raise FxError(f"No exchange rate data available at all for {date}")
        graph = self._graph[rate_date]
        path_rate = self._shortest_path_rate(graph, from_currency, to_currency)
        if path_rate is None:
            raise FxError(
                f"No conversion path {from_currency}->{to_currency} on {rate_date}"
            )
        return amount * path_rate

    @staticmethod
    def _shortest_path_rate(graph: Dict[str, Dict[str, Decimal]], src: str,
                             dst: str) -> Optional[Decimal]:
        if src == dst:
            return Decimal(1)
        if src not in graph:
            return None
        visited = {src}
        queue = deque([(src, Decimal(1))])
        while queue:
            node, acc = queue.popleft()
            for nxt, rate in graph.get(node, {}).items():
                if nxt in visited:
                    continue
                new_acc = acc * rate
                if nxt == dst:
                    return new_acc
                visited.add(nxt)
                queue.append((nxt, new_acc))
        return None
