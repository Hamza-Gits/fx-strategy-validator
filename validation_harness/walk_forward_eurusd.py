"""
Walk-forward analysis for ema_cross_adx on EURUSD.
Council recommendation: pivot to EURUSD if XAUUSD walk-forward fails.
Same methodology as walk_forward_ema_cross.py but on EURUSD.
"""
import os
import sys
import itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv_pair
from strategy_lib import STRATEGIES
from strategy_battery import (compute_pf, compute_avg_r, trades_to_pnls,
                               cost_adjust_pnls, cost_per_trade_R)


GRID = {
    'fast':    [10, 20],
    'slow':    [50, 100],
    'adx_min': [20.0, 25.0],
    'sl_mult': [1.5],
    'tp_mult': [2.0, 3.0],
}


def fit_best(h1_window, fn, min_n=20):
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
    DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
    fn = STRATEGIES['ema_cross']['fn']
    cost_R = cost_per_trade_R()

    for symbol in ['EURUSD', 'GBPUSD']:
        print("="*78)
        print(f"  WALK-FORWARD — ema_cross_adx ({symbol})")
        print(f"  IS=4yr, OOS=1yr, rolling annually")
        print("="*78)

        h1 = load_mt5_csv_pair(DATA_DIR, symbol).loc['2014-01-01':'2024-12-31']
        starts = [2014, 2015, 2016, 2017, 2018, 2019, 2020]
        rows = []
        for is_start in starts:
            is_data = h1.loc[f"{is_start}-01-01":f"{is_start + 4}-12-31"]
            oos_year = is_start + 5
            oos_data = h1.loc[f"{oos_year}-01-01":f"{oos_year}-12-31"]
            if len(oos_data) < 1000:
                continue
            best = fit_best(is_data, fn)
            if best is None:
                continue
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
            })
        df = pd.DataFrame(rows)
        print(df.to_string(index=False))

        if 'oos_pf_cost' in df.columns:
            oos_pfs = df['oos_pf_cost'].dropna()
            n_pass = (oos_pfs > 1.3).sum()
            n_profit = (oos_pfs > 1.0).sum()
            print(f"\n  Years > 1.0: {n_profit}/{len(oos_pfs)}")
            print(f"  Years > 1.3: {n_pass}/{len(oos_pfs)}")
            print(f"  Mean PF:     {oos_pfs.mean():.3f}")
            print(f"  Median PF:   {oos_pfs.median():.3f}\n")

        out = os.path.join(os.path.dirname(__file__), '..', 'diagnostic',
                           f'walk_forward_{symbol.lower()}.csv')
        df.to_csv(out, index=False)
        print(f"  Saved: {out}\n")


if __name__ == '__main__':
    main()
