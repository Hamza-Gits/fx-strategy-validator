"""
GBPUSD-Only Deep Optimization
==============================
GBPUSD is the only pair with cost-adjusted edge across both periods.
This script does a finer-grained parameter search on GBPUSD only,
with costs applied, and additionally tracks:
  - Max consecutive loss streak (drawdown driver)
  - Max equity drawdown in % terms (prop firm DD constraint)
  - Trade frequency (consistency rule check)
  - Average win and loss in $ terms

Outputs the top 5 GBPUSD configurations ranked by avg OOS PF across
both periods, with full risk metrics for each.
"""

import sys
import os
import json
import argparse
from datetime import datetime, timezone
from itertools import product
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf-16'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 'validation_harness'))
from harness import StrategyResult, Trade, run_validation, GateConfig, load_mt5_csv_pair
from strategy_london_breakout import _run_single_symbol
from audit_per_pair import apply_cost_to_trades

REPO_ROOT   = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(REPO_ROOT, 'results')
DATA_DIR    = os.path.join(REPO_ROOT, 'data')

PERIOD_A = ('2015-01-01', '2019-12-31')
PERIOD_B = ('2020-01-01', '2024-12-31')


def equity_curve_stats(trades, risk_per_trade_dollars=100.0):
    """Compute drawdown and streak stats from a list of Trades."""
    if not trades:
        return {'max_dd_pct': 0, 'max_loss_streak': 0, 'avg_win': 0,
                'avg_loss': 0, 'expectancy': 0}
    pnls = np.array([t.pnl for t in trades])
    equity = 100000.0 + np.cumsum(pnls)  # synthetic 100k account
    peaks = np.maximum.accumulate(equity)
    drawdowns = (peaks - equity) / peaks * 100
    max_dd = drawdowns.max()

    streak = 0
    max_streak = 0
    for p in pnls:
        if p < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = losses.mean() if len(losses) > 0 else 0
    win_rate = len(wins) / len(pnls)
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    return {
        'max_dd_pct': round(max_dd, 2),
        'max_loss_streak': max_streak,
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'win_rate': round(win_rate, 3),
        'expectancy': round(expectancy, 2),
    }


def evaluate(h1, period, params, cost_pips=2.0):
    start, end = period
    sliced = h1.loc[start:end].copy()
    if len(sliced) < 100:
        return None

    raw = _run_single_symbol(
        sliced, 'GBPUSD',
        tp_mult=params['tp_mult'],
        use_trend_filter=params['use_trend'],
        w1_ema_period=params['w1_ema'],
        min_range_pips=params['min_range'],
        max_range_pips=params['max_range'],
    )
    if len(raw) < 30:
        return None

    cost_trades = apply_cost_to_trades(raw, 'GBPUSD', cost_pips=cost_pips)

    def strategy_fn(anchor):
        s = anchor.index[0]
        e = anchor.index[-1]
        in_window = sorted([t for t in cost_trades if s <= t.entry_time <= e],
                          key=lambda x: x.entry_time)
        return StrategyResult(trades=in_window, name='gbpusd_london')

    config = GateConfig(
        min_oos_trades=30, bootstrap_percentile=95.0,
        deflated_sharpe_pvalue=0.007, max_is_oos_degradation=0.30,
        n_resamples=2000, num_prior_trials=9,
    )
    v = run_validation(strategy_fn, sliced, config=config, verbose=False)

    # Compute risk metrics on OOS only
    is_n = int(0.7 * len(cost_trades))
    oos_trades = sorted(cost_trades, key=lambda t: t.entry_time)[is_n:]
    risk = equity_curve_stats(oos_trades)

    return {
        'passed': v.passed, 'is_pf': round(v.is_pf, 3),
        'oos_pf': round(v.oos_pf, 3), 'oos_n': v.oos_n,
        'bootstrap': round(v.bootstrap_percentile_achieved, 1),
        'dsr_p': round(v.deflated_pvalue, 4),
        'total_trades': len(cost_trades),
        'risk': risk,
    }


