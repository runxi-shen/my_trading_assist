"""quanttrader — a research-first, guard-railed trading harness.

Layout:

    config/policy.toml   the only place risk limits are defined
    bars                 what counts as a usable, completed bar
    indicators           streaming-safe indicator maths
    strategy             pure functions: completed history -> desired weights
    engine               fills, costs, T+1 settlement, drawdown brake
    feed                 archived snapshots and the read-only live MCP
    walkforward          honest out-of-sample evaluation
"""

from .config import Policy, PolicyError

__all__ = ["Policy", "PolicyError"]
__version__ = "0.1.0"
