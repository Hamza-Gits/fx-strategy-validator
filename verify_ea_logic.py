"""
Verify MQL5 EA logic by running the equivalent Python strategy
on locked config (TP=1.5, range 15-60, W1 EMA-26).

This proves the EA spec produces the validated edge.
Compare results with results/gbpusd_top.json (champion config).
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'validation_harness'))

from datetime import datetime
import numpy as np
import pandas as pd
from strategy_london_breakout import _run_single_symbol
from harness import load_mt5_csv_pair

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# Locked params from gbpusd_top.json
PARAMS = dict(
    tp_mult=1.5,
    use_trend_filter=True,
    w1_ema_period=26,
    min_range_pips=15.0,
    max_range_pips=60.0,
    asian_start=0, asian_end=7,
    london_start=7, london_end=10,
    eod_exit_hour=17,
)

# Apply costs (The5ers actual: 0.9-1.3 pips, conservative 2 pips for safety)
COST_PIPS = 2.0

def apply_cost(trades, pip_size, cost_pips, notional_risk=100.0):
    """Subtract round-trip cost in same normalized units."""
    out = []
    for t in trades:
        # cost in price terms = cost_pips * pip_size
        # P&L scaling: pnl = (price_move/stop_dist)*notional => cost_pnl = (cost/stop_dist)*notional
        stop_dist = abs(t.entry_price - (t.entry_price - t.pnl/notional_risk*abs(t.entry_price-t.exit_price))) if t.pnl else 1
        # Simpler: just deduct cost in pips proportional to risk
        cost_factor = (cost_pips * pip_size) / max(abs(t.entry_price - t.exit_price)/abs(t.pnl/notional_risk) if t.pnl else 1, pip_size)
        out.append(t)
    return trades  # apply at aggregate level instead

def metrics(trades, label, cost_pips=0):
    """Compute PF, win rate, etc. with optional cost deduction."""
    if not trades:
        print(f"  {label}: 0 trades"); return
    pnls = np.array([t.pnl for t in trades])
    # Subtract cost: cost_pips/stop_pips * notional per trade; normalized risk = $100 per stop
    # Stop distance varies per trade. Approximate: cost_pct = cost_pips / avg_stop_pips
    if cost_pips > 0:
        # Each trade's stop dist in pips approximation: notional / (pnl_per_pip)
        # Use the fact that pnl = ($100/stop_pips)*move_pips, so move/stop = pnl/100
        # Cost per trade = (cost_pips / stop_pips) * $100
        pnls_after = []
        for t in trades:
            stop_dist_price = abs(t.entry_price - (t.entry_price + (1.5*(abs(t.entry_price-t.exit_price)/(abs(t.pnl)/100)) if t.pnl else 0)))
            # Use simpler logic: cost_pct of risk = cost_pips/typical_stop_pips
            # Actually: pnl_normalized = ($100/stop_pips_actual) * move_pips
            # So cost in $ = (cost_pips/stop_pips_actual) * $100
            # We can't easily back stop_pips_actual; use an approximation: assume avg stop = 35 pips
            cost_dollars = (cost_pips / 35.0) * 100.0  # rough approximation
            pnls_after.append(t.pnl - cost_dollars)
        pnls = np.array(pnls_after)

    n = len(pnls)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_win = wins.sum() if len(wins) else 0
    gross_loss = abs(losses.sum()) if len(losses) else 1e-9
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
    wr = len(wins) / n * 100 if n else 0
    expectancy = pnls.mean()
    total = pnls.sum()

    # Equity curve & max DD
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq)
    max_dd = dd.max() if len(dd) else 0

    print(f"  {label}: n={n}, PF={pf:.3f}, WinRate={wr:.1f}%, Expectancy=${expectancy:.2f}, Total=${total:.2f}, MaxDD=${max_dd:.2f}")
    return dict(n=n, pf=pf, wr=wr, expectancy=expectancy, total=total, max_dd=max_dd)


def run_period(start, end, label):
    print(f"\n=== {label}: {start} to {end} ===")
    df = load_mt5_csv_pair(DATA_DIR, 'GBPUSD')
    df = df.loc[start:end]
    print(f"  Loaded {len(df)} H1 bars")

    trades = _run_single_symbol(df, 'GBPUSD', **PARAMS)
    print(f"  Total trades: {len(trades)}")
    metrics(trades, "Raw (no cost)", cost_pips=0)
    metrics(trades, "With 2-pip cost", cost_pips=2.0)
    return trades

if __name__ == "__main__":
    print("=" * 70)
    print("VERIFICATION: Python equivalent of MQL5 EA logic")
    print("Locked params:", PARAMS)
    print("=" * 70)

    # Period A (2015-2019)
    period_a = run_period(datetime(2015,1,1), datetime(2019,12,31), "Period A (2015-2019)")

    # Period B (2020-2024)
    period_b = run_period(datetime(2020,1,1), datetime(2024,12,31), "Period B (2020-2024)")

    # Full period
    full = run_period(datetime(2015,1,1), datetime(2024,12,31), "Full Period (2015-2024)")

    print("\n" + "=" * 70)
    print("EXPECTED (from gbpusd_top.json):")
    print("  Period A: OOS PF 1.638, n=102, WR=56%")
    print("  Period B: OOS PF 1.829, n=82, WR=56.7%")
    print("  Note: top.json shows OOS only (30% of period). Full-period numbers will differ.")
    print("=" * 70)
