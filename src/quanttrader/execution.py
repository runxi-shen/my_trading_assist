"""Broker boundary.

The live broker is intentionally crippled: it refuses to place an order unless
the policy has `live_orders_enabled = true`, the kill switch is absent, and a
human has set an explicit session budget. Everything else goes to the paper
broker, which keeps an in-memory ledger of what *would* have happened.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .config import Policy
from .risk import RiskManager, Verdict, describe


class BrokerError(RuntimeError):
    """Raised when a call would do something the policy forbids."""


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    side: str          # "buy" | "sell"
    qty: float
    limit_price: float | None = None
    reason: str = ""
    strategy: str = ""

    @property
    def notional(self) -> float:
        return self.qty * (self.limit_price or 0.0)


@dataclass
class PaperLedger:
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    orders: list[dict] = field(default_factory=list)

    def fill(self, req: OrderRequest, price: float) -> None:
        notional = req.qty * price
        sign = -1 if req.side == "buy" else 1
        self.cash += sign * notional
        self.positions[req.symbol] = self.positions.get(req.symbol, 0.0) + (
            req.qty if req.side == "buy" else -req.qty
        )
        self.orders.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": req.symbol, "side": req.side, "qty": req.qty,
            "price": price, "reason": req.reason, "strategy": req.strategy,
        })


class Broker(Protocol):
    def submit(self, req: OrderRequest, price: float, *, equity: float, cash: float,
               position_value: float, gross_exposure: float, held_qty: float,
               trades_today: int, turnover_today: float) -> Verdict: ...


class PaperBroker:
    """Accepts what the risk gate accepts, and writes down what it would have done."""

    def __init__(self, policy: Policy, cash: float, log_path: Path | None = None) -> None:
        self.policy = policy
        self.ledger = PaperLedger(cash=cash)
        self.risk = RiskManager(policy)
        self.log_path = log_path

    def submit(self, req: OrderRequest, price: float, *, equity: float, cash: float,
               position_value: float, gross_exposure: float, held_qty: float,
               trades_today: int, turnover_today: float) -> Verdict:
        self.risk.observe(equity)
        verdict = self.risk.check(
            symbol=req.symbol, side=req.side, notional=req.notional, equity=equity,
            cash=cash, position_value=position_value, gross_exposure=gross_exposure,
            held_qty=held_qty, order_qty=req.qty, trades_today=trades_today,
            turnover_today=turnover_today)
        if verdict.ok:
            self.ledger.fill(req, price)
            self._log(req, price, verdict)
        return verdict

    def _log(self, req: OrderRequest, price: float, verdict: Verdict) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as fh:
            fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(), "mode": "paper",
                "symbol": req.symbol, "side": req.side, "qty": req.qty, "price": price,
                "reason": req.reason, "strategy": req.strategy, "verdict": verdict.ok,
            }) + "\n")


class RobinhoodBroker:
    """Live order path. Refuses to run unless the policy explicitly unlocks it.

    Kept deliberately inert. Placing a real order requires, in order:
      1. policy.safety.live_orders_enabled = true   (a code change, reviewed)
      2. no kill-switch file present
      3. a RiskManager that has not halted
      4. a nonzero per-session notional budget passed in by the caller
    """

    def __init__(self, policy: Policy, session_budget: float = 0.0) -> None:
        self.policy = policy
        self.risk = RiskManager(policy)
        self.session_budget = session_budget
        self.spent = 0.0
        if not policy.safety.live_orders_enabled:
            raise BrokerError(
                "live order placement is disabled in config/policy.toml; "
                "refusing to construct a live broker"
            )

    def submit(self, req: OrderRequest, price: float, *, equity: float, cash: float,
               position_value: float, gross_exposure: float, held_qty: float,
               trades_today: int, turnover_today: float) -> Verdict:
        self.risk.observe(equity)
        if self.spent + req.notional > self.session_budget:
            return Verdict.deny(f"session budget {self.session_budget:,.2f} exhausted")
        verdict = self.risk.check(
            symbol=req.symbol, side=req.side, notional=req.notional, equity=equity,
            cash=cash, position_value=position_value, gross_exposure=gross_exposure,
            held_qty=held_qty, order_qty=req.qty, trades_today=trades_today,
            turnover_today=turnover_today)
        if not verdict.ok:
            return verdict
        raise BrokerError(
            "live submission is not implemented. Wiring RobinhoodBroker.submit to the "
            "MCP place_equity_order tool is a deliberate manual step, gated on forward "
            "paper evidence. " + describe(verdict)
        )
