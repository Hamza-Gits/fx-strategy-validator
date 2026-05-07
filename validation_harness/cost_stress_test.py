"""
Cost stress test — XAUUSD ema_cross at realistic broker spreads.

Council concern: gold spread widens 3-5x at NY close (H4 boundary at 16:00 UTC).
Current cost model: 40c spread + 20c slippage = 60c flat.
Reality: 40-60c during liquid hours, 100-200c at NY close + news.

Tests cost-adjusted PF at: 60c (current), 100c (realistic), 150c (worst), 200c (news/illiquid).
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv
from strategy_lib import STRATEGIES
from strategy_battery import (compute_pf, compute_avg_r, trades_to_pnls,
                               cost_adjust_pnls, NOTIONAL_RISK)


def cost_R(spread_cents: float, stop_dist_dollars: float = 22.0,
           commission_R: float = 0.02) -> float:
    """Cost per trade in R units. spread_cents includes slippage."""
    return (spread_cents / 100.0) / stop_dist_dollars + commission_R


def main():
    DATA = os.path.join(os.path.dirname(__file__), '..', 'data',
                        'XAUUSD_H1_2013-2025.csv')
    h1 = load_mt5_csv(DATA).loc['2014-01-01':'2024-12-31']
    is_split = int(len(h1) * 0.7)
    oos = h1.iloc[is_split:]

    fn = STRATEGIES['ema_cross']['fn']
    params = {'fast': 10, 'slow': 50, 'adx_min': 20.0, 'sl_mult': 1.5, 'tp_mult': 2.0}
    trades = fn(oos, **params)
    pnls = trades_to_pnls(trades)

    print("="*72)
    print("  COST STRESS TEST — XAUUSD ema_cross OOS (2021-2024)")
    print(f"  Raw OOS: N={len(pnls)}  PF={compute_pf(pnls):.3f}  AvgR={compute_avg_r(pnls):.3f}")
    print("="*72)

    rows = []
    for spread in [60, 80, 100, 120, 150, 200]:
        # spread = total spread+slippage in cents (per round trip)
        c_R = cost_R(spread)
        cp = cost_adjust_pnls(pnls, c_R)
        rows.append({
            'spread_c': spread,
            'cost_R': round(c_R, 4),
            'pf': round(compute_pf(cp), 3),
            'avg_r': round(compute_avg_r(cp), 4),
            'pos_trades': int((cp > 0).sum()),
            'neg_trades': int((cp < 0).sum()),
            'wr_pct': round(100 * (cp > 0).mean(), 1),
        })
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()

    # Verdict
    crit = df[df['spread_c'] == 100]['pf'].iloc[0]
    if crit < 1.3:
        print(f"  VERDICT: FRAGILE — PF drops to {crit:.2f} at realistic 100c spread.")
        print(f"           Strategy can't survive normal NY-close conditions.")
    elif crit < 1.5:
        print(f"  VERDICT: MARGINAL — PF {crit:.2f} at 100c. Thin cushion above 1.3 gate.")
    else:
        print(f"  VERDICT: ROBUST — PF {crit:.2f} at 100c spread.")

    out = os.path.join(os.path.dirname(__file__), '..', 'diagnostic',
                       'cost_stress_test.csv')
    df.to_csv(out, index=False)
    print(f"\n  Saved: {out}")


if __name__ == '__main__':
    main()