def main():
    print(f"\n{'='*60}\n  GBPUSD Deep Optimization (Council follow-up)\n{'='*60}")

    h1 = load_mt5_csv_pair(DATA_DIR, 'GBPUSD')
    print(f"  GBPUSD: {len(h1)} H1 bars\n")

    # Finer grid focused on GBPUSD's sweet spot
    grid = list(product(
        [1.0, 1.25, 1.5, 1.75, 2.0],     # tp_mult
        [5, 10, 15],                       # min_range_pips
        [60, 80, 100],                     # max_range_pips
        [False, True],                     # use_trend
        [10, 20, 26],                      # w1_ema (only used if trend on)
    ))
    # Dedupe: when use_trend=False, w1_ema is irrelevant
    seen = set()
    unique = []
    for g in grid:
        tp, mn, mx, ut, w = g
        key = (tp, mn, mx, ut, w if ut else None)
        if key not in seen:
            seen.add(key)
            unique.append(g)
    grid = unique
    print(f"  Grid: {len(grid)} unique configurations\n")

    results = []
    for idx, (tp, mn, mx, ut, w) in enumerate(grid):
        params = {'tp_mult': tp, 'min_range': mn, 'max_range': mx,
                  'use_trend': ut, 'w1_ema': w}
        ra = evaluate(h1, PERIOD_A, params)
        if ra is None:
            continue
        rb = evaluate(h1, PERIOD_B, params)
        if rb is None:
            continue

        both_pass = ra['passed'] and rb['passed']
        avg_pf = (ra['oos_pf'] + rb['oos_pf']) / 2

        tag = "BOTH PASS" if both_pass else (
            "A pass" if ra['passed'] else "B pass" if rb['passed'] else "fail")
        print(f"  [{idx+1:>3}/{len(grid)}] TP={tp} mn={mn} mx={mx} "
              f"trend={'Y' if ut else 'N'}{f' W1={w}' if ut else ''}  "
              f"A:{ra['oos_pf']} B:{rb['oos_pf']}  avg={avg_pf:.3f}  {tag}")

        if both_pass:
            results.append({
                'params': params, 'avg_oos_pf': round(avg_pf, 3),
                'period_a': ra, 'period_b': rb,
            })

    results.sort(key=lambda r: r['avg_oos_pf'], reverse=True)

    print(f"\n\n{'#'*60}\n# TOP 5 GBPUSD CONFIGURATIONS (both periods PASS net of costs)\n{'#'*60}\n")
    if not results:
        print("  NO CONFIGURATION PASSED BOTH PERIODS NET OF COSTS")
        print("  Even GBPUSD-alone may not have a robust edge after costs.")
    for i, r in enumerate(results[:5], 1):
        p = r['params']
        print(f"\n#{i}: TP={p['tp_mult']}x  range={p['min_range']}-{p['max_range']}pips  "
              f"trend_filter={'ON W1='+str(p['w1_ema']) if p['use_trend'] else 'OFF'}")
        print(f"  Period A: OOS PF={r['period_a']['oos_pf']}  N={r['period_a']['oos_n']}  "
              f"BS={r['period_a']['bootstrap']}%  WR={r['period_a']['risk']['win_rate']}")
        print(f"            MaxDD={r['period_a']['risk']['max_dd_pct']}%  "
              f"MaxLossStreak={r['period_a']['risk']['max_loss_streak']}")
        print(f"  Period B: OOS PF={r['period_b']['oos_pf']}  N={r['period_b']['oos_n']}  "
              f"BS={r['period_b']['bootstrap']}%  WR={r['period_b']['risk']['win_rate']}")
        print(f"            MaxDD={r['period_b']['risk']['max_dd_pct']}%  "
              f"MaxLossStreak={r['period_b']['risk']['max_loss_streak']}")
        print(f"  Avg OOS PF: {r['avg_oos_pf']}")

    out_file = os.path.join(RESULTS_DIR, 'gbpusd_top.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            'top_5': results[:5], 'total_passing': len(results),
            'cost_pips': 2.0,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)
    print(f"\nResults saved to {out_file}")


if __name__ == '__main__':
    main()
