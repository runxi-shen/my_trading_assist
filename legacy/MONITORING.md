# RXRX/TEM observation workflow

This is signal observation only. No live orders, account reallocation, order cancellation, account-setting changes, or broker paper orders. SMMT must never be touched. Current strategy candidates failed preliminary validation.

## Schedule

Hourly weekdays at 10:05, 11:05, 12:05, 13:05, 14:05, 15:05, 16:05 America/Indianapolis. Daily review weekdays at 16:15. Check US exchange holidays and early closes. Skip intraday observation when the regular session is closed, except the 16:05 completed-session observation. Do not pretend the scheduler is a continuously running execution daemon.

## On each hourly observation

1. Read strategy_policy.json and results/summary.json. Remain in research-only mode. Use available Robinhood MCP historical, quote, market-hours and news tools read-only. If tools are unavailable or authentication fails, notify about the blocked monitor once; do not fabricate data.
2. Fetch RXRX/TEM regular-session split-adjusted hourly bars from the last 90 calendar days. Fetch daily bars from the last year for the daily candidate as needed. Save responses as data/forward_hourly.json and data/forward_daily.json, with the same results/bars structure as the archived snapshots. Do not overwrite data/daily.json or data/hourly.json, the original research evidence.
3. Exclude interpolated/zero-volume rows and incomplete bars. Hourly rows are UTC left-edge timestamps and must have ended before decision time. Daily rows are date-labelled and are complete only after that trading date's 16:00 ET close (adjust for early close). Verify actual market calendar and timezone; do not treat today's incomplete daily OHLC as a completed signal.
4. Use backtest.indicators and backtest.signal on fresh completed bars. Freeze daily range threshold 1.5 and hourly range threshold 2.0. These are observation candidates, not approved live strategies. Record entry-condition true/false and an exit-condition observation only if relevant; do not call it a trade, a fill, or portfolio P&L.
5. Append new observations to results/forward_signals.jsonl. Deduplicate by frequency + ticker + bar timestamp, persist results/monitor_state.json, and do not repeat observations after a restart. Check quote timestamps and any gaps; skip stale or insufficient data.
6. Check company news and announced upcoming earnings or clinical dates. Include RXRX's November 2 clinical presentation until verified otherwise. Flag meaningful catalyst changes. Future order rules would need a verified catalyst blackout; the historical test did not validate one.
7. Notify only when an entry condition changes, a material catalyst appears, data/authentication fails, or user action is needed. Stay quiet for unchanged/non-actionable conditions. Never loosen risk rules to pursue the doubling goal.

## Daily review

At 16:15, review newly completed daily signals, fresh catalysts and logged observations. Compare with frozen research candidates and summarize material findings, including failures. Do not repeatedly optimize thresholds, erase unfavorable observations, or relabel an observed price movement as an attainable fill. Do not report simulated portfolio performance without a separately implemented and verified forward paper engine. Keep deployment disabled even if a few signals look promising; report evidence for the user to assess.

If reconciling account value, discover the Agentic account from get_accounts, exclude SMMT market value from managed capital, and distinguish external cash flows from investment return. The funding baseline is unset until deployment. Other holdings are eligible for eventual reallocation but have not been sold by this workflow. TGHI is unlisted and needs explicit tradability verification.
