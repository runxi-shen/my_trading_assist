# RXRX / TEM trading study

Sample 2024-06-14 to 2026-10-08 (581 aligned sessions). Capital modelled: $1,000.00. Universe: RXRX, TEM. Protected: SMMT.

## 0. Source data

Raw broker bars are **not** redistributed in this repository (broker market-data
terms). Re-fetch with `python3 scripts/fetch_data.py` and verify the digest:

| Snapshot | Bytes | SHA-256 |
|---|---:|---|
| data/daily.json | 484,279 | `3605fcf4eef94dc2ac99ee3475d89b8a...` |
| data/hourly.json | 3,057,805 | `3897f77268b6fb28c30e927d6062ea10...` |

## 1. Data quality

| Symbol | Bars | Sessions | First | Last | Median $ volume |
|---|---:|---:|---|---|---:|
| RXRX | 590 | 590 | 2024-06-03 | 2026-10-08 | 77,143,817 |
| TEM | 581 | 581 | 2024-06-14 | 2026-10-08 | 326,660,267 |

Daily volatility: RXRX 5.26%, TEM 6.14%, correlation 0.456.

## 2. Is there a signal? (in-sample, no drawdown brake, 10 bps/side)

Diagnostic only. No brake means these are not deployable; they show whether a
strategy has any raw signal before the risk limit is applied.

| Strategy | Return | Max DD | Sharpe | Trades | Turnover (x equity) |
|---|---:|---:|---:|---:|---:|
| dip_buy_1.5 | +13.25% | -2.03% | 1.01 | 7 | 5.3 |
| bollinger_1.0 | +36.44% | -21.61% | 0.71 | 162 | 170.2 |
| overnight_hold | +27.09% | -29.54% | 0.60 | 581 | 637.0 |
| vol_target | +14.87% | -12.87% | 0.59 | 45 | 2.9 |
| rsi_revert | +4.63% | -4.41% | 0.59 | 14 | 11.9 |
| buy_hold | +20.39% | -50.20% | 0.43 | 85 | 7.2 |
| dip_buy_1.0 | +3.05% | -7.77% | 0.20 | 25 | 19.6 |
| bollinger_2.0 | -2.65% | -12.26% | -0.06 | 28 | 20.0 |
| bollinger_1.5 | -5.43% | -17.96% | -0.11 | 74 | 58.3 |
| trend_follow | -34.55% | -37.34% | -1.21 | 35 | 20.3 |

## 3. Can a 10% drawdown budget survive this much exposure?

| Gross exposure | Daily vol | Annualised vol |
|---|---:|---:|
| 10% | 0.49% | 7.7% |
| 20% | 0.97% | 15.5% |
| 30% | 1.46% | 23.2% |
| 40% | 1.95% | 30.9% |
| 60% | 2.92% | 46.4% |
| 80% | 3.90% | 61.9% |

## 4. Walk-forward, out of sample only (brake enforced)

Parameters are chosen on each training window and frozen, then applied to the
window that follows. Chained out-of-sample return is the headline.

| Gross exposure | OOS return | Worst window DD | Folds with a qualifier | Selections |
|---|---:|---:|---|---|
| 10% | -3.33% | -3.38% | 4/4 | dip_buy_1.5, overnight_hold |
| 20% | -6.63% | -6.38% | 4/4 | dip_buy_1.5, overnight_hold |
| 30% | -3.43% | -5.42% | 4/4 | bollinger_1.0, dip_buy_1.5 |
| 40% | -9.43% | -7.17% | 4/4 | bollinger_1.0, dip_buy_1.5 |
| 60% | +3.69% | -1.90% | 4/4 | dip_buy_1.5, rsi_revert |
| 80% | +4.91% | -2.54% | 4/4 | dip_buy_1.5, rsi_revert |

### Every candidate, every out-of-sample window (80% gross)

| Window | buy_hold | overnight_hold | vol_target | bollinger_1.0 | bollinger_1.5 | bollinger_2.0 | dip_buy_1.0 | dip_buy_1.5 | trend_follow | rsi_revert |
|---|---|---|---|---|---|---|---|---|---|---|
| 2024-06-14..2025-08-13|2025-08-13..2025-11-24 | +0.00% | +0.00% | -0.01% | +0.00% | +0.00% | +0.00% | +3.02% | +3.35% | +0.00% | +1.32% |
| 2024-06-14..2025-11-24|2025-11-24..2026-03-11 | +0.00% | +0.00% | -5.21% | +0.00% | +0.00% | +0.00% | -2.52% | +0.00% | +0.00% | -1.14% |
| 2024-06-14..2026-03-11|2026-03-11..2026-06-24 | +0.00% | +0.00% | -2.00% | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2024-06-14..2026-06-24|2026-06-24..2026-10-06 | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% | +4.64% | +4.73% | +0.00% | -0.78% |

