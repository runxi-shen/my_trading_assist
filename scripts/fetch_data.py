#!/usr/bin/env python3
"""Refresh bar snapshots for research.

    python3 scripts/fetch_data.py --interval daily  --start 2024-06-01
    python3 scripts/fetch_data.py --interval hourly --start 2025-11-01

Writes data/forward_<interval>.json. It NEVER overwrites data/daily.json or
data/hourly.json: those are the frozen evidence every published backtest cites.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quanttrader.feed import RobinhoodFeed, write_snapshot

UNIVERSE = ["RXRX", "TEM", "SPY"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", choices=("daily", "hourly"), default="daily")
    ap.add_argument("--start", default="2024-06-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--symbols", nargs="*", default=UNIVERSE)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    granularity = "day" if args.interval == "daily" else "hour"
    start = f"{args.start}T00:00:00Z"
    end = f"{args.end}T00:00:00Z" if args.end else None

    feed = RobinhoodFeed()
    fetched = feed.fetch(args.symbols, start, end, interval=granularity)
    payload = {
        "request": {"start": args.start, "end": args.end or "now", "bounds": "regular",
                    "adjustment": "split", "source": "Robinhood MCP get_equity_historicals"},
        "results": [{"symbol": s, "interval": granularity, "bounds": "regular", "bars": [
            {"begins_at": b.ts, "open_price": f"{b.open:.6f}", "close_price": f"{b.close:.6f}",
             "high_price": f"{b.high:.6f}", "low_price": f"{b.low:.6f}",
             "volume": int(b.volume), "session": "reg"} for b in bars]}
            for s, bars in fetched.items()],
    }
    name = args.out or f"forward_{args.interval}.json"
    path = ROOT / "data" / name
    if path.name in ("daily.json", "hourly.json"):
        print(f"refusing to overwrite the archived snapshot {path}", file=sys.stderr)
        return 2
    write_snapshot(path, payload)
    print(json.dumps({s: {"bars": len(b), "first": b[0].ts[:10], "last": b[-1].ts[:10]}
                      for s, b in fetched.items()}, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
