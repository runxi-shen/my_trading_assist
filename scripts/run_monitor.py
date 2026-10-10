#!/usr/bin/env python3
"""Read-only forward observation. Safe to run unattended on a schedule.

    python3 scripts/run_monitor.py --interval hourly --days 90
    python3 scripts/run_monitor.py --interval daily  --days 400

Fetches bars from the Robinhood MCP, evaluates the FROZEN candidate signals on
completed bars only, and appends what it saw to results/forward_signals.jsonl.

It places no orders, re-tunes nothing, and overwrites no archived research data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quanttrader.config import Policy
from quanttrader.monitor import observe, should_alert
from quanttrader.research import battery

UNIVERSE = ("RXRX", "TEM")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", choices=("hourly", "daily"), default="hourly")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--quiet", action="store_true", help="only print when there is an alert")
    ap.add_argument("--results", default=str(ROOT / "results"))
    args = ap.parse_args()

    policy = Policy.load()
    if policy.safety.live_orders_enabled:
        print("WARNING: live_orders_enabled is true in policy.toml, but this monitor "
              "never places orders.", file=sys.stderr)

    strategies = battery(UNIVERSE, per_name=policy.risk.max_weight_per_symbol)
    summary = observe(policy, list(UNIVERSE), args.interval, args.days,
                      strategies, Path(args.results))
    alert = should_alert(summary)
    if alert:
        print(alert)
    elif not args.quiet:
        print(json.dumps(summary))
    return 1 if summary.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
