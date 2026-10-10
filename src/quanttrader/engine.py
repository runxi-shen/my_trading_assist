"""Event-driven backtest engine.

The engine exists to make one thing hard: cheating. It is the only component
that knows about money. The rules it enforces, in order, per bar:

1. A strategy decides using bars up to and including the current one only.
2. An order for the next open fills at that bar's open, plus slippage.
3. An order for the close fills at this bar's close, plus slippage.
4. A resting stop is checked against the *same* bar's low and fills at the worse
   of the open and the stop. Gaps are losses, not conveniences.
5. Buying debits settled cash. Selling credits *unsettled* cash that matures on
   the next observed session (cash account, T+1). Unsettled cash is counted in
   equity but is not spendable.
6. Every order is validated against the policy before it is allowed.
7. Once the drawdown brake trips, entries stay frozen for the rest of the run.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .bars import Bar, Universe, session_days
from .config import Policy
from .strategy import Decision, Held, Order, Strategy, View

FAR_FUTURE = "9999-12-31"


@dataclass(slots=True)
class Position:
    symbol: str
    qty: float = 0.0
    entry_price: float = 0.0
    entry_day: str = ""
    bars_held: int = 0
    stop: float | None = None
    cost_basis: float = 0.0


@dataclass(frozen=True, slots=True)
class Fill:
    ts: str
    symbol: str
    side: str
    qty: float
    price: float
    reason: str
    cash_delta: float


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    entry_ts: str
    exit_ts: str
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    return_pct: float
    reason: str
    bars_held: int


@dataclass
class Result:
    strategy: str
    interval: str
    start: str
    end: str
    initial_equity: float
    final_equity: float
    equity_curve: list[tuple[str, float]]
    fills: list[Fill]
    trades: list[Trade]
    halted: bool
    halted_at: str | None
    notes: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, policy: Policy, initial_equity: float, slippage_bps: float | None = None,
                 settlement_days: int | None = None, enforce_settlement: bool = True,
                 enforce_brake: bool = True) -> None:
        self.policy = policy
        self.initial_equity = initial_equity
        self.slip = (slippage_bps if slippage_bps is not None
                     else policy.execution.slippage_bps_each_side) / 10_000
        self.settlement_days = (settlement_days if settlement_days is not None
                                else policy.execution.settlement_days)
        self.enforce_settlement = enforce_settlement
        # Diagnostic runs may disable the brake to expose a strategy's raw risk.
        # Any such run is labelled "no_brake" in reports; it is never a candidate.
        self.enforce_brake = enforce_brake

    def run(self, universe: Universe, strategy: Strategy, interval: str = "daily",
            start: str | None = None, end: str | None = None,
            stop_distance: float | None = None) -> Result:
        timeline = [t for t in universe.timeline()
                    if (start is None or t[:10] >= start) and (end is None or t[:10] < end)]
        if not timeline:
            raise ValueError("no bars in range")

        self.symbols = universe.symbols
        self.bars = {s: {b.ts: b for b in universe.series[s]} for s in self.symbols}
        self.index = {s: {b.ts: i for i, b in enumerate(universe.series[s])} for s in self.symbols}
        days = session_days([self.bars[self.symbols[0]][t] for t in timeline])
        self.rank = {d: k for k, d in enumerate(days)}
        self.days = days

        self.cash = self.initial_equity
        self.unsettled: list[tuple[str, float]] = []
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self.trades: list[Trade] = []
        self.curve: list[tuple[str, float]] = []
        self.notes: list[str] = []
        self.trades_today: dict[str, int] = defaultdict(int)
        self.tol = self.policy.risk.rebalance_tolerance
        queue: dict[str, list[Order]] = defaultdict(list)

        halted = False
        halted_at: str | None = None
        peak = self.initial_equity

        for ts in timeline:
            day = ts[:10]
            self._mature(day)

            for order in queue.pop(ts, []):
                self._trade_to_weight(order, self.bars[order.symbol][ts], "open", stop_distance)

            for sym in list(self.positions):
                pos = self.positions[sym]
                if pos.qty <= 0 or pos.stop is None:
                    continue
                bar = self.bars[sym][ts]
                if bar.low <= pos.stop:
                    self._sell(sym, pos.qty, min(bar.open, pos.stop) * (1 - self.slip),
                               ts, "stop", pos.bars_held)

            equity = self._equity(ts, "close")
            peak = max(peak, equity)
            self.curve.append((ts, equity))

            if (self.enforce_brake and not halted
                    and equity / peak - 1 <= -self.policy.risk.max_drawdown):
                halted, halted_at = True, ts
                self.notes.append(f"drawdown brake tripped at {ts} ({equity / peak - 1:.2%})")
                for sym in list(self.positions):
                    pos = self.positions[sym]
                    if pos.qty > 0:
                        self._sell(sym, pos.qty, self.bars[sym][ts].close * (1 - self.slip),
                                   ts, "brake", pos.bars_held)

            if halted or ts == timeline[-1]:
                continue

            # Consult the strategy on completed history. A "close" order is an
            # order handed in at the closing auction, so it fills at this close.
            view = View(ts=ts, day=day, bars_by_symbol=universe.series,
                        index={s: self.index[s][ts] for s in self.symbols})
            held = {s: Held(s, 0.0, p.bars_held) for s, p in self.positions.items() if p.qty > 0}
            decision = strategy.decide(view, held)
            next_ts = timeline[timeline.index(ts) + 1]
            for order in decision.orders:
                self.policy.assert_tradable(order.symbol)
                if order.when == "close":
                    self._trade_to_weight(order, self.bars[order.symbol][ts], "close", stop_distance)
                else:
                    queue[next_ts].append(order)
            if decision.note:
                self.notes.append(f"{ts} {decision.note}")
            self.curve[-1] = (ts, self._equity(ts, "close"))
            for pos in self.positions.values():
                pos.bars_held += 1

        final_ts = timeline[-1]
        for sym in list(self.positions):
            pos = self.positions[sym]
            if pos.qty > 0:
                self._sell(sym, pos.qty, self.bars[sym][final_ts].close * (1 - self.slip),
                           final_ts, "terminal_valuation", pos.bars_held)
        final_equity = self.cash + sum(a for _, a in self.unsettled)
        if self.curve:
            self.curve[-1] = (final_ts, final_equity)

        return Result(strategy.name, interval, timeline[0], final_ts, self.initial_equity,
                      final_equity, self.curve, self.fills, self.trades, halted, halted_at,
                      self.notes)

    # -- money ------------------------------------------------------------- #

    def _settle_day(self, day: str) -> str:
        """When a sale's proceeds become spendable. Only called when enforcing."""
        k = self.rank[day] + self.settlement_days
        return self.days[k] if k < len(self.days) else FAR_FUTURE

    def _mature(self, day: str) -> None:
        due = [a for d, a in self.unsettled if self.rank.get(d, 10**9) <= self.rank[day]]
        self.cash += sum(due)
        self.unsettled = [(d, a) for d, a in self.unsettled
                          if self.rank.get(d, 10**9) > self.rank[day]]

    def _equity(self, ts: str, at: str) -> float:
        total = self.cash + sum(a for _, a in self.unsettled)
        for sym, pos in self.positions.items():
            if pos.qty > 0:
                total += pos.qty * self._px(self.bars[sym][ts], at)
        return total

    def _px(self, bar: Bar, at: str) -> float:
        return bar.open if at == "open" else bar.close

    def _fee_on_sell(self, notional: float) -> float:
        return notional * self.policy.fees.get("sec_fee_bps_on_sell", 0.0) / 10_000

    # -- orders ------------------------------------------------------------ #

    def _trade_to_weight(self, order: Order, bar: Bar, at: str,
                         stop_distance: float | None) -> None:
        r, ex = self.policy.risk, self.policy.execution
        px = self._px(bar, at)
        # Slippage always works against us: buys pay up, sells give up.
        buy_px = px * (1 + self.slip)
        sell_px = px * (1 - self.slip)
        equity = self._equity(bar.ts, at)
        pos = self.positions.get(order.symbol)
        held = pos.qty if pos else 0.0

        if order.target_weight <= 0:
            want = 0.0
        else:
            weight = min(order.target_weight, r.max_weight_per_symbol, r.max_gross_exposure)
            want = equity * weight / buy_px
            if not ex.allow_fractional_shares:
                want = math.floor(want)

        delta = want - held
        # Ignore drift we did not ask for. Without this a target-weight strategy
        # rebalances every single bar and burns the trade budget on noise.
        if abs(delta) * buy_px < max(ex.min_order_notional, self.tol * equity):
            return
        if self.trades_today[bar.day] >= r.max_trades_per_day:
            return

        if delta > 0:
            reserve = r.cash_reserve_fraction * equity
            affordable = max(0.0, self.cash - reserve) / buy_px
            delta = min(delta, affordable)
            if not ex.allow_fractional_shares:
                delta = math.floor(delta)
            cost = delta * buy_px
            if delta <= 0 or cost < ex.min_order_notional:
                return
            self.trades_today[bar.day] += 1
            self.cash -= cost
            if pos is None:
                pos = Position(order.symbol)
                self.positions[order.symbol] = pos
            if pos.qty <= 0:
                pos.entry_day = bar.day
                pos.cost_basis = 0.0
            pos.cost_basis += cost
            pos.qty += delta
            pos.entry_price = pos.cost_basis / pos.qty
            if stop_distance:
                pos.stop = pos.entry_price - stop_distance
            self.fills.append(Fill(bar.ts, order.symbol, "buy", delta, buy_px,
                                   "signal", -cost))
            return

        qty = min(-delta, held)
        if qty * sell_px < ex.min_order_notional:
            return
        self.trades_today[bar.day] += 1
        self._sell(order.symbol, qty, sell_px, bar.ts, "signal", pos.bars_held)

    def _sell(self, sym: str, qty: float, price: float, ts: str, reason: str,
              bars_held: int) -> None:
        pos = self.positions.get(sym)
        if not pos or pos.qty <= 0 or qty <= 0:
            return
        qty = min(qty, pos.qty)
        gross = qty * price
        fee = self._fee_on_sell(gross)
        proceeds = gross - fee
        pnl = proceeds - qty * pos.entry_price
        self.trades.append(Trade(sym, f"{pos.entry_day}T00:00:00Z" if pos.entry_day else ts, ts,
                                 qty, pos.entry_price, price, pnl, price / pos.entry_price - 1,
                                 reason, bars_held))
        pos.qty -= qty
        if pos.qty <= 1e-9:
            pos.qty = 0.0
            pos.stop = None
            pos.cost_basis = 0.0
        self.fills.append(Fill(ts, sym, "sell", qty, price, reason, proceeds))
        if self.enforce_settlement and self.settlement_days > 0:
            self.unsettled.append((self._settle_day(ts[:10]), proceeds))
        else:
            # Diagnostics only: pretend the broker fronts the cash immediately.
            self.cash += proceeds


