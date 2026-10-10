import copy
import unittest

import backtest as bt


class BacktestTests(unittest.TestCase):
    def test_policy_protects_smmt(self):
        bt.validate_policy()

    def test_future_bars_cannot_change_earlier_indicators(self):
        rows = [{"time":str(i),"open":100+i*.1,"high":101+i*.1,"low":99+i*.1,"close":100+i*.1} for i in range(160)]
        changed = copy.deepcopy(rows)
        for b in changed[130:]:
            b.update(open=1000,high=1001,low=999,close=1000)
        self.assertEqual(bt.indicators(rows)[:130],bt.indicators(changed)[:130])

    def example(self, gap=False):
        times = ["2026-06-01T09:00:00Z","2026-06-01T10:00:00Z",
                 "2026-06-01T11:00:00Z","2026-06-01T12:00:00Z","2026-06-02T10:00:00Z"]
        bars = [{"time":t,"day":t[:10],"open":100.,"close":100.,"high":101.,"low":100.} for t in times]
        bars[2].update(open=50. if gap else 100.,low=49. if gap else 99.,close=50. if gap else 100.)
        series = {s:copy.deepcopy(bars) for s in bt.SYMBOLS}
        inds = {s:[{"mean":200.,"z":-3.,"trend":90.,"atr":.05,"breakout":200.,"exit_low":1.} for _ in bars] for s in bt.SYMBOLS}
        return series,inds

    def test_cash_not_reused_before_next_trading_day(self):
        series,inds = self.example()
        _,_,trades = bt.simulate(series,inds,"hourly","range","2026-06-01","2026-06-03",bps=0)
        rx = [t for t in trades if t["symbol"]=="RXRX"]
        self.assertEqual(rx[0]["entry_time"],"2026-06-01T10:00:00Z")
        self.assertEqual(rx[1]["entry_time"],"2026-06-02T10:00:00Z")
        self.assertEqual(rx[0]["settlement_day"],"2026-06-02")

    def test_gap_stop_fills_at_worse_open_and_brakes(self):
        series,inds = self.example(gap=True)
        metric,_,trades = bt.simulate(series,inds,"hourly","range","2026-06-01","2026-06-03",bps=0)
        self.assertTrue(metric["halted"])
        self.assertEqual(trades[0]["exit_fill"],50.)
        self.assertLess(metric["max_drawdown_pct"],-10)
        self.assertEqual(len(trades),2)


if __name__ == "__main__":
    unittest.main()
