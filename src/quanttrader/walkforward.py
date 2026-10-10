"""Walk-forward evaluation.

The only number that counts here is the out-of-sample one. Parameters are
chosen on a training window, frozen, then applied to the next window that the
selection never saw. Anything selected after looking at the whole sample is
reported separately and labelled as exploratory.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, stdev
from typing import Iterable, Sequence

from .bars import Universe
from .config import Policy
from .engine import Engine, Result, summarize
from .strategy import Strategy


@dataclass(frozen=True, slots=True)
class Fold:
    train_start: str
    train_end: str
    test_end: str

    @property
    def label(self) -> str:
        return f"{self.train_start}..{self.train_end}|{self.train_end}..{self.test_end}"


def make_folds(timeline: Sequence[str], n_folds: int = 4, train_frac: float = 0.5) -> list[Fold]:
    """Anchored folds: the training window always starts at the same bar."""
    if len(timeline) < 40:
        return []
    start = timeline[0][:10]
    usable = len(timeline)
    span = usable // (n_folds + 1)
    folds: list[Fold] = []
    train_end_i = int(usable * train_frac)
    step = max(1, (usable - train_end_i) // n_folds)
    for k in range(n_folds):
        te = train_end_i + k * step
        tx = min(usable - 1, te + step)
        if te <= 0 or tx <= te:
            break
        folds.append(Fold(start, timeline[te][:10], timeline[tx][:10]))
    return folds


def rebase(result: Result, start: str) -> dict:
    """Metrics computed only on marks at or after `start`.

    The strategy has been running (and holding positions) before `start`; that is
    realistic. What we refuse to do is count pre-`start` profit.
    """
    curve = [(ts, v) for ts, v in result.equity_curve if ts[:10] >= start]
    if len(curve) < 2:
        return {"return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0,
                "sessions": 0, "trades": 0, "halted": result.halted, "final_equity": 0.0}
    base = curve[0][1]
    peak, dd = base, 0.0
    daily: dict[str, float] = {}
    for ts, v in curve:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
        daily[ts[:10]] = v
    marks = list(daily.values())
    prev = [base] + marks[:-1]
    rets = [b / a - 1 for a, b in zip(prev, marks)]
    sd = stdev(rets) if len(rets) > 1 else 0.0
    trades = [t for t in result.trades if t.exit_ts[:10] >= start]
    wins = [t.pnl for t in trades]
    return {
        "start": curve[0][0][:10],
        "end": curve[-1][0][:10],
        "sessions": len(daily),
        "return_pct": round((curve[-1][1] / base - 1) * 100, 3),
        "max_drawdown_pct": round(dd * 100, 3),
        "sharpe": round(fmean(rets) / sd * math.sqrt(252), 3) if sd else 0.0,
        "final_equity": round(curve[-1][1], 2),
        "trades": len(trades),
        "win_rate_pct": round(100 * sum(1 for w in wins if w > 0) / len(wins), 2) if wins else None,
        "halted": result.halted,
    }


def _score(train: dict, min_trades: int) -> float | None:
    """Training score. A candidate that halts or barely trades is not eligible."""
    if train["halted"] or train["trades"] < min_trades:
        return None
    return train["sharpe"] if train["return_pct"] > 0 else None


def walk_forward(universe: Universe, candidates: dict[str, Strategy], policy: Policy,
                 capital: float, folds: Sequence[Fold], min_trades: int = 6,
                 slippage_bps: float | None = None, stop_distance: float | None = None,
                 ) -> dict:
    results = []
    for fold in folds:
        trained = {}
        for name, strat in candidates.items():
            res = Engine(policy, capital, slippage_bps=slippage_bps).run(
                universe, strat, start=fold.train_start, end=fold.train_end,
                stop_distance=stop_distance)
            trained[name] = rebase(res, fold.train_start)
        scored = {n: _score(t, min_trades) for n, t in trained.items()}
        eligible = {n: s for n, s in scored.items() if s is not None}
        chosen = max(eligible, key=lambda n: eligible[n]) if eligible else None
        entry = {"fold": fold.label, "train": trained, "chosen": chosen,
                 "train_score": eligible.get(chosen) if chosen else None,
                 "no_candidate_qualified": chosen is None, "test": None}
        if chosen:
            res = Engine(policy, capital, slippage_bps=slippage_bps).run(
                universe, candidates[chosen], start=fold.train_start, end=fold.test_end,
                stop_distance=stop_distance)
            entry["test"] = rebase(res, fold.train_end)
            entry["test_all"] = {
                n: rebase(Engine(policy, capital, slippage_bps=slippage_bps).run(
                    universe, s, start=fold.train_start, end=fold.test_end,
                    stop_distance=stop_distance), fold.train_end)
                for n, s in candidates.items()}
        results.append(entry)
    return {"folds": results, "aggregate": _aggregate(results)}


def _aggregate(folds: list[dict]) -> dict:
    """Chain the out-of-sample windows: one continuous net-of-cost track record."""
    equity = 1.0
    picked: list[str] = []
    dd = 0.0
    per_fold = []
    for f in folds:
        test = f.get("test")
        if not test:
            per_fold.append({"fold": f["fold"], "chosen": None, "return_pct": 0.0})
            continue
        equity *= 1 + test["return_pct"] / 100
        dd = min(dd, test["max_drawdown_pct"] / 100)
        picked.append(f["chosen"])
        per_fold.append({"fold": f["fold"], "chosen": f["chosen"],
                         "return_pct": test["return_pct"], "dd": test["max_drawdown_pct"],
                         "trades": test["trades"], "halted": test["halted"]})
    return {
        "out_of_sample_return_pct": round((equity - 1) * 100, 3),
        "worst_window_drawdown_pct": round(dd * 100, 3),
        "selections": picked,
        "per_fold": per_fold,
        "folds_with_a_qualifying_candidate": sum(1 for f in folds if f.get("chosen")),
        "folds": len(folds),
    }


def stress(universe: Universe, strategy: Strategy, policy: Policy, capital: float,
           start: str, end: str, bps: Iterable[float] | None = None,
           stop_distance: float | None = None) -> dict[str, dict]:
    """Re-run one frozen strategy across execution-cost assumptions."""
    out = {}
    for b in (bps if bps is not None else (0, policy.execution.slippage_bps_each_side, *policy.execution.stress_slippage_bps)):
        res = Engine(policy, capital, slippage_bps=b).run(
            universe, strategy, start=start, end=end, stop_distance=stop_distance)
        out[str(b)] = summarize(res)
    return out
