"""Tests for the safety layer: the things that must never be bypassable."""
from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quanttrader.config import Policy
from quanttrader.execution import BrokerError, OrderRequest, PaperBroker, RobinhoodBroker
from quanttrader.risk import RiskManager


def policy(**over) -> Policy:
    return replace(Policy.load(), **over)


class TestRiskGate(unittest.TestCase):
    def base(self, **kw):
        args = dict(symbol="RXRX", side="buy", notional=100.0, equity=1000.0, cash=1000.0,
                    position_value=0.0, gross_exposure=0.0, held_qty=0.0, order_qty=1.0,
                    trades_today=0, turnover_today=0.0)
        args.update(kw)
        return args

    def test_clean_order_is_allowed(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        self.assertTrue(rm.check(**self.base()).ok)

    def test_protected_symbol_is_blocked_even_for_a_sell(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        v = rm.check(**self.base(symbol="SMMT", side="sell", held_qty=10, order_qty=10))
        self.assertFalse(v.ok)
        self.assertTrue(any("protected" in r for r in v.reasons))

    def test_symbol_outside_the_universe_is_blocked(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        self.assertFalse(rm.check(**self.base(symbol="TSLA")).ok)

    def test_shorting_is_blocked_even_though_long_only_is_set(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        v = rm.check(**self.base(side="sell", held_qty=1, order_qty=5))
        self.assertFalse(v.ok)

    def test_cash_reserve_is_never_spendable(self):
        # widen the caps so the reserve is the only thing that can refuse the order
        wide = policy(risk=replace(policy().risk, max_weight_per_symbol=0.95,
                                   max_gross_exposure=0.95))
        rm = RiskManager(wide)
        rm.observe(1000.0)
        # cash 1000, reserve 20% -> 800 spendable; ask for 900
        v = rm.check(**self.base(notional=900.0))
        self.assertFalse(v.ok)
        self.assertTrue(any("reserve" in r or "settled cash" in r for r in v.reasons))
        self.assertTrue(rm.check(**self.base(notional=700.0)).ok)

    def test_per_name_cap_is_enforced(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        v = rm.check(**self.base(notional=100.0, position_value=380.0))
        self.assertFalse(v.ok)
        self.assertTrue(any("per-name cap" in r for r in v.reasons))

    def test_gross_exposure_cap_is_enforced(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        v = rm.check(**self.base(notional=100.0, gross_exposure=750.0))
        self.assertFalse(v.ok)
        self.assertTrue(any("gross exposure" in r for r in v.reasons))

    def test_drawdown_brake_halts_and_stays_halted(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        rm.observe(880.0)
        self.assertTrue(rm.halted)
        # recovering the equity must NOT silently resume trading
        rm.observe(1200.0)
        self.assertTrue(rm.halted)
        self.assertFalse(rm.check(**self.base(equity=1200.0)).ok)

    def test_only_a_human_reset_clears_a_halt(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        rm.observe(850.0)
        self.assertTrue(rm.halted)
        rm.reset_after_review()
        self.assertFalse(rm.halted)
        self.assertTrue(rm.check(**self.base()).ok)

    def test_kill_switch_file_blocks_everything(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rm = RiskManager(policy(), root=root)
            rm.observe(1000.0)
            (root / "KILL").write_text("stop")
            v = rm.check(**self.base())
            self.assertFalse(v.ok)
            self.assertTrue(any("kill switch" in r for r in v.reasons))

    def test_daily_trade_budget(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        self.assertFalse(rm.check(**self.base(trades_today=99)).ok)

    def test_turnover_cap(self):
        rm = RiskManager(policy())
        rm.observe(1000.0)
        self.assertFalse(rm.check(**self.base(turnover_today=1200.0)).ok)


class TestBrokerBoundary(unittest.TestCase):
    def test_live_broker_refuses_to_exist_while_disabled(self):
        with self.assertRaises(BrokerError):
            RobinhoodBroker(policy())

    def test_paper_broker_accepts_only_what_risk_accepts(self):
        pb = PaperBroker(policy(), cash=1000.0)
        ok = pb.submit(OrderRequest("RXRX", "buy", 1.0, limit_price=100.0),
                       price=100.0, equity=1000.0, cash=1000.0, position_value=0.0,
                       gross_exposure=0.0, held_qty=0.0, trades_today=0, turnover_today=0.0)
        self.assertTrue(ok.ok)
        self.assertEqual(pb.ledger.positions["RXRX"], 1.0)
        self.assertEqual(pb.ledger.cash, 900.0)

        bad = pb.submit(OrderRequest("SMMT", "buy", 1.0, limit_price=10.0),
                        price=10.0, equity=1000.0, cash=900.0, position_value=0.0,
                        gross_exposure=100.0, held_qty=0.0, trades_today=1, turnover_today=100.0)
        self.assertFalse(bad.ok)
        self.assertNotIn("SMMT", pb.ledger.positions)

    def test_paper_ledger_cannot_go_short(self):
        pb = PaperBroker(policy(), cash=1000.0)
        v = pb.submit(OrderRequest("RXRX", "sell", 5.0, limit_price=10.0),
                      price=10.0, equity=1000.0, cash=1000.0, position_value=0.0,
                      gross_exposure=0.0, held_qty=0.0, trades_today=0, turnover_today=0.0)
        self.assertFalse(v.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
