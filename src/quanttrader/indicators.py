"""Streaming-safe indicators.

Every function takes a sequence and returns a list the same length, with `None`
where there is not yet enough history. Callers index the tail, so an indicator
computed at bar i can never see bar i+1.
"""
from __future__ import annotations

import math
from statistics import fmean, stdev
from typing import Sequence


def sma(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def ema(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = fmean(values[:n])
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rolling_sd(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(n - 1, len(values)):
        out[i] = stdev(values[i - n + 1 : i + 1])
    return out


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int) -> list[float | None]:
    """Average true range. Needs n+1 bars because TR uses the prior close."""
    trs: list[float] = []
    for i, (h, l, c) in enumerate(zip(highs, lows, closes)):
        prev = closes[i - 1] if i else c
        trs.append(max(h - l, abs(h - prev), abs(l - prev)))
    out: list[float | None] = [None] * len(closes)
    run = 0.0
    for i, tr in enumerate(trs):
        run += tr
        if i >= n:
            run -= trs[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def rsi(closes: Sequence[float], n: int = 2) -> list[float | None]:
    """Wilder RSI. Default n=2 gives the classic short-term mean-reversion signal."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def zscore(closes: Sequence[float], n: int) -> list[float | None]:
    means = sma(closes, n)
    sds = rolling_sd(closes, n)
    out: list[float | None] = [None] * len(closes)
    for i, (m, s) in enumerate(zip(means, sds)):
        if m is not None and s:
            out[i] = (closes[i] - m) / s
    return out


def donchian(highs: Sequence[float], lows: Sequence[float], n: int) -> tuple[list[float | None], list[float | None]]:
    """Highest high / lowest low of the n bars *before* the current one."""
    upper: list[float | None] = [None] * len(highs)
    lower: list[float | None] = [None] * len(lows)
    for i in range(n, len(highs)):
        upper[i] = max(highs[i - n : i])
        lower[i] = min(lows[i - n : i])
    return upper, lower


def realized_vol(closes: Sequence[float], n: int, periods_per_year: int) -> list[float | None]:
    """Annualised close-to-close volatility."""
    rets = [None] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    out: list[float | None] = [None] * len(closes)
    for i in range(n, len(closes)):
        window = [r for r in rets[i - n + 1 : i + 1] if r is not None]
        if len(window) > 1 and stdev(window) > 0:
            out[i] = stdev(window) * math.sqrt(periods_per_year)
    return out