def summarize(result: Result) -> dict:
    """Headline metrics. Sharpe uses daily marks, which is the convention here."""
    import math
    from statistics import fmean, stdev

    peak = result.initial_equity
    dd = 0.0
    daily: dict[str, float] = {}
    for ts, value in result.equity_curve:
        peak = max(peak, value)
        dd = min(dd, value / peak - 1)
        daily[ts[:10]] = value
    marks = list(daily.values())
    prev = [result.initial_equity] + marks[:-1]
    rets = [b / a - 1 for a, b in zip(prev, marks)]
    sd = stdev(rets) if len(rets) > 1 else 0.0
    pnl = [t.pnl for t in result.trades]
    gross_traded = sum(abs(f.cash_delta) for f in result.fills)
    return {
        "strategy": result.strategy,
        "interval": result.interval,
        "start": result.start[:10],
        "end": result.end[:10],
        "sessions": len(daily),
        "return_pct": round((result.final_equity / result.initial_equity - 1) * 100, 3),
        "max_drawdown_pct": round(dd * 100, 3),
        "sharpe": round(fmean(rets) / sd * math.sqrt(252), 3) if sd else 0.0,
        "final_equity": round(result.final_equity, 2),
        "trades": len(result.trades),
        "fills": len(result.fills),
        "turnover_x_equity": round(gross_traded / result.initial_equity, 2),
        "win_rate_pct": round(100 * sum(1 for p in pnl if p > 0) / len(pnl), 2) if pnl else None,
        "halted": result.halted,
    }
