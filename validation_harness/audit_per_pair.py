"""
Per-Pair Audit with Realistic Costs (Council Gate 1 + 2)
=========================================================
Runs the London breakout on EACH pair INDIVIDUALLY (not pooled) with
1.5 pip spread + 0.5 pip slippage = 2 pip round-trip cost subtracted from
every trade's PnL.

Outputs per-pair stats for Period A (2015-19) and Period B (2021-24):
  - Trade count (IS + OOS)
  - Win rate (IS + OOS)
  - Profit factor (IS + OOS)
  - Bootstrap percentile vs zero-edge null
  - Deflated Sharpe p-value
  - PASS / FAIL per pair

Council kill-gate: if any single pair has OOS PF < 1.15 net of costs,
the pooled headline PF was an illusion. Stop and re-think.
"""

import sys
import os
import argparse
from datetime import datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (
    StrategyResult, Trade, run_validation, GateConfig, load_mt5_csv_pair
)
from strategy_london_breakout import _run_single_symbol

DATA_DIR = os.environ.get(
    'DATA_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
)


def apply_cost_to_trades(trades, symbol, cost_pips=2.0):
    """
    Subtract realistic round-trip cost from each trade's PnL.

    Cost model: 1.5 pip spread + 0.5 pip slippage = 2 pips total round-trip.
    P&L is in normalised dollars where stop_distance moves $100.
    Cost in $ = (cost_pips * pip_size / stop_distance) * NOTIONAL_RISK ($100).
    """
    is_jpy = 'JPY' in symbol.upper()
    pip = 0.01 if is_jpy else 0.0001
    cost_price = cost_pips * pip
    NOTIONAL_RISK = 100.0

    adjusted = []
    for t in trades:
        # stop_distance was the basis for original PnL normalisation
        stop_distance = abs(t.entry_price - (t.entry_price - t.pnl/NOTIONAL_RISK *
                                              (1 if t.direction == 1 else -1) *
                                              max(abs(t.exit_price - t.entry_price), 1e-9) /
                                              max(abs(t.exit_price - t.entry_price), 1e-9)))
        # Recover stop distance more cleanly from trade geometry:
        # PnL = (direction*(exit-entry)/stop_distance) * NOTIONAL_RISK
        # so stop_distance = (direction*(exit-entry)) / (PnL/NOTIONAL_RISK)
        if abs(t.pnl) > 1e-9:
            stop_distance = abs(t.direction * (t.exit_price - t.entry_price) / (t.pnl / NOTIONAL_RISK))
        else:
            stop_distance = max(abs(t.exit_price - t.entry_price), pip)

        cost_dollars = (cost_price / stop_distance) * NOTIONAL_RISK
        new_pnl = t.pnl - cost_dollars
        adjusted.append(Trade(
            entry_time=t.entry_time, exit_time=t.exit_time,
            direction=t.direction, entry_price=t.entry_price,
            exit_price=t.exit_price, pnl=new_pnl, bars_held=t.bars_held
        ))
    return adjusted


