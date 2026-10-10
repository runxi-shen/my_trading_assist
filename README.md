# quanttrader — RXRX / TEM

A research-first trading harness for the Robinhood **Agentic** account.
It is built so that the honest answer to "does this make money?" is the easy
thing to obtain, and the dangerous thing (placing a real order) is the hard one.

**Current status: research only. Live orders are disabled in `config/policy.toml`.
No strategy in this repository has passed validation.**

---

## Read this first

You asked for two things that cannot both be true:

| Goal | Constraint |
|---|---|
| Double the money by 2026-12-31 (+100% in ~59 sessions) | Maximum 10% drawdown |

Holding RXRX and TEM at the 80% gross exposure the policy allows produces a
**61.9% annualised volatility** book. Over 59 sessions that is a 29.9% standard
deviation. Doubling requires a **2.3-sigma** move *with zero drift*, and a 10%
drawdown is only a **2.6-sigma daily** move — it is hit almost immediately.

The measured out-of-sample result, tested four ways, is roughly **zero**. The
simulated probability of reaching $4,838 before December 31, inside the 10%
drawdown limit, is **0.25%** — and the brake halts the strategy in **99%** of
simulated paths. This is not a strategy that can be tuned into existence; the
two constraints are arithmetically opposed.

Details: [`BACKTEST_REPORT.md`](BACKTEST_REPORT.md).

---

## What is actually here

```
config/policy.toml     the ONLY place risk limits live. Everything reads from it.
src/quanttrader/
  config.py            policy loading + validation (refuses to start if inconsistent)
  bars.py              what counts as a usable, completed bar
  indicators.py        streaming-safe indicator maths (no forward references)
  strategy.py          pure functions: completed history -> desired weights
  engine.py            fills, slippage, T+1 settlement, drawdown brake
  risk.py              pre-trade gate. The last line of defence.
  execution.py         paper broker + a deliberately inert live broker
  feed.py              archived snapshots and the read-only Robinhood MCP
  walkforward.py       out-of-sample evaluation
  monitor.py           read-only forward observation (dedup, restart-safe)
  research.py          candidate battery, goal test, Monte Carlo
scripts/
  run_backtest.py      regenerates results/study.json + BACKTEST_REPORT.md
  run_monitor.py       hourly / daily observation, places no orders
  fetch_data.py        refreshes snapshots; never overwrites the archive
tests/                 33 tests, all green
legacy/                the original drafted files, kept for reference only
data/                  frozen OHLCV snapshots the published numbers cite
```

Zero third-party dependencies. Python 3.11+ (stdlib `tomllib`).

```bash
nix develop                       # or your own python3.11+
python3 -m unittest discover -s tests
python3 scripts/run_backtest.py   # ~90s
PYTHONPATH=src python3 -m quanttrader.cli status
```

---

## The account

Account identity is **not** in this repository. `config/policy.toml` names an
environment variable (`QUANTTRADER_ACCOUNT`) and the runtime reads the number from
there; `Policy.require_account()` fails closed if it is unset. Account numbers,
balances and position sizes are personal financial data and belong in
`local/account_snapshot.md`, which is gitignored.

Structural facts that matter for strategy design, all verified via the Robinhood
MCP on 2026-10-09:

* The only agent-enabled account is of type **`cash`**, which means **T+1
  settlement**, no margin, no shorting, and **no options**.
* A separate default margin account exists but is **not** agent-enabled, so the
  agent cannot touch it.
* `human_must_approve_trades` was **off**. Turn it on in the Robinhood app before
  anything automated goes near this account.

The T+1 constraint is the one that bites: proceeds from a sale are not spendable
until the next session, which roughly halves the deployable time of any
high-frequency strategy. The engine models this rather than assuming it away.

## What the evidence says

**1. RXRX and TEM are volatile, not cyclical.** Annualised vol 83% and 98%,
average daily range 7–8%. They *look* like they oscillate; they are mostly noise.

