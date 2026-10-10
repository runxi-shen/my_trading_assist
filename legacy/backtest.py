"""Deterministic, offline RXRX/TEM research. No broker/order code.

Signals use completed bars; fills use next opens. Cash sale proceeds settle
on the next observed trading date. Stops fill at the worse of open and stop,
then adverse slippage. Daily/hourly histories are tested separately.
"""
import csv
import hashlib
import json
import math
from datetime import date
from pathlib import Path
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parent
CAPITAL = 1052.50  # normalized illustrative capital; performance scales
SYMBOLS = ("RXRX", "TEM")
CUTOFF = "2026-10-09"  # exclusive; matched completed daily/hourly sessions
HOLDOUT = "2026-06-01"
STOP_DRAWDOWN = 0.10


def validate_policy():
    policy = json.loads((ROOT / "strategy_policy.json").read_text())
    assert "SMMT" in policy["protected_symbols"]
    assert not (set(policy["protected_symbols"]) & set(policy["permitted_symbols"]))
    assert set(SYMBOLS) <= set(policy["permitted_symbols"])
    assert not policy["allow_live_orders"] and policy["long_only"] and not policy["leverage"]
    assert policy["maximum_strategy_drawdown"] == STOP_DRAWDOWN


def load(interval):
    raw = json.loads((ROOT / "data" / f"{interval}.json").read_text())
    series, audit = {}, {}
    for result in raw["results"]:
        bars = []
        for row in result["bars"]:
            if row.get("interpolated") or row["volume"] <= 0 or row["begins_at"][:10] >= CUTOFF:
                continue
            bar = {k: float(row[f"{k}_price"]) for k in ("open", "high", "low", "close")}
            assert 0 < bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]
            bar.update(time=row["begins_at"], day=row["begins_at"][:10], volume=row["volume"])
            bars.append(bar)
        assert bars and len({b["time"] for b in bars}) == len(bars)
        assert bars == sorted(bars, key=lambda b: b["time"])
        series[result["symbol"]] = bars
        audit[result["symbol"]] = {"received": len(result["bars"]), "usable": len(bars),
                                  "first": bars[0]["time"], "last": bars[-1]["time"]}
    return series, audit


def indicators(bars):
    closes = [b["close"] for b in bars]
    trs = []
    out = []
    for i, b in enumerate(bars):
        prev = closes[max(0, i-1)]
        trs.append(max(b["high"]-b["low"], abs(b["high"]-prev), abs(b["low"]-prev)))
        if i < 100:
            out.append(None)
            continue
        mean = fmean(closes[i-19:i+1])
        sd = stdev(closes[i-19:i+1])
        out.append({"mean": mean, "z": (b["close"]-mean)/sd if sd else 0,
                    "trend": fmean(closes[i-99:i+1]), "atr": fmean(trs[i-13:i+1]),
                    "breakout": max(x["high"] for x in bars[i-20:i]),
                    "exit_low": min(x["low"] for x in bars[i-10:i])})
    return out


def signal(kind, threshold, b, ind, holding):
    if ind is None:
        return False
    if holding:
        return b["close"] >= ind["mean"] if kind in ("pullback", "range") else b["close"] < ind["exit_low"]
    if kind != "range" and b["close"] <= ind["trend"]:
        return False
    return ind["z"] < -threshold if kind in ("pullback", "range") else b["close"] > ind["breakout"]