## 5. Sub-period stability

A real edge shows up in every regime. A backtest artefact shows up in one.

| Strategy | 2024-06-14..2025-03-25 | 2025-03-25..2025-12-31 | 2025-12-31..2026-10-08 | Positive in all | Sign flips |
|---|---:|---:|---:|---|---:|
| bollinger_1.0 | +13.98% | +7.34% | +4.92% | yes | 0 |
| vol_target | +9.68% | +0.59% | +3.98% | yes | 0 |
| dip_buy_1.0 | +0.00% | +1.67% | +1.63% | no | 1 |
| dip_buy_1.5 | +0.00% | +8.99% | +4.80% | no | 1 |
| rsi_revert | +0.00% | +5.45% | +0.84% | no | 1 |
| bollinger_1.5 | +8.12% | +16.35% | -5.38% | no | 1 |
| buy_hold | +21.32% | -7.97% | +13.66% | no | 2 |
| bollinger_2.0 | +1.80% | +22.96% | -12.45% | no | 1 |
| trend_follow | -17.67% | -18.12% | -17.03% | no | 0 |
| overnight_hold | +57.42% | +8.08% | -28.17% | no | 1 |

## 6. Execution-cost sensitivity

**dip_buy_1.5**

| bps/side | Return | Max DD | Sharpe | Trades |
|---|---:|---:|---:|---:|
| 0 | +13.81% | -2.03% | 1.05 | 7 |
| 5 | +13.53% | -2.03% | 1.03 | 7 |
| 10 | +13.25% | -2.03% | 1.01 | 7 |
| 25 | +12.41% | -2.03% | 0.96 | 7 |
| 50 | +11.03% | -2.02% | 0.86 | 7 |

**bollinger_1.0**

| bps/side | Return | Max DD | Sharpe | Trades |
|---|---:|---:|---:|---:|
| 0 | +55.19% | -19.24% | 0.96 | 162 |
| 5 | +45.51% | -20.43% | 0.83 | 162 |
| 10 | +36.44% | -21.61% | 0.71 | 162 |
| 25 | +12.50% | -26.27% | 0.34 | 162 |
| 50 | -18.41% | -37.91% | -0.29 | 162 |

**overnight_hold**

| bps/side | Return | Max DD | Sharpe | Trades |
|---|---:|---:|---:|---:|
| 0 | +102.19% | -15.04% | 1.54 | 581 |
| 5 | +60.30% | -21.06% | 1.07 | 581 |
| 10 | +27.09% | -29.54% | 0.60 | 581 |
| 25 | -36.60% | -54.86% | -0.83 | 581 |
| 50 | -80.08% | -81.21% | -3.21 | 582 |

## 7. The doubling goal, tested

### 80% gross

Needs 100.0% in 59 sessions (2.32 sigma at 29.94% horizon vol). With zero drift, P(success) = 1.0302%.

| Assumed Sharpe | Expected return | P(reach target) | P(brake halts) | Median end | 5th pct | 95th pct |
|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 0.0% | 0.245% | 99.2% | $948 | $866 | $1,315 |
| 0.50 | 7.2% | 0.410% | 98.9% | $959 | $868 | $1,375 |
| 1.00 | 14.5% | 0.785% | 98.3% | $973 | $869 | $1,473 |
| 1.53 | 22.2% | 1.515% | 97.4% | $993 | $872 | $1,598 |

### 20% gross

Needs 100.0% in 59 sessions (9.26 sigma at 7.48% horizon vol). With zero drift, P(success) = 0.0000%.

| Assumed Sharpe | Expected return | P(reach target) | P(brake halts) | Median end | 5th pct | 95th pct |
|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 0.0% | 0.000% | 24.8% | $996 | $898 | $1,129 |
| 0.50 | 1.8% | 0.000% | 18.9% | $1,015 | $900 | $1,148 |
| 1.00 | 3.6% | 0.000% | 14.0% | $1,033 | $906 | $1,167 |
| 1.53 | 5.5% | 0.000% | 9.6% | $1,053 | $918 | $1,191 |