**2. Daily mean reversion is real but tiny.** Lag-1 autocorrelation ≈ −0.04.
Variance ratios below 1. Same-day reversal is there, but the signal-to-noise is
far too low to carry a position.

**3. The overnight drift was the one strong signal — and it is dead.**
TEM's close→open return compounded to +541% over the sample, but by third:
`+425% → +23% → −0.8%`. RXRX: `+66% → +80% → −44%`. It also dies at 25 bps/side
(+27% at 10 bps → **−37%** at 25 bps), which is what you pay to trade twice a day.

**4. Nothing survived walk-forward.** Chained out-of-sample return across four
folds, brake enforced:

| Gross exposure | OOS return (≈14 months) | Worst window DD |
|---|---:|---:|
| 10% | −3.33% | −2.7% |
| 20% | −6.63% | −5.3% |
| 40% | −9.43% | −6.5% |
| 80% | +4.91% | −2.4% |

Only two strategies were positive in all three sub-periods: `bollinger_1.0`
(mean reversion; +36% at 10 bps but −18% at 50 bps) and `vol_target`. Everything
else flipped sign.

**5. The 10% drawdown brake is the binding constraint.** At 80% gross it trips
on the first normal pullback — it halted `buy_hold` after five sessions. To
respect a 10% budget on these two names, gross exposure must be **≤ 15–20%**.

---

## Design decisions worth knowing

* **Strategies cannot cheat.** They receive a `View` that only exposes completed
  bars, and they emit desired *weights* — never orders, sizes, or prices. Sizing,
  costs and caps all live in the engine.
* **T+1 is modelled, not assumed away.** Buying debits settled cash; selling
  credits unsettled cash that matures next session. This is why the overnight
  strategy can only trade roughly every other day.
* **Slippage is always adverse.** Buys pay up, sells give up. A regression test
  guards this — an earlier version had sells receiving *positive* slippage,
  which cancelled every cost and made a high-turnover strategy look free.
* **The brake is permanent.** Once tripped, entries stay frozen for the rest of
  the run. `reset_after_review()` exists and is meant to be called by a human.
* **One policy file.** `config/policy.toml` is validated at load; an inconsistent
  policy raises rather than running with surprising limits.

---

## Running it

```bash
# observation only, safe to schedule. Writes results/forward_signals.jsonl
python3 scripts/run_monitor.py --interval hourly --days 90 --quiet
python3 scripts/run_monitor.py --interval daily  --days 400 --quiet

# emergency stop: create this file and every order path refuses
touch KILL
```

Built around hourly observation at 10:05–16:05 America/Indianapolis on weekdays
plus a daily review. Signals are deduplicated by
`(interval, strategy, symbol, bar_ts)`, so restarts do not double-log. The very
first run of each interval backfills the whole lookback window — that is
intentional, it establishes the baseline — and every later run appends only new
bars. Delete `results/forward_signals.jsonl` to start the baseline over.

---

## If you want to trade this

In order, and not skipping steps:

1. **Decide which constraint to relax.** Pick one, explicitly:
   * *Keep the 10% drawdown* → gross exposure ≤ 15–20%, expect low single-digit
     quarterly returns. This is a real, if modest, portfolio.
   * *Keep the ambition* → accept 40–50% drawdowns. That is what a genuine
     triple-digit annual return looks like. It is not "balanced".
2. **Do not liquidate the existing holdings to fund this.** They are −20.6%,
   and the strategies above do not justify realising that loss.
3. **Run the monitor for a full quarter and record what it saw.** The log is
   evidence; the backtest is not.
4. **Fix the safety settings.** Turn `human_must_approve_trades` back **on** in
   the Robinhood app. It is currently off.
5. **Only then** wire `RobinhoodBroker.submit` to `place_equity_order`, behind a
   session notional budget, with `live_orders_enabled` flipped in a reviewed commit.

The `legacy/` directory holds the original drafted files. They are reference
material, not working code, and nothing here imports them.
