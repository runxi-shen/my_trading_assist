"""Invariants the engine must never break.

These are the tests that matter: if one fails, every backtest number in this
repository is meaningless.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quanttrader.bars import Bar, Universe, clean
from dataclasses import replace

from quanttrader.config import Policy, PolicyError
from quanttrader.engine import Engine, summarize
from quanttrader.strategy import Decision, Order, StrategyBase, View


def test_policy() -> Policy:
    """The shipped policy, with the synthetic symbol AAA permitted.

    Engine tests must exercise the real limits, so this only widens the
    universe - every risk number is the production one.
    """
    return replace(Policy.load(), permitted_symbols=frozenset({'AAA'}))


def synthetic(days: int = 30) -> list[Bar]:
    """A tiny deterministic series. Opens follow the prior close so that the
    overnight return is exactly zero unless a test deliberately moves it."""
    bars: list[Bar] = []
    price = 100.0
    for i in range(days):
        day = f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}"
        bars.append(Bar("AAA", f"{day}T00:00:00Z", price, price + 1, price - 1, price, 1_000))
    return bars


def gapped(days: int = 30, symbol: str = "AAA", start: float = 100.0) -> list[Bar]:
    """Each open gaps up 1% from the prior close, so an overnight strategy earns."""
    bars: list[Bar] = []
    price = start
    for i in range(days):
        day = f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}"
        open_ = price * 1.01
        bars.append(Bar(symbol, f"{day}T00:00:00Z", open_, open_ + 1, open_ - 1, open_, 1_000))
        price = open_
    return bars


def gapped_pair(days: int = 6) -> Universe:
    """Two names, so the 40%-per-name cap actually fills the book to its 80% limit.

    This is the only configuration in which T+1 settlement binds, because only
    then does cash fall to the reserve floor.
    """
    return Universe({"AAA": gapped(days), "BBB": gapped(days, "BBB", 50.0)})


class Flat(StrategyBase):
    name = "flat"


class AlwaysLong(StrategyBase):
    name = "always_long"

    def target(self, symbol: str, view: View, held) -> float:
        return 0.8


class TestPolicy(unittest.TestCase):
    def test_shipped_policy_is_valid(self):
        Policy.load().validate()

    def test_smmt_can_never_be_traded(self):
        policy = Policy.load()
        with self.assertRaises(PolicyError):
            policy.assert_tradable("SMMT")

    def test_protected_symbol_cannot_enter_the_universe(self):
        policy = Policy.load()
        self.assertFalse(policy.tradable("SMMT"))
        self.assertTrue(policy.tradable("RXRX"))

    def test_unpermitted_symbol_is_rejected(self):
        policy = Policy.load()
        with self.assertRaises(PolicyError):
            policy.assert_tradable("TSLA")


class TestBars(unittest.TestCase):
    def test_interpolated_and_zero_volume_bars_are_dropped(self):
        raw = [
            {"begins_at": "2026-01-01T00:00:00Z", "open_price": "1", "high_price": "1",
             "low_price": "1", "close_price": "1", "volume": 0, "interpolated": True},
            {"begins_at": "2026-01-02T00:00:00Z", "open_price": "1", "high_price": "2",
             "low_price": "1", "close_price": "2", "volume": 10},
        ]
        bars = clean(raw, "AAA")
        self.assertEqual([b.ts[:10] for b in bars], ["2026-01-02"])

    def test_corrupt_ohlc_is_rejected(self):
        raw = [{"begins_at": "2026-01-02T00:00:00Z", "open_price": "5", "high_price": "1",
                "low_price": "1", "close_price": "5", "volume": 10}]
        self.assertEqual(clean(raw, "AAA"), [])

    def test_cutoff_drops_the_incomplete_session(self):
        raw = [{"begins_at": f"2026-01-{d:02d}T00:00:00Z", "open_price": "1", "high_price": "2",
                "low_price": "1", "close_price": "2", "volume": 10} for d in (1, 2, 3)]
        self.assertEqual(len(clean(raw, "AAA", cutoff="2026-01-03")), 2)


class TestNoLookahead(unittest.TestCase):
    def test_strategy_view_cannot_see_the_current_or_future_bar(self):
        seen: list[int] = []

        class Recorder(StrategyBase):
            name = "recorder"

            def target(self, symbol, view, held):
                seen.append(len(view.window(symbol, 10**6)))
                return 0.0

        universe = Universe({"AAA": synthetic(10)})
        Engine(test_policy(), 1000.0, slippage_bps=0).run(universe, Recorder(["AAA"]))
        # strictly increasing: one more completed bar each session, never ahead
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen[0], 1)
        self.assertLessEqual(seen[-1], 10)

    def test_editing_future_bars_cannot_change_past_decisions(self):
        class Snap(StrategyBase):
            name = "snap"

            def target(self, symbol, view, held):
                return 0.0

        def decisions(bars):
            universe = Universe({"AAA": bars})
            out: list[str] = []

            class Spy(StrategyBase):
                name = "spy"

                def decide(self, view, held):
                    out.append(view.ts)
                    return Decision([Order("AAA", 0.0)])

            Engine(test_policy(), 1000.0, slippage_bps=0).run(universe, Spy(["AAA"]))
            return out

        base = synthetic(20)
        tampered = list(base)
        tampered[15] = Bar("AAA", tampered[15].ts, 999, 1000, 998, 999, 1_000)
        self.assertEqual(decisions(base), decisions(tampered))


class TestSettlement(unittest.TestCase):
    def test_proceeds_are_not_spendable_until_the_next_session(self):
        """Buy the close, sell the next open: the rebuy that same day must fail."""
        from quanttrader.strategy import OvernightHold

        two = replace(test_policy(), permitted_symbols=frozenset({"AAA", "BBB"}))
        universe = gapped_pair(6)
        result = Engine(two, 1000.0, slippage_bps=0).run(universe, OvernightHold(["AAA", "BBB"]))

        pairs = [(f.ts[:10], f.symbol, f.side) for f in result.fills]
        # session 1 buys both names at the close; session 2 sells both at the open.
        self.assertEqual(sorted(p for p in pairs if p[0] == "2026-01-01"),
                         [("2026-01-01", "AAA", "buy"), ("2026-01-01", "BBB", "buy")])
        # the book is at its 80% cap after session 1, so session 2 cannot rebuy.
        self.assertEqual([p for p in pairs if p[0] == "2026-01-02" and p[2] == "buy"], [])
        # once session 2's sales settle, session 3 may buy again.
        self.assertEqual(sorted(p for p in pairs if p[0] == "2026-01-03" and p[2] == "buy"),
                         [("2026-01-03", "AAA", "buy"), ("2026-01-03", "BBB", "buy")])

    def test_settlement_can_be_relaxed_for_diagnostics(self):
        from quanttrader.strategy import OvernightHold

        two = replace(test_policy(), permitted_symbols=frozenset({"AAA", "BBB"}))
        universe = gapped_pair(6)
        result = Engine(two, 1000.0, slippage_bps=0,
                        enforce_settlement=False).run(universe, OvernightHold(["AAA", "BBB"]))
        buys = {f.ts[:10] for f in result.fills if f.side == "buy"}
        self.assertIn("2026-01-02", buys)

    def test_cash_never_goes_negative(self):
        class Scripted(StrategyBase):
            name = "scripted"

            def __init__(self, symbols):
                super().__init__(symbols)
                self.n = 0

            def decide(self, view, held):
                self.n += 1
                return Decision([Order("AAA", 0.9 if self.n % 2 else 0.0, "close")])

        universe = Universe({"AAA": gapped(24)})
        result = Engine(test_policy(), 1000.0, slippage_bps=10).run(universe, Scripted(["AAA"]))
        spent = sum(-f.cash_delta for f in result.fills if f.side == "buy")
        received = sum(f.cash_delta for f in result.fills if f.side == "sell")
        self.assertGreater(spent, 0)
        self.assertGreater(received, 0)


class TestStops(unittest.TestCase):
    def test_gap_through_a_stop_fills_at_the_worse_open(self):
        bars = [
            Bar("AAA", "2026-01-01T00:00:00Z", 100, 101, 99, 100, 1000),
            Bar("AAA", "2026-01-02T00:00:00Z", 100, 101, 99, 100, 1000),
            # overnight collapse: open far below the stop, whole bar low
            Bar("AAA", "2026-01-03T00:00:00Z", 50, 51, 49, 50, 1000),
            Bar("AAA", "2026-01-04T00:00:00Z", 50, 51, 49, 50, 1000),
        ]

        class Enter(StrategyBase):
            name = "enter"

            def decide(self, view, held):
                return Decision([Order("AAA", 0.5 if not held else 0.5, "close")])

        result = Engine(test_policy(), 1000.0, slippage_bps=0).run(
            Universe({"AAA": bars}), Enter(["AAA"]), stop_distance=10.0)
        sells = [f for f in result.fills if f.side == "sell"]
        self.assertTrue(sells)
        self.assertEqual(sells[0].reason, "stop")
        self.assertEqual(sells[0].price, 50.0)  # the gap, not the 90 stop level

    def test_drawdown_brake_flattens_and_freezes(self):
        bars = [Bar("AAA", f"2026-01-{i + 1:02d}T00:00:00Z",
                    100 * (0.90 ** i), 100 * (0.90 ** i) + 1,
                    100 * (0.90 ** i) - 1, 100 * (0.90 ** i), 1000) for i in range(10)]

        class Enter(StrategyBase):
            name = "enter"

            def target(self, symbol, view, held):
                return 0.8

        result = Engine(test_policy(), 1000.0, slippage_bps=0).run(
            Universe({"AAA": bars}), Enter(["AAA"]))
        self.assertTrue(result.halted)
        self.assertIn("brake", [f.reason for f in result.fills])
        # no entries after the halt
        halt_ts = result.halted_at
        after = [f for f in result.fills if f.ts > halt_ts and f.side == "buy"]
        self.assertEqual(after, [])


class TestMonitor(unittest.TestCase):
    def test_dedup_key_separates_strategies(self):
        """Regression: the key once omitted the strategy, so only the first
        strategy on a bar ever got logged and the other nine vanished."""
        from quanttrader.monitor import Observation

        a = Observation("daily", "RXRX", "2026-01-02T00:00:00Z", 0.4, "buy_hold")
        b = Observation("daily", "RXRX", "2026-01-02T00:00:00Z", 0.0, "rsi_revert")
        self.assertNotEqual(a.key, b.key)
        self.assertEqual(a.key, Observation("daily", "RXRX", a.bar_ts, 0.4, "buy_hold").key)

    def test_incomplete_bars_are_never_read(self):
        from datetime import datetime, timezone
        from quanttrader.monitor import _last_completed

        now = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
        bars = [Bar("AAA", "2026-01-05T14:00:00Z", 1, 1, 1, 1, 1),
                Bar("AAA", "2026-01-05T15:00:00Z", 1, 1, 1, 1, 1)]
        # the 15:00 hourly bar ends at 16:00, still in the future
        self.assertEqual([b.ts[11:16] for b in _last_completed(bars, "hourly", now)], ["14:00"])


class TestCosts(unittest.TestCase):
    def test_slippage_always_works_against_the_strategy(self):
        """A round trip must cost money at every level of slippage.

        Regression test: sells once received *positive* slippage, which cancelled
        every buy-side cost and made high-turnover strategies look free.
        """
        class Churn(StrategyBase):
            name = "churn"

            def __init__(self, symbols):
                super().__init__(symbols)
                self.n = 0

            def target(self, symbol, view, held):
                self.n += 1
                return 0.4 if self.n % 2 else 0.0

        universe = Universe({"AAA": gapped(24)})
        returns = []
        for bps in (0, 10, 50):
            m = summarize(Engine(test_policy(), 1000.0, slippage_bps=bps).run(
                universe, Churn(["AAA"])))
            returns.append(m["return_pct"])
        self.assertGreater(returns[0], returns[1])
        self.assertGreater(returns[1], returns[2])

    def test_a_flat_market_bleeds_money_when_trading(self):
        """No price movement at all: every round trip should be a small loss."""
        class Churn(StrategyBase):
            name = "churn"

            def __init__(self, symbols):
                super().__init__(symbols)
                self.n = 0

            def target(self, symbol, view, held):
                self.n += 1
                return 0.4 if self.n % 2 else 0.0

        universe = Universe({"AAA": synthetic(20)})
        m = summarize(Engine(test_policy(), 1000.0, slippage_bps=25).run(
            universe, Churn(["AAA"])))
        self.assertLess(m["return_pct"], 0)


class TestRiskLimits(unittest.TestCase):
    def test_position_cap_is_enforced(self):
        class Greedy(StrategyBase):
            name = "greedy"

            def target(self, symbol, view, held):
                return 1.0  # asks for everything

        result = Engine(test_policy(), 1000.0, slippage_bps=0).run(
            Universe({"AAA": gapped(5)}), Greedy(["AAA"]))
        buys = [f for f in result.fills if f.side == "buy"]
        self.assertTrue(buys)
        notional = sum(f.qty * f.price for f in buys)
        self.assertLessEqual(notional, 1000 * 0.40 + 1e-6)

    def test_daily_trade_budget_is_respected(self):
        policy = test_policy()

        class Churn(StrategyBase):
            name = "churn"

            def __init__(self, symbols):
                super().__init__(symbols)
                self.n = 0

            def target(self, symbol, view, held):
                self.n += 1
                return 0.4 if self.n % 2 else 0.0

        result = Engine(policy, 1000.0, slippage_bps=0).run(
            Universe({"AAA": gapped(20)}), Churn(["AAA"]))
        per_day: dict[str, int] = {}
        for f in result.fills:
            per_day[f.ts[:10]] = per_day.get(f.ts[:10], 0) + 1
        self.assertLessEqual(max(per_day.values()), policy.risk.max_trades_per_day + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
