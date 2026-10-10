"""Price bars and the invariants we refuse to trade on.

Everything downstream assumes bars that have passed through `clean`. That is the
one place that decides what "a real, completed, usable bar" means.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    ts: str          # ISO-8601 UTC, bar *start* (as Robinhood returns it)
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def day(self) -> str:
        return self.ts[:10]


def _pos(value: float) -> bool:
    return value > 0 and value == value  # not NaN


def clean(raw_bars: Iterable[dict], symbol: str, cutoff: str | None = None) -> list[Bar]:
    """Filter raw Robinhood bars down to usable ones, then assert OHLC sanity.

    Dropped: interpolated placeholders, zero/negative volume, non-positive prices,
    and anything at or after `cutoff` (an incomplete session).
    """
    seen: set[str] = set()
    bars: list[Bar] = []
    for row in raw_bars:
        ts = row["begins_at"]
        if row.get("interpolated") or row.get("volume", 0) <= 0:
            continue
        if cutoff is not None and ts[:10] >= cutoff:
            continue
        o, h, l, c = (float(row[f"{k}_price"]) for k in ("open", "high", "low", "close"))
        if not all(_pos(v) for v in (o, h, l, c)):
            continue
        if not (l <= min(o, c) and max(o, c) <= h):
            continue  # corrupt bar; safer to lose a bar than to trust it
        if ts in seen:
            raise ValueError(f"{symbol}: duplicate bar timestamp {ts}")
        seen.add(ts)
        bars.append(Bar(symbol, ts, o, h, l, c, float(row["volume"])))
    bars.sort(key=lambda b: b.ts)
    return bars


def session_days(bars: Sequence[Bar]) -> list[str]:
    """Ordered distinct trading dates."""
    out: list[str] = []
    for b in bars:
        if not out or out[-1] != b.day:
            out.append(b.day)
    return out


@dataclass(frozen=True, slots=True)
class Universe:
    """Aligned bar series for a set of symbols, with a shared timeline."""

    series: dict[str, list[Bar]]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self.series))

    def timeline(self) -> list[str]:
        """Timestamps present in *every* symbol, ascending."""
        common: set[str] | None = None
        for bars in self.series.values():
            stamps = {b.ts for b in bars}
            common = stamps if common is None else common & stamps
        return sorted(common or ())

    def index(self, symbol: str) -> dict[str, int]:
        return {b.ts: i for i, b in enumerate(self.series[symbol])}

    def warmup_end(self, needed: int) -> str:
        """First timestamp at which every symbol has `needed` history bars."""
        return max(self.series[s][needed].ts for s in self.symbols)

    def slice(self, symbols: Sequence[str]) -> "Universe":
        return Universe({s: self.series[s] for s in symbols})