def metrics(curve, trades, halted):
    values = [CAPITAL] + [x["value"] for x in curve]
    peak, dd = CAPITAL, 0
    for value in values:
        peak = max(peak, value)
        dd = min(dd, value/peak-1)
    daily = {}
    for row in curve:
        daily[row["time"][:10]] = row["value"]
    ds = list(daily.values())
    rets = [b/a-1 for a, b in zip([CAPITAL]+ds[:-1], ds)]
    sd = stdev(rets) if len(rets)>1 else 0
    sharpe = fmean(rets)/sd*math.sqrt(252) if sd else 0
    duration = (date.fromisoformat(curve[-1]["time"][:10])-date.fromisoformat(curve[0]["time"][:10])).days
    pnl = [t["pnl"] for t in trades]
    return {"return_pct": round((values[-1]/CAPITAL-1)*100, 3),
            "max_drawdown_pct": round(dd*100, 3), "sharpe_zero_cash_yield": round(sharpe, 3),
            "ending_value": round(values[-1], 2), "closed_trades": len(trades),
            "win_rate_pct": round(sum(p>0 for p in pnl)/len(pnl)*100, 2) if pnl else None,
            "halted": halted, "days": duration,
            "start": curve[0]["time"], "end": curve[-1]["time"]}


def simulate(series, inds, interval, kind, start, end, threshold=1.5, bps=10, brake=True):
    # Fixed independent 40% sleeves plus untouched 20% cash reserve.
    maps = {s: {b["time"]: i for i,b in enumerate(series[s])} for s in SYMBOLS}
    times = sorted(set(maps["RXRX"]) & set(maps["TEM"]))
    times = [t for t in times if start <= t[:10] < end]
    assert times
    days = sorted({t[:10] for t in times})
    next_day = {d: days[i+1] if i+1<len(days) else "9999-12-31" for i,d in enumerate(days)}
    states = {s: {"cash": .4*CAPITAL, "pending": [], "qty": 0, "entry": 0,
                  "stop": 0, "age": 0, "force_exit": False} for s in SYMBOLS}
    slip = bps/10000
    curve, trades = [], []
    peak, halted = CAPITAL, False
    max_age = (10 if interval=="daily" else 30) if kind in ("pullback", "range") else (30 if interval=="daily" else 60)
    def sell(s, b, price, reason):
        st = states[s]
        proceeds = st["qty"]*price*(1-slip)
        st["pending"].append((next_day[b["day"]], proceeds))
        trades.append({"symbol": s, "entry_time": st["entry_time"], "exit_time": b["time"],
                       "quantity":st["qty"], "entry_fill":st["entry"], "exit_fill":price*(1-slip),
                       "settlement_day":next_day[b["day"]], "pnl": proceeds-st["cost"], "reason": reason})
        st["qty"] = 0
        st["force_exit"] = False
    for t in times:
        for s in SYMBOLS:
            st = states[s]
            idx = maps[s][t]
            b = series[s][idx]
            if idx == 0:
                continue
            prev, ind = series[s][idx-1], inds[s][idx-1]
            matured = [amount for day,amount in st["pending"] if day<=b["day"]]
            st["cash"] += sum(matured)
            st["pending"] = [(day,amount) for day,amount in st["pending"] if day>b["day"]]
            sold = False
            if st["qty"]:
                st["age"] += 1
                if st["force_exit"] or signal(kind,threshold,prev,ind,True) or st["age"]>=max_age:
                    sell(s,b,b["open"],"drawdown" if st["force_exit"] else "signal_or_time")
                    sold = True
                elif b["low"] <= st["stop"]:
                    sell(s,b,min(b["open"],st["stop"]),"stop")
                    sold = True
            if not sold and not st["qty"] and not halted and signal(kind,threshold,prev,ind,False):
                # Stop distance sizes approximately <=0.5% of initial portfolio risk.
                # Actual loss can be larger on a gap; stop isn't a guaranteed bound.
                distance = 2*ind["atr"]
                fill = b["open"]*(1+slip)
                budget = min(st["cash"], .4*CAPITAL)
                qty = min(budget/fill, .005*CAPITAL/distance) if distance>0 else 0
                if qty*fill>=1:
                    st.update(qty=qty, entry=fill, cost=qty*fill, entry_time=t,
                              stop=max(0, fill-distance), age=0)
                    st["cash"] -= qty*fill
                    if b["low"]<=st["stop"]:
                        sell(s,b,min(b["open"],st["stop"]),"entry_bar_stop")
            assert st["cash"]>=-1e-7
        value = .2*CAPITAL + sum(st["cash"]+sum(a for _,a in st["pending"])+
                                st["qty"]*series[s][maps[s][t]]["close"] for s,st in states.items())
        peak = max(peak,value)
        if brake and value/peak-1<=-STOP_DRAWDOWN:
            halted = True
            for st in states.values():
                st["force_exit"] = bool(st["qty"])
        curve.append({"time": t,"value":value})
    # Conservative exit valuation only: costs on remaining shares at final close.
    # Terminal proceeds are valued, not reused; not a claimed next-open live fill.
    final = times[-1]
    for s,st in states.items():
        if st["qty"]:
            b = series[s][maps[s][final]]
            sell(s,b,b["close"],"terminal_valuation")
    curve[-1]["value"] = .2*CAPITAL+sum(st["cash"]+sum(a for _,a in st["pending"]) for st in states.values())
    return metrics(curve,trades,halted), curve, trades


