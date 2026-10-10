# RXRX / TEM backtest — October 9, 2026

**Decision: neither selected model passed validation. Live orders remain disabled.**

SMMT is protected. The user's goal is to double managed funds excluding SMMT by December 31, 2026; these results do not support that goal.

## What was tested

Three long-only strategy families: (1) buy a 20-bar standardized pullback only above a 100-bar moving average, (2) the same pullback without the trend filter, and (3) break above the prior 20-bar high while above the 100-bar average. Pullback thresholds: 1, 1.5, and 2 standard deviations. Exit pullbacks at the 20-bar mean or a time limit; exit breakouts below the prior 10-bar low or a time limit. All use fixed 2 ATR stops.

Maximum 40% initial capital per ticker, 20% reserve, and approximately 0.5% initial capital risk per entry before gaps/costs. No averaging down, leverage, shorts, or broker execution. A 10% marked-to-market strategy drawdown freezes entries and schedules strategy exits at the next open. A gap can exceed the threshold. Existing non-SMMT stocks were not liquidated or modeled as cash; this is a hypothetical separately funded sleeve.

Initial illustrative capital is $1,052.50; percentage results scale before fill/liquidity effects. This is NOT the current live account cash balance or a live funding commitment.

## Historical evaluation: June 1–October 8, 2026

Selection thresholds use pre-June training return minus maximum drawdown, requiring positive training return, at least eight closed trades, and no drawdown halt. Training selects daily range/1.5 and hourly range/2.0. Neither passes subsequent evaluation. The range family was added after inspecting the initial families' evaluation results: treat this as exploratory research, not a pristine untouched holdout. Forward monitoring is necessary.

| Frequency | Strategy | Threshold | Return after 10 bps/side | Maximum drawdown | Closed trades |
|---|---|---:|---:|---:|---:|
| daily | pullback | 1.0 | +0.000% | 0.000% | 0 |
| daily | pullback | 1.5 | +0.000% | 0.000% | 0 |
| daily | pullback | 2.0 | +0.000% | 0.000% | 0 |
| daily | range | 1.0 | +0.546% | -0.543% | 4 |
| daily | range | 1.5 | +0.004% | -0.543% | 2 |
| daily | range | 2.0 | -0.056% | -0.293% | 1 |
| daily | breakout | 1.5 | -1.675% | -1.884% | 6 |
| hourly | pullback | 1.0 | +3.166% | -2.227% | 26 |
| hourly | pullback | 1.5 | +1.731% | -1.958% | 16 |
| hourly | pullback | 2.0 | -1.183% | -2.852% | 9 |
| hourly | range | 1.0 | +6.697% | -3.943% | 55 |
| hourly | range | 1.5 | +3.918% | -4.200% | 40 |
| hourly | range | 2.0 | -1.978% | -5.289% | 24 |
| hourly | breakout | 1.5 | +4.602% | -3.605% | 17 |

### Execution-cost sensitivity for training-selected candidates

| Frequency | 10 bps each side | 25 bps each side | 50 bps each side |
|---|---:|---:|---:|
| daily | +0.004% | -0.013% | -0.041% |
| hourly | -1.978% | -2.582% | -3.762% |

### Comparisons

Daily buy-and-hold with 40% RXRX, 40% TEM, 20% cash returned +20.530% but suffered -23.426% maximum drawdown. It has no drawdown brake and is shown to illustrate the return/risk tradeoff, not as a qualifying strategy. An 80% SPY / 20% cash price-only comparison returned +1.803% with -3.599% drawdown. SPY dividends and cash interest are omitted.

## Data and verification

Robinhood MCP regular-session split-adjusted OHLCV snapshots are saved in data/. Synthetic/interpolated and zero-volume bars were excluded, including pre-IPO TEM placeholders. Both frequencies end October 8 for comparable completed-session evaluation. Daily raw TEM data begin June 14, 2024; real hourly data begin December 22, 2025. Indicators need 100-bar warmup. Hourly series may omit partial opening/closing intervals; this is not a complete intraday execution audit.

Signals use completed bars and execute at the next available bar open with adverse slippage. Stops use the worse of open and stop before slippage. Same-bar entry stops are handled conservatively. Cash sale proceeds cannot be reused before the next observed trading date (T+1). Terminal positions are valued at the last close less exit costs; that is not a claimed live close fill.

Costs are estimates, not measured bid/ask reconstruction. No taxes, dividends, cash interest, impact, failed fills, order latency, or historical event blackout calendar. Chosen tickers introduce selection bias. Training/evaluation dates are retrospective and the sample is short. Positive returns from a variant chosen after inspecting this period would not establish a live edge.

Four engine checks verify future-bar isolation, cash settlement, gap-stop execution/drawdown halting, and the SMMT policy. JSON results include raw-data SHA-256 hashes; CSV files contain selected-candidate equity curves and trades.

Run `nix develop --command python3 backtest.py` and `nix develop --command python3 -m unittest -v test_backtest.py`.

## Next step

Hourly observation during regular market hours plus a daily review. Track the frozen daily and hourly range candidates without retuning from each new result. Keep live orders disabled. Observe catalysts and data quality; log new completed-bar signals once. Do not equate monitoring with a paper portfolio or filled orders. Before any deployment, require a defined funding baseline excluding SMMT, a separately verified paper executor, more independent evidence, reconciliation/idempotency, tradability checks, and broker approval handling.

RXRX has a November 2, 2026 clinical presentation, so recurring monitoring must check scheduled clinical and earnings catalysts. [Recursion's Q2 release](https://ir.recursion.com/news-releases/news-release-details/recursion-reports-second-quarter-financial-results-genentech). Robinhood can require approval on individual trades even with approvals off. [Robinhood agent trading documentation](https://robinhood.com/us/en/support/articles/trading-with-your-agent/). More primary sources are in research_notes.md.
