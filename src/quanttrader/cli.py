"""Command line entry points.

Deliberately small. Anything that could trade lives behind `risk.RiskManager`
and is not reachable from here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Policy
from .feed import SnapshotFeed
from .monitor import observe, should_alert
from .research import battery

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ("RXRX", "TEM")


def status() -> int:
    """Print the policy and data state. The first thing to run anywhere new."""
    policy = Policy.load()
    out = {
        "account_env_var": policy.account.account_number_env,
        "account_configured": policy.account.identified,
        "account_type": policy.account.type,
        "protected": sorted(policy.protected_symbols),
        "permitted": sorted(policy.permitted_symbols),
        "rotation_candidates": sorted(policy.rotation_candidates),
        "quarantined": sorted(policy.quarantined_symbols),
        "max_weight_per_symbol": policy.risk.max_weight_per_symbol,
        "cash_reserve_fraction": policy.risk.cash_reserve_fraction,
        "max_drawdown": policy.risk.max_drawdown,
        "live_orders_enabled": policy.safety.live_orders_enabled,
        "kill_switch_present": (ROOT / policy.safety.kill_switch_file).exists(),
        "snapshots": {},
    }
    for interval in ("daily", "hourly"):
        try:
            feed = SnapshotFeed(interval, cutoff="2026-10-09")
            out["snapshots"][interval] = {
                s: {"bars": len(feed.bars(s)),
                    "first": feed.bars(s)[0].ts[:10],
                    "last": feed.bars(s)[-1].ts[:10]}
                for s in feed.symbols() if s in UNIVERSE
            }
        except Exception as exc:
            out["snapshots"][interval] = {"error": str(exc)}
    print(json.dumps(out, indent=2))
    return 0


def monitor(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="quant-monitor")
    ap.add_argument("--interval", choices=("hourly", "daily"), default="hourly")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--results", default=str(ROOT / "results"))
    args = ap.parse_args(argv)

    policy = Policy.load()
    strategies = battery(UNIVERSE, per_name=policy.risk.max_weight_per_symbol)
    summary = observe(policy, list(UNIVERSE), args.interval, args.days,
                      strategies, Path(args.results))
    alert = should_alert(summary)
    if alert:
        print(alert)
    elif not args.quiet:
        print(json.dumps(summary))
    return 1 if summary.get("error") else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: quanttrader <status|monitor> [options]")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        return status()
    if cmd == "monitor":
        return monitor(rest)
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
