# legacy/

Superseded. **Nothing here is imported by anything else in the repository, and
nothing here should be run.**

This is the original drafted work, kept only so the negative result is traceable.
It reached the same conclusion the current study did, by a rougher route:

> "Decision: neither selected model passed validation. Live orders remain disabled."

What it got right: an event-driven loop, next-open fills, T+1 settlement, a
drawdown brake, a protected-symbol check, and honest reporting of failure.

What it got wrong, and why it was replaced:

| Problem | Consequence |
|---|---|
| Slippage applied in one direction | Sells received *favourable* fills, cancelling every cost, so high-turnover strategies looked free |
| Indicators computed on full history | No structural guard against reading the current bar |
| Strategy state mutated during the loop | Hard to test, easy to introduce lookahead |
| Risk limits scattered through the file | A single constant (`STOP_DRAWDOWN`) instead of one validated policy |
| `strategy_policy.json` doubled as config and prose | Limits could drift from the code |
| No test for settlement or cost direction | The bugs above survived |

The current implementation lives in `src/quanttrader/` and is covered by
`tests/`. `scripts/run_backtest.py` regenerates `BACKTEST_REPORT.md` from scratch.

Keep this directory for the record. Do not build on it.
