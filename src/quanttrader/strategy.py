"""Strategies are pure functions of completed history -> desired orders.

Design rule: a strategy never sees a bar after `view.i`, never sees cash or
position sizes, and never places orders itself. It only says what weight it
*wants*. Sizing, cost, settlement and every risk cap live in the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol, Sequence

from .bars import Bar
from .indicators import atr, donchian, realized_vol, rsi, sma, zscore

When = Literal["close", "next_open"]


@dataclass(frozen=True, slots=True)
class Order:
    symbol: str
    target_weight: float
    when: When = "next_open"


@dataclass(frozen=True, slots=True)
class Held:
    symbol: str
    weight: float
    bars_held: int


@dataclass(frozen=True, slots=True)
class View:
    """Everything a strategy is allowed to know at a decision point.

    `index[symbol]` is the last *completed* bar for that symbol. Symbols are not
    assumed to share an index space: a name that listed later simply has a
    shorter history, and `has()` reports that honestly.
    """

    ts: str
    day: str
    bars_by_symbol: Mapping[str, Sequence[Bar]]
    index: Mapping[str, int]

    def bars(self, symbol: str) -> Sequence[Bar]:
        return self.bars_by_symbol[symbol]

    def window(self, symbol: str, n: int) -> Sequence[Bar]:
        end = self.index[symbol] + 1
        return self.bars_by_symbol[symbol][max(0, end - n) : end]

    def closes(self, symbol: str, n: int | None = None) -> list[float]:
        return [b.close for b in (self.window(symbol, 10**9) if n is None else self.window(symbol, n))]

    def has(self, symbol: str, n: int) -> bool:
        return self.index[symbol] + 1 >= n


@dataclass(slots=True)
class Decision:
    orders: list[Order] = field(default_factory=list)
    note: str = ""


class Strategy(Protocol):
    name: str

    def decide(self, view: View, held: Mapping[str, Held]) -> Decision: ...


class StrategyBase:
    """Convenience base: emits one target-weight order per symbol, flat or long."""

    name = "base"
    when: When = "next_open"

    def __init__(self, symbols: Sequence[str]) -> None:
        self.symbols = tuple(symbols)

    def target(self, symbol: str, view: View, held: Held | None) -> float:
        raise NotImplementedError

    def decide(self, view: View, held: Mapping[str, Held]) -> Decision:
        return Decision(
            [Order(s, self.target(s, view, held.get(s)), self.when) for s in self.symbols]
        )


# --------------------------------------------------------------------------- #
# Reference strategies. Each is a hypothesis about how these names behave.
# --------------------------------------------------------------------------- #


class BuyHold(StrategyBase):
    """Baseline. Equal weight, always long. Beats most clever ideas net of costs."""

    name = "buy_hold"

    def __init__(self, symbols: Sequence[str], weight: float = 1.0) -> None:
        super().__init__(symbols)
        self.weight = weight

    def target(self, symbol: str, view: View, held: Held | None) -> float:
        return self.weight / len(self.symbols)


class OvernightHold(StrategyBase):
    """Long from the close into the next open, flat during the session.

    The cross-sectional evidence for this drift is real, but it decays and it
    pays two spreads a day, so it is extremely cost-sensitive.
    """

    name = "overnight_hold"

    def __init__(self, symbols: Sequence[str], weight: float = 1.0) -> None:
        super().__init__(symbols)
        self.weight = weight

    def decide(self, view: View, held: Mapping[str, Held]) -> Decision:
        w = self.weight / len(self.symbols)
        orders: list[Order] = []
        for s in self.symbols:
            orders.append(Order(s, w, "close"))        # buy the close
            orders.append(Order(s, 0.0, "next_open"))  # sell the open
        return Decision(orders, note="overnight")


class VolTargetHold(StrategyBase):
    """Buy and hold, but scale exposure inversely to realised volatility.

    Vol targeting is one of the few effects that survives out of sample: it
    improves risk-adjusted return without pretending to forecast direction.
    """

    name = "vol_target_hold"

    def __init__(self, symbols: Sequence[str], target_vol: float = 0.45, lookback: int = 20,
                 max_weight: float = 1.0) -> None:
        super().__init__(symbols)
        self.target_vol = target_vol
        self.lookback = lookback
        self.max_weight = max_weight

    def target(self, symbol: str, view: View, held: Held | None) -> float:
        if not view.has(symbol, self.lookback + 2):
            return 0.0
        vol = realized_vol(view.closes(symbol), self.lookback, 252)[-1]
        if not vol:
            return 0.0
        return min(self.max_weight / len(self.symbols), self.target_vol / vol / len(self.symbols))


class BollingerRevert(StrategyBase):
    """Buy a stretched-down close, exit at the mean. Optional trend filter."""

    name = "bollinger_revert"

    def __init__(self, symbols: Sequence[str], entry_z: float = 1.5, lookback: int = 20,
                 trend_ma: int = 0, max_hold: int = 10, per_symbol_weight: float = 0.4) -> None:
        super().__init__(symbols)
        self.entry_z, self.lookback = entry_z, lookback
        self.trend_ma, self.max_hold = trend_ma, max_hold
        self.w = per_symbol_weight

    def target(self, symbol: str, view: View, held: Held | None) -> float:
        need = max(self.lookback, self.trend_ma) + 2
        if not view.has(symbol, need):
            return 0.0 if held else 0.0
        closes = view.closes(symbol)
        z = zscore(closes, self.lookback)[-1]
        if z is None:
            return 0.0
        if held:
            mean = sma(closes, self.lookback)[-1]
            if closes[-1] >= mean or held.bars_held >= self.max_hold:
                return 0.0
            return self.w
        if z < -self.entry_z:
            if self.trend_ma:
                tma = sma(closes, self.trend_ma)[-1]
                if tma is None or closes[-1] <= tma:
                    return 0.0
            return self.w
        return 0.0


class DipBuyTrend(StrategyBase):
    """Buy a sharp one-day drop, but only while the name is in an uptrend.

    This is the mean-reversion signal the data actually supports, and only for
    names whose regime is up. Long-only, so it can only ever be a filter.
    """

    name = "dip_buy_trend"

    def __init__(self, symbols: Sequence[str], drop_sd: float = 1.0, trend_ma: int = 200,
                 max_hold: int = 3, lookback: int = 20, per_symbol_weight: float = 0.4) -> None:
        super().__init__(symbols)
        self.drop_sd, self.trend_ma = drop_sd, trend_ma
        self.max_hold, self.lookback = max_hold, lookback
        self.w = per_symbol_weight

    def target(self, symbol: str, view: View, held: Held | None) -> float:
        if not view.has(symbol, max(self.trend_ma, self.lookback + 2) + 1):
            return 0.0
        closes = view.closes(symbol)
        if held:
            return self.w if held.bars_held < self.max_hold else 0.0
        ret = closes[-1] / closes[-2] - 1
        sd = _sd_of_returns(closes, self.lookback)
        if not sd or ret > -self.drop_sd * sd:
            return 0.0
        tma = sma(closes, self.trend_ma)[-1]
        return self.w if tma is not None and closes[-1] > tma else 0.0


class TrendFollow(StrategyBase):
    """Donchian breakout entry, prior-low exit, only above the long average."""

    name = "trend_follow"

    def __init__(self, symbols: Sequence[str], entry_n: int = 20, exit_n: int = 10,
                 trend_ma: int = 100, max_hold: int = 30, per_symbol_weight: float = 0.4) -> None:
        super().__init__(symbols)
        self.entry_n, self.exit_n = entry_n, exit_n
        self.trend_ma, self.max_hold = trend_ma, max_hold
        self.w = per_symbol_weight

    def target(self, symbol: str, view: View, held: Held | None) -> float:
        need = max(self.entry_n, self.exit_n, self.trend_ma) + 2
        if not view.has(symbol, need):
            return 0.0
        closes = view.closes(symbol)
        window = view.window(symbol, need)
        highs = [b.high for b in window]
        lows = [b.low for b in window]
        upper, lower = donchian(highs, lows, self.entry_n)
        _, exit_low = donchian(highs, lows, self.exit_n)
        if held:
            if closes[-1] < exit_low[-1] or held.bars_held >= self.max_hold:
                return 0.0
            return self.w
        if closes[-1] > upper[-1]:
            tma = sma(closes, self.trend_ma)[-1]
            if tma is not None and closes[-1] > tma:
                return self.w
        return 0.0


class RsiRevert(StrategyBase):
    """Classic short-horizon oversold bounce (RSI-2 style), optional trend filter."""

    name = "rsi_revert"

    def __init__(self, symbols: Sequence[str], level: float = 10.0, exit_level: float = 60.0,
                 period: int = 2, trend_ma: int = 200, max_hold: int = 3,
                 per_symbol_weight: float = 0.4) -> None:
        super().__init__(symbols)
        self.level, self.exit_level, self.period = level, exit_level, period
        self.trend_ma, self.max_hold = trend_ma, max_hold
        self.w = per_symbol_weight

    def target(self, symbol: str, view: View, held: Held | None) -> float:
        if not view.has(symbol, max(self.trend_ma, self.period + 2) + 1):
            return 0.0
        closes = view.closes(symbol)
        v = rsi(closes, self.period)[-1]
        if v is None:
            return 0.0
        if held:
            return self.w if v < self.exit_level and held.bars_held < self.max_hold else 0.0
        if v > self.level:
            return 0.0
        tma = sma(closes, self.trend_ma)[-1]
        return self.w if tma is not None and closes[-1] > tma else 0.0


class AtrStops:
    """Mixin: an intrabar stop `atr_multiple` ATR below the entry price.

    The engine owns execution of the stop; the strategy only publishes the
    distance, so risk geometry stays in one place.
    """

    atr_lookback: int = 14
    atr_multiple: float = 2.0

    def stop_distance(self, symbol: str, view: View) -> float | None:
        if not view.has(symbol, self.atr_lookback + 2):
            return None
        window = view.window(symbol, self.atr_lookback + 1)
        a = atr([b.high for b in window], [b.low for b in window], [b.close for b in window],
                self.atr_lookback)[-1]
        return None if not a else self.atr_multiple * a


def _sd_of_returns(closes: Sequence[float], n: int) -> float | None:
    from statistics import stdev

    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    window = rets[-n:]
    return stdev(window) if len(window) > 1 else None
