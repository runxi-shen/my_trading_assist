"""Pre-trade risk gate.

Every order path — live, paper, or replayed — must pass through `RiskManager`.
This is the last line of defence and the only component allowed to say no to a
strategy. It is deliberately dumb: no forecasting, no exceptions, no overrides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Policy


@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def allow(cls) -> "Verdict":
        return cls(True)

    @classmethod
    def deny(cls, *reasons: str) -> "Verdict":
        return cls(False, tuple(reasons))

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class RiskManager:
    policy: Policy
    root: Path = field(default_factory=Path.cwd)
    peak_equity: float | None = None
    halted: bool = False
    halt_reason: str | None = None

    # -- state ------------------------------------------------------------- #

    def observe(self, equity: float) -> None:
        """Mark the book. Must be called on every valuation, before any order."""
        self.peak_equity = equity if self.peak_equity is None else max(self.peak_equity, equity)
        if not self.halted and self.drawdown(equity) <= -self.policy.risk.max_drawdown:
            self.halted = True
            self.halt_reason = (
                f"drawdown {self.drawdown(equity):.2%} breached the "
                f"{self.policy.risk.max_drawdown:.0%} limit at equity {equity:,.2f}"
            )

    def drawdown(self, equity: float) -> float:
        if not self.peak_equity:
            return 0.0
        return equity / self.peak_equity - 1

    @property
    def kill_switch_tripped(self) -> bool:
        return (self.root / self.policy.safety.kill_switch_file).exists()

    def reset_after_review(self) -> None:
        """Only a human, after reviewing, should ever call this."""
        self.halted = False
        self.halt_reason = None

    # -- the gate ---------------------------------------------------------- #

    def check(self, *, symbol: str, side: str, notional: float, equity: float,
              cash: float, position_value: float, gross_exposure: float,
              held_qty: float, order_qty: float, trades_today: int,
              turnover_today: float) -> Verdict:
        r, ex = self.policy.risk, self.policy.execution
        bad: list[str] = []

        if self.kill_switch_tripped:
            bad.append(f"kill switch file present ({self.policy.safety.kill_switch_file})")
        if self.halted:
            bad.append(f"strategy halted: {self.halt_reason}")
        if symbol in self.policy.protected_symbols:
            bad.append(f"{symbol} is protected and may never be traded")
        elif not self.policy.tradable(symbol):
            bad.append(f"{symbol} is outside the permitted universe")
        if side not in ("buy", "sell"):
            bad.append(f"unknown side {side!r}")
        if r.long_only and side == "sell" and order_qty > held_qty:
            bad.append("long-only: cannot sell more than is held (no shorting)")
        if not ex.allow_fractional_shares and order_qty != int(order_qty):
            bad.append("fractional shares are disabled")
        if notional < ex.min_order_notional:
            bad.append(f"notional {notional:.2f} below the {ex.min_order_notional:.2f} minimum")
        if trades_today >= r.max_trades_per_day:
            bad.append(f"daily trade budget exhausted ({r.max_trades_per_day})")
        if turnover_today + notional > r.max_daily_turnover * equity:
            bad.append(f"daily turnover cap would be breached ({r.max_daily_turnover:.2f}x equity)")

        if side == "buy":
            reserve = r.cash_reserve_fraction * equity
            if notional > cash - reserve:
                bad.append(
                    f"insufficient settled cash: needs {notional:,.2f}, "
                    f"spendable {max(0.0, cash - reserve):,.2f} after the "
                    f"{r.cash_reserve_fraction:.0%} reserve"
                )
            if position_value + notional > r.max_weight_per_symbol * equity + 1e-9:
                bad.append(
                    f"{symbol} would exceed the {r.max_weight_per_symbol:.0%} per-name cap"
                )
            if gross_exposure + notional > r.max_gross_exposure * equity + 1e-9:
                bad.append(f"gross exposure would exceed {r.max_gross_exposure:.0%}")

        return Verdict.allow() if not bad else Verdict.deny(*bad)


def describe(verdict: Verdict) -> str:
    return "allow" if verdict.ok else "deny: " + "; ".join(verdict.reasons)
