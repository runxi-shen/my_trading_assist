"""The research study: candidate battery, walk-forward, stress, and the goal test.

Everything here is reproducible from `data/*.json` alone. Nothing in this module
may place an order or touch a broker.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean, stdev

from .bars import Universe, session_days
from .config import Policy
from .engine import Engine, summarize
from .feed import SnapshotFeed
from .strategy import (BollingerRevert, BuyHold, DipBuyTrend, OvernightHold, RsiRevert,
                       Strategy, TrendFollow, VolTargetHold)

DEFAULT_UNIVERSE = ("RXRX", "TEM")


def battery(symbols: tuple[str, ...], per_name: float) -> dict[str, Strategy]:
    """The candidate set. Every entry is a distinct, falsifiable hypothesis."""
    w = per_name
    return {
        "buy_hold": BuyHold(list(symbols), weight=w * len(symbols)),
        "overnight_hold": OvernightHold(list(symbols), weight=w * len(symbols)),
        "vol_target": VolTargetHold(list(symbols), target_vol=0.12, max_weight=w * len(symbols)),
        "bollinger_1.0": BollingerRevert(list(symbols), entry_z=1.0, per_symbol_weight=w),
        "bollinger_1.5": BollingerRevert(list(symbols), entry_z=1.5, per_symbol_weight=w),
        "bollinger_2.0": BollingerRevert(list(symbols), entry_z=2.0, per_symbol_weight=w),
        "dip_buy_1.0": DipBuyTrend(list(symbols), drop_sd=1.0, per_symbol_weight=w),
        "dip_buy_1.5": DipBuyTrend(list(symbols), drop_sd=1.5, per_symbol_weight=w),
        "trend_follow": TrendFollow(list(symbols), per_symbol_weight=w),
        "rsi_revert": RsiRevert(list(symbols), per_symbol_weight=w),
    }


def data_quality(universe: Universe) -> dict:
    return {
        s: {
            "bars": len(bars),
            "first": bars[0].ts[:10],
            "last": bars[-1].ts[:10],
            "sessions": len(session_days(bars)),
            "median_dollar_volume": _median(b.close * b.volume for b in bars),
        }
        for s, bars in universe.series.items()
    }


def _median(xs) -> float:
    vals = sorted(xs)
    return vals[len(vals) // 2] if vals else 0.0


def goal_test(capital: float, target_multiple: float, sessions: int,
              daily_vol: float, sharpes=(0.0, 0.5, 1.0, 1.53),
              paths: int = 20_000, seed: int = 7, max_drawdown: float = 0.10) -> dict:
    """Can the stated goal be reached inside the stated risk limit?

    The simulation halts permanently on the drawdown brake, because that is what
    the policy does. A goal that mostly ends in a halt is not a strategy.
    """
    rng = random.Random(seed)
    need = math.log(target_multiple)
    horizon_sd = daily_vol * math.sqrt(sessions)
    out = {"sessions": sessions, "required_return_pct": (target_multiple - 1) * 100,
           "daily_vol": daily_vol, "horizon_sd_pct": horizon_sd * 100,
           "sigma_needed": need / horizon_sd,
           "p_double_zero_drift": 0.5 * math.erfc((need / horizon_sd) / math.sqrt(2)),
           "by_sharpe": {}}

    for sh in sharpes:
        mu_h = sh / math.sqrt(252) * daily_vol * sessions
        p = 0.5 * math.erfc(((need - mu_h) / horizon_sd) / math.sqrt(2))
        ends, halts = [], 0
        daily_mu = sh / math.sqrt(252) * daily_vol
        for _ in range(paths):
            eq, peak = 1.0, 1.0
            stopped = False
            for _ in range(sessions):
                eq *= math.exp(rng.gauss(daily_mu, daily_vol) - 0.5 * daily_vol ** 2)
                peak = max(peak, eq)
                if eq / peak - 1 <= -max_drawdown:
                    stopped = True
                    break
            ends.append(eq)
            halts += stopped
        ends.sort()
        out["by_sharpe"][f"{sh:.2f}"] = {
            "expected_horizon_return_pct": round(mu_h * 100, 2),
            "p_reach_target": round(sum(1 for e in ends if capital * e >= capital * target_multiple) / len(ends), 6),
            "prob_brake_halts": round(halts / len(ends), 4),
            "median_end": round(capital * ends[len(ends) // 2], 2),
            "p05_end": round(capital * ends[int(0.05 * len(ends))], 2),
            "p95_end": round(capital * ends[int(0.95 * len(ends))], 2),
        }
    return out


def portfolio_vol(sd_a: float, sd_b: float, corr: float, gross: float) -> float:
    w = gross / 2
    return math.sqrt(w * w * sd_a ** 2 + w * w * sd_b ** 2 + 2 * w * w * corr * sd_a * sd_b)


@dataclass
class StudyConfig:
    # Neutral unit. The study is scale-free: every result is a percentage,
    # so no real account balance is published here.
    capital: float = 1000.00
    interval: str = "daily"
    cutoff: str = "2026-10-09"
    folds: int = 4
    train_frac: float = 0.5
    exposures: tuple[float, ...] = (0.10, 0.20, 0.30, 0.40, 0.60, 0.80)
    target_multiple: float = 2.0
    sessions_to_goal: int = 59


def describe_stats(bars_a, bars_b) -> dict:
    ra = [bars_a[i].close / bars_a[i - 1].close - 1 for i in range(1, len(bars_a))]
    rb = [bars_b[i].close / bars_b[i - 1].close - 1 for i in range(1, len(bars_b))]
    n = min(len(ra), len(rb))
    ra, rb = ra[-n:], rb[-n:]
    ma, mb = fmean(ra), fmean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    corr = cov / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return {"sd_a": stdev(ra), "sd_b": stdev(rb), "corr": corr, "n": n,
            "mean_a": ma, "mean_b": mb}
