"""Load and validate the risk policy.

A `Policy` is the only thing that can widen risk, and it can only be widened by
editing config/policy.toml. Code that trades takes a Policy, never raw numbers.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = ROOT / "config" / "policy.toml"


class PolicyError(ValueError):
    """The policy is internally inconsistent. Refuse to run."""


@dataclass(frozen=True, slots=True)
class Account:
    """Account identity.

    `account_number` is resolved from the environment, never committed. Committing
    it to a public repository would publish an account identifier, so the policy
    file only names the variable to read.
    """

    account_number_env: str
    type: str
    broker: str

    @property
    def account_number(self) -> str:
        return os.environ.get(self.account_number_env, "")

    @property
    def identified(self) -> bool:
        return bool(self.account_number)


@dataclass(frozen=True, slots=True)
class Risk:
    long_only: bool
    leverage: bool
    options: bool
    shorting: bool
    max_weight_per_symbol: float
    cash_reserve_fraction: float
    max_gross_exposure: float
    max_drawdown: float
    max_daily_turnover: float
    max_trades_per_day: int
    rebalance_tolerance: float


@dataclass(frozen=True, slots=True)
class Execution:
    slippage_bps_each_side: float
    stress_slippage_bps: tuple[float, ...]
    settlement_days: int
    allow_fractional_shares: bool
    min_order_notional: float


@dataclass(frozen=True, slots=True)
class Safety:
    live_orders_enabled: bool
    require_human_approval: bool
    kill_switch_file: str
    paper_before_live_sessions: int


@dataclass(frozen=True, slots=True)
class Policy:
    account: Account
    protected_symbols: frozenset[str]
    permitted_symbols: frozenset[str]
    rotation_candidates: frozenset[str]
    quarantined_symbols: frozenset[str]
    risk: Risk
    execution: Execution
    safety: Safety
    fees: dict[str, float] = field(default_factory=dict)

    def validate(self) -> None:
        r = self.risk
        problems: list[str] = []
        if not r.long_only:
            problems.append("long_only must stay true")
        if r.leverage:
            problems.append("leverage must stay false")
        if r.shorting:
            problems.append("shorting must stay false")
        if self.protected_symbols & self.permitted_symbols:
            problems.append(
                f"protected symbols may never be tradable: {sorted(self.protected_symbols & self.permitted_symbols)}"
            )
        if self.protected_symbols & self.rotation_candidates:
            problems.append("protected symbols may never be rotation candidates")
        if r.max_weight_per_symbol <= 0 or r.max_weight_per_symbol > 1:
            problems.append("max_weight_per_symbol must be in (0, 1]")
        if r.cash_reserve_fraction < 0:
            problems.append("cash_reserve_fraction must be >= 0")
        if r.max_gross_exposure > 1:
            problems.append("max_gross_exposure must be <= 1 (no leverage)")
        if r.max_drawdown <= 0 or r.max_drawdown >= 1:
            problems.append("max_drawdown must be in (0, 1)")
        if r.max_trades_per_day < 1:
            problems.append("max_trades_per_day must be >= 1")
        if not 0 <= r.rebalance_tolerance < 1:
            problems.append("rebalance_tolerance must be in [0, 1)")
        if self.account.type != "cash":
            problems.append("only a cash account is modelled (T+1 settlement, no margin)")
        if problems:
            raise PolicyError("; ".join(problems))

    def require_account(self) -> str:
        """For paths that actually touch the brokerage. Fails closed."""
        if not self.account.identified:
            raise PolicyError(
                f"account number is not set; export {self.account.account_number_env} "
                "before any account-touching command (it is deliberately not in git)"
            )
        return self.account.account_number

    def tradable(self, symbol: str) -> bool:
        return symbol in self.permitted_symbols and symbol not in self.protected_symbols

    def assert_tradable(self, symbol: str) -> None:
        if symbol in self.protected_symbols:
            raise PolicyError(f"{symbol} is protected and may never be traded")
        if not self.tradable(symbol):
            raise PolicyError(f"{symbol} is not in the permitted universe")

    @property
    def all_known_symbols(self) -> frozenset[str]:
        return (
            self.permitted_symbols
            | self.protected_symbols
            | self.rotation_candidates
            | self.quarantined_symbols
        )

    @classmethod
    def load(cls, path: Path | str = DEFAULT_POLICY_PATH) -> "Policy":
        raw = tomllib.loads(Path(path).read_text())
        policy = cls(
            account=Account(**raw["account"]),
            protected_symbols=frozenset(raw["protection"]["protected_symbols"]),
            permitted_symbols=frozenset(raw["universe"]["permitted_symbols"]),
            rotation_candidates=frozenset(raw["universe"]["rotation_candidates"]),
            quarantined_symbols=frozenset(raw["universe"]["quarantined_symbols"]),
            risk=Risk(**raw["risk"]),
            execution=Execution(
                slippage_bps_each_side=raw["execution"]["slippage_bps_each_side"],
                stress_slippage_bps=tuple(raw["execution"]["stress_slippage_bps"]),
                settlement_days=raw["execution"]["settlement_days"],
                allow_fractional_shares=raw["execution"]["allow_fractional_shares"],
                min_order_notional=raw["execution"]["min_order_notional"],
            ),
            safety=Safety(**raw["safety"]),
            fees=dict(raw.get("fees", {})),
        )
        policy.validate()
        return policy
