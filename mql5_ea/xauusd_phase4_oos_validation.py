#!/usr/bin/env python3
"""
XAUUSD Phase 4: OOS validation + cost-realism check.
Capital is no longer the constraint — only the edge matters.

Best IS config (Phase 1B): ATR=1.25 / Hold=5 / W1_EMA filter
  IS:  PF 2.472, WR 64.1%, AvgR 0.386, N=39 (2013-2020)

Tests:
  1. OOS (2021-2025) at zero cost — does the edge survive?
  2. OOS with realistic Aqua-equivalent costs (40c spread + $7/lot RTT + 1-pip slip).
  3. Comparison: does AvgR_OOS / AvgR_IS suggest overfit (>30% degradation = red flag).
"""
import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'validation_harness'))
from harness import load_mt5_csv
from xauusd_phase1b_multiday_atr import run

DATA_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'XAUUSD_H1_2013-2025.csv')

# Best IS config from Phase 1B
ATR_MULT       = 1.25
MAX_HOLD_DAYS  = 5
FILTER_MODE    = 'W1_EMA'

# Cost model — realistic XAUUSD execution
SPREAD_DOLLARS    = 0.40   # 40 cents typical XAUUSD spread on retail
SLIPPAGE_DOLLARS  = 0.10   # 10 cents per side
COMMISSION_R_FRAC = 0.02   # ~2% of stop distance for $7/lot RTT on tight stops


def cost_adjust(result_dict, atr_estimate=15.0, sl_atr_mult=1.5):
    """Subtract realistic execution cost from AvgR.
    Cost in dollars: spread + 2*slippage = 0.60.
    Stop distance = 1.5 * ATR ~ 22.5 dollars on gold.
    Cost as fraction of R = 0.60 / 22.5 = 0.027 R per trade."""
    cost_R = (SPREAD_DOLLARS + 2 * SLIPPAGE_DOLLARS) / (sl_atr_mult * atr_estimate) + COMMISSION_R_FRAC
    n = result_dict['count']
    avg_r_net = result_dict['avg_r'] - cost_R
    # Recompute PF assuming cost shifts every trade equally
    # Simpler: report net AvgR and total R
    return {
        **result_dict,
        'cost_R':      cost_R,
        'avg_r_net':   avg_r_net,
        'total_R_net': avg_r_net * n,
        'edge_alive':  avg_r_net > 0,
    }


def main():
    print("Loading XAUUSD H1 (2013-2025)...")
    h1 = load_mt5_csv(DATA_FILE)
    is_h1  = h1.loc[:'2020-12-31']
    oos_h1 = h1.loc['2021-01-01':]
    print(f"  IS bars:  {len(is_h1)} ({is_h1.index[0].date()} -> {is_h1.index[-1].date()})")
    print(f"  OOS bars: {len(oos_h1)} ({oos_h1.index[0].date()} -> {oos_h1.index[-1].date()})\n")

    print("=" * 92)
    print(f"PHASE 4 OOS VALIDATION — config: ATR={ATR_MULT} / Hold={MAX_HOLD_DAYS}d / Filter={FILTER_MODE}")
    print("=" * 92)

    configs = [
        (1.25, 5, 'W1_EMA'),  # high edge, low N
        (0.75, 3, 'NONE'),    # high N, lower edge
        (1.00, 5, 'W1_EMA'),  # middle ground
        (1.25, 3, 'W1_EMA'),  # variant
    ]
    print(f"\n{'Config':<22} {'Win':<10} {'N':<5} {'PF':<8} {'WR%':<7} {'AvgR':<8} {'AvgR_net':<10} {'TotalR_net':<11} {'Edge?'}")
    print("-" * 100)
    for atr_m, hold, fmode in configs:
        for label, data in [('IS', is_h1), ('OOS', oos_h1)]:
            r = run(data, atr_m, hold, fmode)
            if r is None:
                continue
            net = cost_adjust(r)
            tag = f"ATR={atr_m}/H={hold}/{fmode}"
            print(f"{tag:<22} {label:<10} {r['count']:<5} {r['pf']:<8.3f} {r['wr']:<7.1f} "
                  f"{r['avg_r']:<8.3f} {net['avg_r_net']:<10.3f} {net['total_R_net']:<11.2f} "
                  f"{'YES' if net['edge_alive'] else 'NO'}")
        print()
    return

    is_r  = run(is_h1,  ATR_MULT, MAX_HOLD_DAYS, FILTER_MODE)
    oos_r = run(oos_h1, ATR_MULT, MAX_HOLD_DAYS, FILTER_MODE)

    if is_r is None or oos_r is None:
        print("ERROR: no trades produced.")
        return

    is_net  = cost_adjust(is_r)
    oos_net = cost_adjust(oos_r)

    print(f"\n{'Window':<10} {'N':<5} {'PF':<8} {'WR%':<7} {'AvgR':<8} {'AvgR_net':<10} {'TotalR_net':<11} {'Edge?'}")
    print("-" * 92)
    print(f"{'IS  13-20':<10} {is_r['count']:<5} {is_r['pf']:<8.3f} {is_r['wr']:<7.1f} "
          f"{is_r['avg_r']:<8.3f} {is_net['avg_r_net']:<10.3f} {is_net['total_R_net']:<11.2f} "
          f"{'YES' if is_net['edge_alive'] else 'NO'}")
    print(f"{'OOS 21-25':<10} {oos_r['count']:<5} {oos_r['pf']:<8.3f} {oos_r['wr']:<7.1f} "
          f"{oos_r['avg_r']:<8.3f} {oos_net['avg_r_net']:<10.3f} {oos_net['total_R_net']:<11.2f} "
          f"{'YES' if oos_net['edge_alive'] else 'NO'}")

    print()
    print(f"Cost model: spread={SPREAD_DOLLARS} + 2*slip={SLIPPAGE_DOLLARS} on 1.5*ATR(~15) stop "
          f"+ commission ~{COMMISSION_R_FRAC:.0%} of R = {is_net['cost_R']:.3f}R/trade")
    print()

    # Degradation check
    if is_r['avg_r'] > 0:
        degr = 100 * (is_r['avg_r'] - oos_r['avg_r']) / is_r['avg_r']
        print(f"AvgR degradation IS->OOS: {degr:+.1f}%  (>30% = overfit warning)")

    print()
    print("=" * 92)
    print("VERDICT GATES:")
    print(f"  OOS PF (zero cost) >= 1.5 ............ {oos_r['pf']:.3f}  {'PASS' if oos_r['pf'] >= 1.5 else 'FAIL'}")
    print(f"  OOS net AvgR > 0 ..................... {oos_net['avg_r_net']:+.3f}  {'PASS' if oos_net['edge_alive'] else 'FAIL'}")
    print(f"  OOS N >= 15 .......................... {oos_r['count']}     {'PASS' if oos_r['count'] >= 15 else 'FAIL'}")
    print(f"  Degradation < 30% .................... {degr:+.1f}%  {'PASS' if degr < 30 else 'FAIL'}")
    print("=" * 92)


if __name__ == '__main__':
    main()
