"""Read-only forward observation.

This is the only component that runs unattended. It fetches bars, evaluates the
frozen candidate signals on *completed* bars, and appends what it saw to a log.
It cannot place an order: it holds no broker, and it imports no execution code.

Design rules:
  * never overwrite the archived research snapshots
  * never re-tune a threshold from new data
  * deduplicate by (interval, symbol, bar timestamp) so restarts are safe
  * report failures loudly and stay quiet about non-events
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .bars import Bar
from .config import Policy
from .feed import RobinhoodFeed
from .strategy import Strategy, View

STATUS_FILE = "monitor_state.json"
SIGNAL_LOG = "forward_signals.jsonl"
HEALTH_LOG = "monitor_health.jsonl"


@dataclass(frozen=True, slots=True)
class Observation:
    interval: str
    symbol: str
    bar_ts: str
    target_weight: float
    strategy: str = ""
    note: str = ""

    @property
    def key(self) -> str:
        # The strategy name MUST be part of the key. Without it, the first
        # strategy to log a bar hides every other strategy on that same bar.
        return f"{self.interval}|{self.strategy}|{self.symbol}|{self.bar_ts}"


def _load_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text().splitlines():
        if line.strip():
            keys.add(json.loads(line)["key"])
    return keys


def _append(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _last_completed(bars: list[Bar], interval: str, now: datetime) -> list[Bar]:
    """Return only bars that have definitely finished by `now`.

    Robinhood stamps a bar with its *start*. Anything whose end is still in the
    future is dropped, otherwise the monitor would read a half-formed bar.
    """
    width = timedelta(hours=1) if interval == "hourly" else timedelta(days=1)
    out = []
    for b in bars:
        ts = datetime.fromisoformat(b.ts.replace("Z", "+00:00"))
        if ts + width <= now:
            out.append(b)
    return out


def observe(policy: Policy, symbols: list[str], interval: str, days_back: int,
            strategies: dict[str, Strategy], results_dir: Path,
            now: datetime | None = None) -> dict:
    """Fetch, evaluate, and log. Returns a summary of what happened."""
    now = now or datetime.now(timezone.utc)
    for s in symbols:
        if s in policy.protected_symbols:
            raise ValueError(f"refusing to monitor protected symbol {s}")

    results_dir.mkdir(parents=True, exist_ok=True)
    state_path = results_dir / STATUS_FILE
    signal_path = results_dir / SIGNAL_LOG
    health_path = results_dir / HEALTH_LOG

    granularity = "hour" if interval == "hourly" else "day"
    start = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")

    summary = {"interval": interval, "fetched": 0, "new": 0, "error": None,
               "observed_at": now.isoformat()}
    try:
        bars = RobinhoodFeed().fetch(symbols, start, interval=granularity)
    except Exception as exc:  # surfaced, never hidden
        summary["error"] = f"{type(exc).__name__}: {exc}"
        _append(health_path, [{"ts": now.isoformat(), "interval": interval,
                               "ok": False, "error": summary["error"]}])
        return summary

    bars = {s: _last_completed(v, interval, now) for s, v in bars.items()}
    summary["fetched"] = sum(len(v) for v in bars.values())
    if not bars or any(not v for v in bars.values()):
        summary["error"] = "at least one symbol has no completed bars"
        _append(health_path, [{"ts": now.isoformat(), "interval": interval,
                               "ok": False, "error": summary["error"]}])
        return summary

    seen = _load_keys(signal_path)
    fresh: list[dict] = []
    for name, strat in strategies.items():
        # Evaluate every completed bar in the window, using only data up to it.
        ts_list = [t for t in sorted({b.ts for b in bars[symbols[0]]})]
        for ts in ts_list:
            idx = {s: next((i for i, b in enumerate(bars[s]) if b.ts == ts), None) for s in symbols}
            if any(v is None for v in idx.values()):
                continue
            view = View(ts=ts, day=ts[:10], bars_by_symbol={s: bars[s] for s in symbols},
                        index={s: idx[s] for s in symbols})  # type: ignore[index]
            decision = strat.decide(view, {})
            for order in decision.orders:
                obs = Observation(interval, order.symbol, ts, order.target_weight, name,
                                  decision.note)
                if obs.key in seen:
                    continue
                seen.add(obs.key)
                fresh.append({"key": obs.key, "ts": now.isoformat(), "interval": interval,
                              "symbol": order.symbol, "bar_ts": ts,
                              "strategy": name, "target_weight": order.target_weight,
                              "when": order.when, "note": decision.note})

    _append(signal_path, fresh)
    summary["new"] = len(fresh)
    _append(health_path, [{"ts": now.isoformat(), "interval": interval, "ok": True,
                           "fetched": summary["fetched"], "new": len(fresh)}])
    state_path.write_text(json.dumps({
        "last_run": now.isoformat(), "interval": interval,
        "observations_logged": len(seen), "symbols": symbols,
    }, indent=2) + "\n")
    return summary


def should_alert(summary: dict) -> str | None:
    """Only meaningful changes or failures. Silence otherwise."""
    if summary.get("error"):
        return f"[quanttrader] {summary['interval']} monitor FAILED: {summary['error']}"
    if summary.get("new", 0) > 0:
        return f"[quanttrader] {summary['new']} new {summary['interval']} signal(s) logged"
    return None
