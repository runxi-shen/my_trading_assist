# RXRX/TEM trading research

Reviewed October 9, 2026. Research only; no account reads, orders, or scheduling performed.

## Primary-source facts

- **RXRX is catalyst driven.** Recursion's August 5, 2026 release announces additional REC-4881 Phase 2 safety/efficacy results at the CGA-IGC meeting on **November 2, 2026, 13:30–15:00 MST**. It also expects an FDA registrational-path update in 2H26 and a REC-7735 Phase 1/2 trial start in 2H26. Quarter-end cash was $556.8 million, quarterly net loss $131.0 million, and expected runway early 2028 without additional financing. These are company expectations, not guaranteed outcomes. [Recursion Q2 release](https://ir.recursion.com/news-releases/news-release-details/recursion-reports-second-quarter-financial-results-genentech).
- **RXRX history begins in April 2021.** Nasdaq records the IPO bell event April 16, 2021. A backtest must not fabricate earlier observations. [Nasdaq IPO event](https://www.nasdaq.com/events/recursion-pharmaceuticals-inc.-rings-the-opening-bell-in-celebration-of-its-ipo).
- **TEM history begins June 14, 2024.** Its filing states Nasdaq trading began that day and its IPO completed June 17 at $37/share. This limits independent regimes available to test. [Tempus 2024 Form 10-Q](https://investors.tempus.com/static-files/7aea7b14-291d-4962-a95f-bd372a056019).
- **TEM also has fundamental jumps.** Its July 30, 2026 Q2 release reports FDA approval of xT Tumor Only, approximately $200 million of new data/application licenses, and completion of a $460 million convertible-note offering. Treat earnings, regulatory, licensing, acquisition, and financing announcements as potential structural price changes. [Tempus Q2 release filed with SEC](https://www.sec.gov/Archives/edgar/data/1717115/000119312526326083/tem-ex99_1.htm).
- **Agent account access is bounded.** An external Robinhood MCP agent can read account/position information but place trades only in its Agentic account. [External onboarding](https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/).
- **Autonomous execution is broker-supported but conditional.** Robinhood says trade approvals are off by default for external MCP accounts, can be toggled in app, and some trades can require approval even when disabled. With approvals enabled, the customer reviews and manually places proposed orders in app. Never infer this particular account's current setting from the default. [Trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/).
- **Cash and limited margin differ.** Robinhood says Agentic limited margin allows trading unsettled sale proceeds but adds no borrowing or leverage. Cash accounts remain an available alternative. [Agent account types](https://robinhood.com/us/en/support/articles/setting-up-an-agent/). Standard stock/option settlement is the next business day, T+1. [Robinhood settlement explanation](https://robinhood.com/gb/en/support/articles/T1-settlements/).

## Relevant current MCP metadata (inspected; not called)

- `get_accounts`: account `type` is cash, margin, or limited_margin. Cash unsettled_funds is informational and may lag; `get_portfolio.buying_power` is authoritative for current order capacity.
- `get_trade_approval_setting`: read before a workflow depends on approvals. Only the customer changes the setting in app. Approval-off does not guarantee every order bypasses approval.
- `place_equity_order`: real money; default review first. Client order UUID must remain the same on a transport retry. Whole-share limit orders provide price bounds; fractional/dollar-based equity orders require market orders during regular hours. Stop and market orders execute regular hours only; extended and overnight execution is limit-only.
- `decline_trade_approval`: agent can decline a pending proposal; only customer can approve it.

## Proposed operational constraints (inferences/design choices)

1. Enforce a symbol allowlist of RXRX and TEM. SMMT is a hard protected holding: block all SMMT sells, rebalancing, collateral strategies, and liquidation instructions originating in this project. Use separate strategy capital rather than whole-account equity to size trades.
2. Start with daily signals and hourly **monitoring**, not hourly churn. Test intraday signals only on actual historical hourly OHLCV; daily bars cannot validate hourly execution.
3. Distinguish mean reversion from a lasting repricing: use trend/regime filters, a maximum holding period, capped exposure, and no averaging down. Evaluate these against buy-and-hold and cash rather than assuming cyclical patterns are stable.
4. Exclude new entries around known earnings/clinical announcements. Build blackout timestamps from information available at the historical decision time. Using today's complete historical event calendar during backtesting introduces look-ahead bias. If the event calendar is unavailable, identify the price-only test as incomplete and keep catalyst checks mandatory in forward paper monitoring.
5. Include next-bar execution, bid/ask/slippage stresses, gap losses, unfilled limit orders, trading calendars, and cash T+1 if applicable. Count capital committed to pending orders and approvals so repeat runs cannot double-spend.
6. A maximum drawdown threshold can halt new strategy entries and reduce RXRX/TEM exposure; it must never sell SMMT to restore that threshold. Because SMMT remains held, strategy controls cannot guarantee a limit on total-account drawdown.
7. Separate signal calculation from execution; reconcile open orders/fills before submitting another proposal. Persist the last processed bar, intended order, and UUID to prevent duplicate orders after restarts. Missing/stale quotes, broker errors, or failed reconciliation should skip new entries and notify the user.
8. Backtest success is evidence for a paper trial, not proof of future profit. Demand untouched out-of-sample performance and parameter stability; if the edge fails costs or holdout periods, do not deploy that model.