def benchmark(series,start,end,symbols=("RXRX","TEM"),bps=10):
    maps = {s:{b["time"]:b for b in series[s]} for s in symbols}
    times = sorted(set.intersection(*(set(m) for m in maps.values())))
    times = [t for t in times if start<=t[:10]<end]
    slip = bps/10000
    shares = {s:.8*CAPITAL/len(symbols)/(maps[s][times[0]]["open"]*(1+slip)) for s in symbols}
    curve = [{"time":t,"value":.2*CAPITAL+sum(q*maps[s][t]["close"] for s,q in shares.items())} for t in times]
    curve[-1]["value"] = .2*CAPITAL+sum(q*maps[s][times[-1]]["close"]*(1-slip) for s,q in shares.items())
    return metrics(curve,[],False)


def main():
    validate_policy()
    output = ROOT/"results"
    output.mkdir(exist_ok=True)
    report = {"method": {"capital":CAPITAL,"allocation":"40% per ticker cap; 20% reserve",
                          "risk_per_trade":"0.5% initial capital at 2 ATR stop; gap losses can exceed",
                          "holdout_start":HOLDOUT,"end_exclusive":CUTOFF,
                          "cash_settlement":"T+1 next observed trading day; no proceeds reuse same day",
                          "fees":"10/25/50 bps adverse execution EACH side; no tax or cash interest",
                          "drawdown_brake":STOP_DRAWDOWN,
                          "limitations":["Price-only; no historical catalyst blackout calendar",
                                         "Fractional market execution proxy, not limit-order validation",
                                         "Hourly feed may omit partial opening/closing intervals",
                                         "Universe selected by user after observing performance",
                                         "Exploratory retrospective evaluation, not a pristine untouched test: range family added after inspecting initial holdout",
                                         "No live or paper order executor"]},"intervals":{}}
    for interval in ("daily","hourly"):
        series,audit = load(interval)
        inds = {s:indicators(b) for s,b in series.items()}
        start = max(series[s][100]["day"] for s in SYMBOLS)
        candidates = []
        for kind in ("pullback","range","breakout"):
            for threshold in ((1.0,1.5,2.0) if kind in ("pullback","range") else (1.5,)):
                train,_,_ = simulate(series,inds,interval,kind,start,HOLDOUT,threshold)
                eligible = train["return_pct"]>0 and train["closed_trades"]>=8 and not train["halted"]
                score = train["return_pct"]+train["max_drawdown_pct"]
                candidates.append({"kind":kind,"threshold":threshold,"training":train,"eligible":eligible,"score":score})
        eligible = [c for c in candidates if c["eligible"]]
        # Freeze selection solely from training. If none pass, no deployable winner.
        chosen = max(eligible or candidates,key=lambda c:c["score"])
        frozen = {"kind":chosen["kind"],"threshold":chosen["threshold"],"training_qualified":bool(eligible)}
        tests = []
        for c in candidates:
            for bps in (10,25,50):
                test,curve,trades = simulate(series,inds,interval,c["kind"],HOLDOUT,CUTOFF,c["threshold"],bps)
                tests.append({"kind":c["kind"],"threshold":c["threshold"],"bps_each_side":bps,"metrics":test})
                if c is chosen and bps==10:
                    for name,rows in (("curve",curve),("trades",trades)):
                        with (output/f"{interval}_{name}.csv").open("w") as f:
                            if rows:
                                writer = csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        selected = next(t["metrics"] for t in tests if t["kind"]==chosen["kind"] and t["threshold"]==chosen["threshold"] and t["bps_each_side"]==10)
        selected_stress = next(t["metrics"] for t in tests if t["kind"]==chosen["kind"] and t["threshold"]==chosen["threshold"] and t["bps_each_side"]==50)
        passed = bool(eligible) and selected["return_pct"]>0 and selected["closed_trades"]>=8 and not selected["halted"] and selected_stress["return_pct"]>0
        raw = (ROOT/"data"/f"{interval}.json").read_bytes()
        report["intervals"][interval] = {"audit":audit,"data_sha256":hashlib.sha256(raw).hexdigest(),
                                        "training_candidates":candidates,"frozen_selection":frozen,"holdout_tests":tests,
                                        "selected_holdout":selected,"preliminary_validation_pass":passed,
                                        "benchmarks":{"equal_weight_80pct":benchmark(series,HOLDOUT,CUTOFF),
                                                      "spy_80pct_price_only":benchmark(series,HOLDOUT,CUTOFF,("SPY",)),
                                                      "cash_zero_interest_return_pct":0}}
    (output/"summary.json").write_text(json.dumps(report,indent=2)+"\n")
    lines = ["# RXRX / TEM backtest — October 9, 2026", "",
             "**Decision: neither selected model passed validation. Live orders remain disabled.**", "",
             "SMMT is protected. The user's goal is to double managed funds excluding SMMT by December 31, 2026; these results do not support that goal.", "",
             "## What was tested", "",
             "Three long-only strategy families: (1) buy a 20-bar standardized pullback only above a 100-bar moving average, (2) the same pullback without the trend filter, and (3) break above the prior 20-bar high while above the 100-bar average. Pullback thresholds: 1, 1.5, and 2 standard deviations. Exit pullbacks at the 20-bar mean or a time limit; exit breakouts below the prior 10-bar low or a time limit. All use fixed 2 ATR stops.", "",
             "Maximum 40% initial capital per ticker, 20% reserve, and approximately 0.5% initial capital risk per entry before gaps/costs. No averaging down, leverage, shorts, or broker execution. A 10% marked-to-market strategy drawdown freezes entries and schedules strategy exits at the next open. A gap can exceed the threshold. Existing non-SMMT stocks were not liquidated or modeled as cash; this is a hypothetical separately funded sleeve.", "",
             "Initial illustrative capital is $1,052.50; percentage results scale before fill/liquidity effects. This is NOT the current live account cash balance or a live funding commitment.", "",
             "## Historical evaluation: June 1–October 8, 2026", "",
             "Selection thresholds use pre-June training return minus maximum drawdown, requiring positive training return, at least eight closed trades, and no drawdown halt. Training selects daily range/1.5 and hourly range/2.0. Neither passes subsequent evaluation. The range family was added after inspecting the initial families' evaluation results: treat this as exploratory research, not a pristine untouched holdout. Forward monitoring is necessary.", "",
             "| Frequency | Strategy | Threshold | Return after 10 bps/side | Maximum drawdown | Closed trades |", "|---|---|---:|---:|---:|---:|"]
    for interval,d in report["intervals"].items():
        for t in d["holdout_tests"]:
            if t["bps_each_side"]==10:
                m=t["metrics"]
                lines.append(f"| {interval} | {t['kind']} | {t['threshold']} | {m['return_pct']:+.3f}% | {m['max_drawdown_pct']:.3f}% | {m['closed_trades']} |")
    lines += ["", "### Execution-cost sensitivity for training-selected candidates", "",
              "| Frequency | 10 bps each side | 25 bps each side | 50 bps each side |", "|---|---:|---:|---:|"]
    for interval,d in report["intervals"].items():
        sel=d["frozen_selection"]
        tests=[t for t in d["holdout_tests"] if t["kind"]==sel["kind"] and t["threshold"]==sel["threshold"]]
        lines.append("| "+interval+" | "+" | ".join(f"{t['metrics']['return_pct']:+.3f}%" for t in tests)+" |")
    lines += ["", "### Comparisons", "",
              "Daily buy-and-hold with 40% RXRX, 40% TEM, 20% cash returned +20.530% but suffered -23.426% maximum drawdown. It has no drawdown brake and is shown to illustrate the return/risk tradeoff, not as a qualifying strategy. An 80% SPY / 20% cash price-only comparison returned +1.803% with -3.599% drawdown. SPY dividends and cash interest are omitted.", "",
              "## Data and verification", "",
              "Robinhood MCP regular-session split-adjusted OHLCV snapshots are saved in data/. Synthetic/interpolated and zero-volume bars were excluded, including pre-IPO TEM placeholders. Both frequencies end October 8 for comparable completed-session evaluation. Daily raw TEM data begin June 14, 2024; real hourly data begin December 22, 2025. Indicators need 100-bar warmup. Hourly series may omit partial opening/closing intervals; this is not a complete intraday execution audit.", "",
              "Signals use completed bars and execute at the next available bar open with adverse slippage. Stops use the worse of open and stop before slippage. Same-bar entry stops are handled conservatively. Cash sale proceeds cannot be reused before the next observed trading date (T+1). Terminal positions are valued at the last close less exit costs; that is not a claimed live close fill.", "",
              "Costs are estimates, not measured bid/ask reconstruction. No taxes, dividends, cash interest, impact, failed fills, order latency, or historical event blackout calendar. Chosen tickers introduce selection bias. Training/evaluation dates are retrospective and the sample is short. Positive returns from a variant chosen after inspecting this period would not establish a live edge.", "",
              "Four engine checks verify future-bar isolation, cash settlement, gap-stop execution/drawdown halting, and the SMMT policy. JSON results include raw-data SHA-256 hashes; CSV files contain selected-candidate equity curves and trades.", "",
              "Run `nix develop --command python3 backtest.py` and `nix develop --command python3 -m unittest -v test_backtest.py`.", "",
              "## Next step", "",
              "Hourly observation during regular market hours plus a daily review. Track the frozen daily and hourly range candidates without retuning from each new result. Keep live orders disabled. Observe catalysts and data quality; log new completed-bar signals once. Do not equate monitoring with a paper portfolio or filled orders. Before any deployment, require a defined funding baseline excluding SMMT, a separately verified paper executor, more independent evidence, reconciliation/idempotency, tradability checks, and broker approval handling.", "",
              "RXRX has a November 2, 2026 clinical presentation, so recurring monitoring must check scheduled clinical and earnings catalysts. [Recursion's Q2 release](https://ir.recursion.com/news-releases/news-release-details/recursion-reports-second-quarter-financial-results-genentech). Robinhood can require approval on individual trades even with approvals off. [Robinhood agent trading documentation](https://robinhood.com/us/en/support/articles/trading-with-your-agent/). More primary sources are in research_notes.md."]
    (ROOT/"BACKTEST_REPORT.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({i:{k:v for k,v in d.items() if k in ("frozen_selection","selected_holdout","preliminary_validation_pass","benchmarks")} for i,d in report["intervals"].items()},indent=2))


if __name__ == "__main__":
    main()
