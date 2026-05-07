"""
Walk-forward analysis for ema_cross_adx.

Council concern: PF 2.46 on a single OOS slice with params chosen on IS = textbook overfit.
True edge survives rolling re-optimization.

Method:
  - Rolling windows: 4-year IS (~25k bars), 1-year OOS (~6k bars), advance 1 year.
  - For each window: optimize fast/slow/adx_min on IS, evaluate best on OOS.
  - Track parameter stability AND OOS PF stability across windows.

If params drift wildly OR OOS PF degrades window-to-window: overfit.
If params cluster AND OOS PF stable: real edge.
"""
import os
import sys
import itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv
from strategy_lib import STRATEGIES
from strategy_battery import (compute_pf, compute_avg_r, trades_to_pnls,
                               cost_adjust_pnls, cost_per_trade_R)


# Parameter grid (smaller — for walk-forward speed)
GRID = {
    'fast':    [10, 20],
    'slow':    [50, 100],
    'adx_min': [20.0, 25.0],
    'sl_mult': [1.5],
    'tp_mult': [2.0, 3.0],
}


def fit_best(h1_window, fn, min_n=20):
    """Fit grid on this window, return best params + metrics by raw PF."""
    keys = list(GRID.keys())
    best = None
    for combo in itertools.product(*[GRID[k] for k in keys]):
        params = dict(zip(keys, combo))
        trades = fn(h1_window, **params)
        if len(trades) < min_n:
            continue
        pnls = trades_to_pnls(trades)
        pf = compute_pf(pnls)
        if best is None or pf > best['pf']:
            best = {'params': params, 'pf': pf, 'n': len(trades)}
    return best


def main():
    DATA = os.path.join(os.path.dirname(__file__), '..', 'data',
                        'XAUUSD_H1_2013-2025.csv')
    h1 = load_mt5_csv(DATA).loc['2014-01-01':'2024-12-31']
    fn = STRATEGIES['ema_cross']['fn']
    cost_R = cost_per_trade_R()

    # Define rolling windows: IS=4y, OOS=1y, step 1y
    print("="*78)
    print("  WALK-FORWARD ANALYSIS — ema_cross_adx (XAUUSD)")
    print("  IS=4yr, OOS=1yr, rolling annually 2018-2024")
    print("="*78)

    starts = [2014, 2015, 2016, 2017, 2018, 2019, 2020]  # IS start
    rows = []
    for is_start in starts:
        is_end = f"{is_start + 4}-12-31"
        oos_year = is_start + 5
        is_data = h1.loc[f"{is_start}-01-01":is_end]
        oos_data = h1.loc[f"{oos_year}-01-01":f"{oos_year}-12-31"]
        if len(oos_data) < 1000:
            continue

        best = fit_best(is_data, fn)
        if best is None:
            rows.append({'is_period': f"{is_start}-{is_start+4}",
                         'oos_year': oos_year, 'error': 'no IS fit'})
            continue

        # OOS test
        oos_trades = fn(oos_data, **best['params'])
        oos_pnls = trades_to_pnls(oos_trades)
        oos_cp = cost_adjust_pnls(oos_pnls, cost_R)
        rows.append({
            'is_period': f"{is_start}-{is_start+4}",
            'oos_year': oos_year,
            'fast': best['params']['fast'],
            'slow': best['params']['slow'],
            'adx': best['params']['adx_min'],
            'tp_mult': best['params']['tp_mult'],
            'is_pf': round(best['pf'], 3),
            'is_n': best['n'],
            'oos_n': len(oos_trades),
            'oos_pf': round(compute_pf(oos_pnls), 3) if len(oos_pnls) > 0 else 0,
            'oos_pf_cost': round(compute_pf(oos_cp), 3) if len(oos_cp) > 0 else 0,
            'oos_avg_r': round(compute_avg_r(oos_pnls), 4) if len(oos_pnls) > 0 else 0,
        })

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()

    # Stability metrics
    if 'oos_pf_cost' in df.columns:
        oos_pfs = df['oos_pf_cost'].dropna()
        param_drift_fast = df['fast'].nunique() if 'fast' in df else 'na'
        param_drift_slow = df['slow'].nunique() if 'slow' in df else 'na'
        n_profit_years = (oos_pfs > 1.0).sum()
        n_passing_gate = (oos_pfs > 1.3).sum()

        print(f"  Walk-forward windows: {len(oos_pfs)}")
        print(f"  Years OOS PF > 1.0:    {n_profit_years}/{len(oos_pfs)}")
        print(f"  Years OOS PF > 1.3:    {n_passing_gate}/{len(oos_pfs)}")
        print(f"  Mean OOS PF (cost):    {oos_pfs.mean():.3f}")
        print(f"  Median OOS PF (cost):  {oos_pfs.median():.3f}")
        print(f"  Min/Max OOS PF:        {oos_pfs.min():.3f} / {oos_pfs.max():.3f}")
        print(f"  Param drift (fast):    {param_drift_fast} unique values across windows")
        print(f"  Param drift (slow):    {param_drift_slow} unique values across windows")

        if n_passing_gate < len(oos_pfs) * 0.5:
            print(f"\n  VERDICT: UNSTABLE — only {n_passing_gate}/{len(oos_pfs)} years pass 1.3 gate.")
            print(f"           Strategy is window-dependent, not a true edge.")
        elif param_drift_fast > 1 or param_drift_slow > 1:
            print(f"\n  VERDICT: PARAM DRIFT — best params change across windows.")
            print(f"           Edge exists but is regime-fragile.")
        else:
            print(f"\n  VERDICT: STABLE — params + PF consistent. Edge survives walk-forward.")

    out = os.path.join(os.path.dirname(__file__), '..', 'diagnostic',
                       'walk_forward_ema_cross.csv')
    df.to_csv(out, index=False)
    print(f"\n  Saved: {out}")


if __name__ == '__main__':
    main()