def run_pair(symbol, h1_data, period_start, period_end,
             tp_mult=1.5, cost_pips=2.0, label=""):
    """Run strategy on a single pair, apply costs, validate."""
    sliced = h1_data.loc[period_start:period_end].copy()
    if len(sliced) < 100:
        return None

    raw_trades = _run_single_symbol(
        sliced, symbol,
        tp_mult=tp_mult,
        use_trend_filter=False,
        min_range_pips=10.0,
        max_range_pips=80.0,
    )

    cost_trades = apply_cost_to_trades(raw_trades, symbol, cost_pips=cost_pips)

    def strategy_fn(anchor_data):
        start = anchor_data.index[0]
        end   = anchor_data.index[-1]
        # Filter trades to this slice
        in_window = [t for t in cost_trades
                     if start <= t.entry_time <= end]
        in_window.sort(key=lambda x: x.entry_time)
        return StrategyResult(trades=in_window, name=f'london_{symbol}')

    config = GateConfig(
        min_oos_trades=30,
        bootstrap_percentile=95.0,
        deflated_sharpe_pvalue=0.007,
        max_is_oos_degradation=0.30,
        n_resamples=10000,
        num_prior_trials=9,
    )

    print(f"\n{'='*60}\n  {label}: {symbol}\n{'='*60}")
    verdict = run_validation(strategy_fn, sliced, config=config)
    return verdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default=DATA_DIR)
    parser.add_argument('--cost-pips', type=float, default=2.0,
                        help='Round-trip cost in pips (1.5 spread + 0.5 slippage = 2.0)')
    parser.add_argument('--tp-mult', type=float, default=1.5)
    args = parser.parse_args()

    SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY']
    print(f"\n{'#'*60}\n# COUNCIL GATE 1+2: Per-Pair Audit with Costs\n{'#'*60}")
    print(f"# TP={args.tp_mult}x  Cost={args.cost_pips} pips round-trip\n")

    pair_data = {}
    for sym in SYMBOLS:
        pair_data[sym] = load_mt5_csv_pair(args.data_dir, sym)
        print(f"  {sym}: {len(pair_data[sym])} H1 bars  "
              f"({pair_data[sym].index[0].date()} to {pair_data[sym].index[-1].date()})")

    PERIOD_A = ('2015-01-01', '2019-12-31')
    PERIOD_B = ('2020-01-01', '2024-12-31')

    summary = []

    for period_label, (start, end) in [('Period A (2015-19)', PERIOD_A),
                                        ('Period B (2020-24)', PERIOD_B)]:
        print(f"\n\n{'#'*60}\n# {period_label}\n{'#'*60}")
        for sym in SYMBOLS:
            v = run_pair(sym, pair_data[sym], start, end,
                         tp_mult=args.tp_mult, cost_pips=args.cost_pips,
                         label=period_label)
            if v is None:
                continue
            summary.append({
                'period': period_label, 'pair': sym,
                'is_pf': v.is_pf, 'oos_pf': v.oos_pf, 'oos_n': v.oos_n,
                'bootstrap': v.bootstrap_percentile_achieved,
                'dsr_p': v.deflated_pvalue,
                'passed': v.passed,
            })

    # Final summary table
    print(f"\n\n{'#'*60}\n# COUNCIL GATE VERDICT\n{'#'*60}")
    print(f"\n{'Period':<22} {'Pair':<8} {'IS PF':>7} {'OOS PF':>7} {'OOS N':>6} "
          f"{'BS%':>6} {'DSR p':>7}  Verdict")
    print("-" * 80)
    for s in summary:
        verdict = 'PASS' if s['passed'] else 'FAIL'
        print(f"{s['period']:<22} {s['pair']:<8} "
              f"{s['is_pf']:>7.3f} {s['oos_pf']:>7.3f} {s['oos_n']:>6} "
              f"{s['bootstrap']:>6.1f} {s['dsr_p']:>7.4f}  {verdict}")

    print(f"\n{'#'*60}")
    print("# COUNCIL KILL-GATE CHECK")
    print(f"{'#'*60}")
    failures = [s for s in summary if not s['passed']]
    weak = [s for s in summary if s['oos_pf'] is not None and s['oos_pf'] < 1.15]
    if weak:
        print(f"\n>>> KILL-GATE TRIPPED: {len(weak)} pair(s) with OOS PF < 1.15 net of costs:")
        for w in weak:
            print(f"    - {w['period']} / {w['pair']}: OOS PF = {w['oos_pf']:.3f}")
        print("\n>>> The pooled PF was an illusion. Strategy is not robust.")
    else:
        print("\n>>> All pairs OOS PF >= 1.15 net of costs. Edge is real.")
        print(">>> Proceed to Gate 3: include 2020 / Gate 4: 2025 forward test.")


if __name__ == '__main__':
    main()
