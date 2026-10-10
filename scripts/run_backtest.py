#!/usr/bin/env python3
"""Run the full research study and write results/study.json + BACKTEST_REPORT.md.

    python3 scripts/run_backtest.py

Reads only the archived snapshots in data/. Places no orders.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quanttrader.config import Policy
from quanttrader.engine import Engine, summarize
from quanttrader.feed import SnapshotFeed
from quanttrader.research import StudyConfig, battery, data_quality, describe_stats, goal_test, portfolio_vol
from quanttrader.walkforward import make_folds, walk_forward

UNIVERSE = ("RXRX", "TEM")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    cfg = StudyConfig()
    policy = Policy.load()
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)

    feed = SnapshotFeed(cfg.interval, cutoff=cfg.cutoff)
    universe = feed.universe(list(UNIVERSE))
    timeline = universe.timeline()
    folds = make_folds(timeline, n_folds=cfg.folds, train_frac=cfg.train_frac)

    stats = describe_stats(universe.series["RXRX"], universe.series["TEM"])
    report: dict = {
        "config": asdict(cfg),
        "policy": {"account": policy.account.account_number, "type": policy.account.type,
                   "protected": sorted(policy.protected_symbols),
                   "permitted": sorted(policy.permitted_symbols),
                   "max_weight_per_symbol": policy.risk.max_weight_per_symbol,
                   "max_drawdown": policy.risk.max_drawdown,
                   "live_orders_enabled": policy.safety.live_orders_enabled},
        "data_quality": data_quality(universe),
        "series_stats": stats,
        "sample": {"bars": len(timeline), "first": timeline[0][:10], "last": timeline[-1][:10]},
        "source_data": {
            name: {"sha256": _sha256(ROOT / "data" / f"{name}.json"),
                   "bytes": (ROOT / "data" / f"{name}.json").stat().st_size}
            for name in ("daily", "hourly")
        },
        "folds": [f.label for f in folds],
    }

    # 1. In-sample, brake off: is there any signal at all?
    report["insample_signals"] = {}
    for name, strat in battery(UNIVERSE, per_name=0.5).items():
        res = Engine(policy, cfg.capital, enforce_brake=False).run(universe, strat, cfg.interval)
        report["insample_signals"][name] = summarize(res)

    # 2. Drawdown compatibility: what gross exposure can a 10% DD budget survive?
    dvol = {}
    for gross in cfg.exposures:
        v = portfolio_vol(stats["sd_a"], stats["sd_b"], stats["corr"], gross)
        dvol[f"{gross:.2f}"] = {"daily_vol": v, "annual_vol": v * (252 ** 0.5)}
    report["exposure_vol"] = dvol

    # 3. Walk-forward, brake ENFORCED, across exposure levels.
    report["walk_forward"] = {}
    for gross in cfg.exposures:
        per_name = gross / len(UNIVERSE)
        rep = walk_forward(universe, battery(UNIVERSE, per_name), policy, cfg.capital,
                           folds, min_trades=4)
        report["walk_forward"][f"{gross:.2f}"] = {
            "per_name_weight": per_name,
            "aggregate": rep["aggregate"],
            "per_fold_detail": [
                {"fold": f["fold"], "chosen": f["chosen"], "test": f["test"]}
                for f in rep["folds"]
            ],
            "all_candidates_by_window": [
                {"fold": f["fold"], **(f.get("test_all") or {})} for f in rep["folds"]
            ],
        }

    # 4. Sub-period stability: does the signal survive in every regime, or is it
    #    one lucky stretch dressed up as an edge?
    thirds = [timeline[k * len(timeline) // 3][:10] for k in (0, 1, 2)] + [timeline[-1][:10]]
    report["subperiod_stability"] = {
        "windows": thirds,
        "strategies": {},
    }
    for name, strat in battery(UNIVERSE, per_name=0.5).items():
        rows = []
        for k in range(3):
            res = Engine(policy, cfg.capital, enforce_brake=False).run(
                universe, strat, cfg.interval, start=thirds[k], end=thirds[k + 1])
            rows.append(summarize(res)["return_pct"])
        report["subperiod_stability"]["strategies"][name] = {
            "returns": rows, "all_positive": all(r > 0 for r in rows),
            "sign_flips": sum(1 for a, b in zip(rows, rows[1:]) if (a > 0) != (b > 0))}

    # 5. Cost stress on the strongest in-sample signals.
    strong = sorted(report["insample_signals"],
                    key=lambda n: report["insample_signals"][n]["sharpe"], reverse=True)[:3]
    report["cost_stress"] = {}
    for name in strong:
        strat = battery(UNIVERSE, per_name=0.5)[name]
        rows = {}
        for bps in (0, 5, 10, 25, 50):
            res = Engine(policy, cfg.capital, slippage_bps=bps, enforce_brake=False).run(
                universe, strat, cfg.interval)
            rows[str(bps)] = summarize(res)
        report["cost_stress"][name] = rows

    # 6. The goal test.
    best = max(report["insample_signals"].values(), key=lambda m: m["sharpe"])
    report["goal_test"] = {
        "gross_80pct": goal_test(cfg.capital, cfg.target_multiple, cfg.sessions_to_goal,
                                 dvol["0.80"]["daily_vol"]),
        "gross_20pct": goal_test(cfg.capital, cfg.target_multiple, cfg.sessions_to_goal,
                                 dvol["0.20"]["daily_vol"]),
        "best_insample_sharpe": best["sharpe"],
    }

    (out_dir / "study.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    write_markdown(report, ROOT / "BACKTEST_REPORT.md")
    print(json.dumps({
        "bars": report["sample"]["bars"],
        "insample_top": strong,
        "oos": {k: v["aggregate"]["out_of_sample_return_pct"] for k, v in report["walk_forward"].items()},
        "p_double_80pct": report["goal_test"]["gross_80pct"]["p_double_zero_drift"],
    }, indent=2))
    return 0


def write_markdown(r: dict, path: Path) -> None:
    L: list[str] = []
    add = L.append
    add("# RXRX / TEM trading study")
    add("")
    add(f"Sample {r['sample']['first']} to {r['sample']['last']} "
        f"({r['sample']['bars']} aligned sessions). Capital modelled: "
        f"${r['config']['capital']:,.2f}. Universe: {', '.join(r['policy']['permitted'])}. "
        f"Protected: {', '.join(r['policy']['protected'])}.")
    add("")
    add("## 0. Source data")
    add("")
    add("Raw broker bars are **not** redistributed in this repository (broker market-data")
    add("terms). Re-fetch with `python3 scripts/fetch_data.py` and verify the digest:")
    add("")
    add("| Snapshot | Bytes | SHA-256 |")
    add("|---|---:|---|")
    for name, d in r.get("source_data", {}).items():
        add(f"| data/{name}.json | {d['bytes']:,} | `{d['sha256'][:32]}...` |")
    add("")
    add("## 1. Data quality")
    add("")
    add("| Symbol | Bars | Sessions | First | Last | Median $ volume |")
    add("|---|---:|---:|---|---|---:|")
    for s, d in r["data_quality"].items():
        add(f"| {s} | {d['bars']} | {d['sessions']} | {d['first']} | {d['last']} | "
            f"{d['median_dollar_volume']:,.0f} |")
    st = r["series_stats"]
    add("")
    add(f"Daily volatility: RXRX {st['sd_a']:.2%}, TEM {st['sd_b']:.2%}, "
        f"correlation {st['corr']:.3f}.")
    add("")
    add("## 2. Is there a signal? (in-sample, no drawdown brake, 10 bps/side)")
    add("")
    add("Diagnostic only. No brake means these are not deployable; they show whether a")
    add("strategy has any raw signal before the risk limit is applied.")
    add("")
    add("| Strategy | Return | Max DD | Sharpe | Trades | Turnover (x equity) |")
    add("|---|---:|---:|---:|---:|---:|")
    for n, m in sorted(r["insample_signals"].items(), key=lambda kv: -kv[1]["sharpe"]):
        add(f"| {n} | {m['return_pct']:+.2f}% | {m['max_drawdown_pct']:.2f}% | "
            f"{m['sharpe']:.2f} | {m['trades']} | {m['turnover_x_equity']:.1f} |")
    add("")
    add("## 3. Can a 10% drawdown budget survive this much exposure?")
    add("")
    add("| Gross exposure | Daily vol | Annualised vol |")
    add("|---|---:|---:|")
    for k, v in r["exposure_vol"].items():
        add(f"| {float(k):.0%} | {v['daily_vol']:.2%} | {v['annual_vol']:.1%} |")
    add("")
    add("## 4. Walk-forward, out of sample only (brake enforced)")
    add("")
    add("Parameters are chosen on each training window and frozen, then applied to the")
    add("window that follows. Chained out-of-sample return is the headline.")
    add("")
    add("| Gross exposure | OOS return | Worst window DD | Folds with a qualifier | Selections |")
    add("|---|---:|---:|---|---|")
    for k, v in r["walk_forward"].items():
        a = v["aggregate"]
        add(f"| {float(k):.0%} | {a['out_of_sample_return_pct']:+.2f}% | "
            f"{a['worst_window_drawdown_pct']:.2f}% | "
            f"{a['folds_with_a_qualifying_candidate']}/{a['folds']} | "
            f"{', '.join(sorted(set(a['selections']))) or '-'} |")
    add("")
    add("### Every candidate, every out-of-sample window (80% gross)")
    add("")
    windows = r["walk_forward"].get("0.80", {}).get("all_candidates_by_window", [])
    if windows:
        names = [k for k in windows[0] if k != "fold"]
        add("| Window | " + " | ".join(names) + " |")
        add("|---" * (len(names) + 1) + "|")
        for w in windows:
            cells = " | ".join(f"{w[n]['return_pct']:+.2f}%" for n in names)
            add(f"| {w['fold']} | {cells} |")
    add("")
    add("## 5. Sub-period stability")
    add("")
    add("A real edge shows up in every regime. A backtest artefact shows up in one.")
    add("")
    w = r["subperiod_stability"]["windows"]
    add(f"| Strategy | {w[0]}..{w[1]} | {w[1]}..{w[2]} | {w[2]}..{w[3]} | Positive in all | Sign flips |")
    add("|---|---:|---:|---:|---|---:|")
    for n, d in sorted(r["subperiod_stability"]["strategies"].items(),
                       key=lambda kv: -min(kv[1]["returns"])):
        add(f"| {n} | " + " | ".join(f"{x:+.2f}%" for x in d["returns"]) +
            f" | {'yes' if d['all_positive'] else 'no'} | {d['sign_flips']} |")
    add("")
    add("## 6. Execution-cost sensitivity")
    add("")
    for name, rows in r["cost_stress"].items():
        add(f"**{name}**")
        add("")
        add("| bps/side | Return | Max DD | Sharpe | Trades |")
        add("|---|---:|---:|---:|---:|")
        for bps, m in rows.items():
            add(f"| {bps} | {m['return_pct']:+.2f}% | {m['max_drawdown_pct']:.2f}% | "
                f"{m['sharpe']:.2f} | {m['trades']} |")
        add("")
    add("## 7. The doubling goal, tested")
    add("")
    for label, g in (("80% gross", r["goal_test"]["gross_80pct"]),
                     ("20% gross", r["goal_test"]["gross_20pct"])):
        add(f"### {label}")
        add("")
        add(f"Needs {g['required_return_pct']:.1f}% in {g['sessions']} sessions "
            f"({g['sigma_needed']:.2f} sigma at {g['horizon_sd_pct']:.2f}% horizon vol). "
            f"With zero drift, P(success) = {g['p_double_zero_drift']:.4%}.")
        add("")
        add("| Assumed Sharpe | Expected return | P(reach target) | P(brake halts) | Median end | 5th pct | 95th pct |")
        add("|---:|---:|---:|---:|---:|---:|---:|")
        for sh, d in g["by_sharpe"].items():
            add(f"| {sh} | {d['expected_horizon_return_pct']:.1f}% | {d['p_reach_target']:.3%} | "
                f"{d['prob_brake_halts']:.1%} | ${d['median_end']:,.0f} | "
                f"${d['p05_end']:,.0f} | ${d['p95_end']:,.0f} |")
        add("")
    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
