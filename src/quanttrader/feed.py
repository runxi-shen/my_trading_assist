"""Bar sources.

Two implementations behind one shape: an archived snapshot (research, tests,
reproducible backtests) and the live Robinhood MCP (forward monitoring).
Nothing else in the codebase knows where bars come from.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from .bars import Bar, Universe, clean

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = ROOT / "data"

MCP_URL = "https://agent.robinhood.com/mcp/trading"
MCP_KEYCHAIN_ACCOUNT = "robinhood-trading|5cbe81c78ff5ae58"


class Feed(Protocol):
    def bars(self, symbol: str) -> list[Bar]: ...


class SnapshotFeed:
    """Reads `data/<interval>.json`, the frozen evidence used by backtests.

    These files are never overwritten by monitoring code.
    """

    def __init__(self, interval: str, root: Path | str = SNAPSHOT_DIR,
                 cutoff: str | None = None) -> None:
        self.interval = interval
        self.cutoff = cutoff
        path = Path(root) / f"{interval}.json"
        raw = json.loads(path.read_text())
        self._raw = {r["symbol"]: r["bars"] for r in raw["results"]}

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._raw))

    def bars(self, symbol: str) -> list[Bar]:
        return clean(self._raw[symbol], symbol, self.cutoff)

    def universe(self, symbols: Sequence[str] | None = None) -> Universe:
        chosen = symbols or self.symbols()
        return Universe({s: self.bars(s) for s in chosen})


class RobinhoodFeed:
    """Live bars from the Robinhood MCP.

    Read-only: this class only ever calls `get_equity_historicals`.
    """

    def __init__(self, url: str = MCP_URL, keychain_account: str = MCP_KEYCHAIN_ACCOUNT) -> None:
        self.url = url
        self.account = keychain_account

    def _token(self) -> str:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Codex MCP Credentials",
             "-a", self.account, "-w"],
            capture_output=True, text=True, check=True).stdout
        return json.loads(raw)["token_response"]["access_token"]

    def _call(self, method: str, params: dict, rid: int = 1) -> dict:
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=body, headers={
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        raw = urllib.request.urlopen(req, timeout=60).read().decode()
        for line in raw.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return json.loads(raw)

    def fetch(self, symbols: Sequence[str], start: str, end: str | None = None,
              interval: str = "day", bounds: str = "regular") -> dict[str, list[Bar]]:
        out: dict[str, list[Bar]] = {}
        for i in range(0, len(symbols), 10):
            args: dict = {"symbols": list(symbols[i:i + 10]), "start_time": start,
                          "interval": interval, "bounds": bounds, "adjustment_type": "split"}
            if end:
                args["end_time"] = end
            resp = self._call("tools/call", {"name": "get_equity_historicals", "arguments": args}, rid=2)
            if resp.get("result", {}).get("isError"):
                raise RuntimeError(f"get_equity_historicals failed: {resp['result']}")
            payload = resp["result"].get("structuredContent") or {}
            data = payload.get("data", payload)
            for r in data["results"]:
                out[r["symbol"]] = clean(r["bars"], r["symbol"])
        return out

    def universe(self, symbols: Sequence[str], start: str, end: str | None = None,
                 interval: str = "day") -> Universe:
        return Universe(self.fetch(symbols, start, end, interval))


def write_snapshot(path: Path | str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, indent=None, separators=(",", ":")) + "\n")
