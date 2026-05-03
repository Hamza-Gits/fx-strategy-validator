"""
Phase 3 — Honest Cost Re-validation (FAST)
==========================================
Run backtest ONCE, then apply different cost levels mathematically to the trade list.
For symmetric cost (entry+exit), each trade pays 2*cost_pips, so:
   adjusted_pnl_pips = raw_pnl_pips - 2*cost_pips

This is mathematically equivalent to per-trade slippage on entry+exit.
"""

import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'validation_harness'))
from harness import load_mt5_csv_pair


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def run_zero_cost_backtest(h1: pd.DataFrame, symbol: str,
                            tp_mult: float = 1.5, w1_ema_period: int = 26,
                            min_range_pips: float = 15.0, max_range_pips: float = 60.0,
                            asian_start: int = 0, asian_end: int = 7,
                            london_start: int = 7, london_end: int = 10,
                            eod_exit_hour: int = 17):
    is_jpy = 'JPY' in symbol.upper()
    pip = 0.01 if is_jpy else 0.0001

    h1 = h1.copy()
    w1 = h1.resample('1W').agg({'open':'first','high':'max','low':'min',
                                 'close':'last','volume':'sum'}).dropna()
    w1['w1_ema'] = ema(w1['close'], w1_ema_period)
    h1['w1_ema'] = w1['w1_ema'].reindex(h1.index, method='ffill')
    h1['w1_close'] = w1['close'].reindex(h1.index, method='ffill')
    h1 = h1.dropna(subset=['w1_ema'])

    h1_arr = h1.reset_index()
    h1_idx = pd.DatetimeIndex(h1_arr['time'])
    h1_arr.index = range(len(h1_arr))

    trades = []
    dates = sorted(set(h1_idx.date))

    for day in dates:
        asian_mask = (h1_idx.date == day) & (h1_idx.hour >= asian_start) & (h1_idx.hour < asian_end)
        asian_pos = np.where(asian_mask)[0]
        if len(asian_pos) < 3: continue
        asian_high = h1_arr.loc[asian_pos, 'high'].max()
        asian_low = h1_arr.loc[asian_pos, 'low'].min()
        asian_range = asian_high - asian_low
        range_pips = asian_range / pip
        if range_pips < min_range_pips or range_pips > max_range_pips: continue

        london_mask = (h1_idx.date == day) & (h1_idx.hour >= london_start) & (h1_idx.hour < london_end)
        london_pos = np.where(london_mask)[0]
        if len(london_pos) == 0: continue

        ref = h1_arr.loc[asian_pos[-1]]
        allow_long = ref['w1_close'] > ref['w1_ema']
        allow_short = ref['w1_close'] < ref['w1_ema']

        traded = False
        for lp in london_pos:
            if traded: break
            bar = h1_arr.loc[lp]
            if allow_long and bar['close'] > asian_high:
                direction, entry, sl, tp = 1, asian_high, asian_low, asian_high + tp_mult*asian_range
            elif allow_short and bar['close'] < asian_low:
                direction, entry, sl, tp = -1, asian_low, asian_high, asian_low - tp_mult*asian_range
            else: continue

            forward_mask = h1_idx > bar['time']
            next_day_mask = h1_idx.date > day
            scan_until = np.where(next_day_mask)[0]
            scan_end = scan_until[0] if len(scan_until) > 0 else len(h1_arr)
            scan_pos = np.where(forward_mask)[0]; scan_pos = scan_pos[scan_pos < scan_end]

            exit_p = None
            for fp in scan_pos:
                fb = h1_arr.loc[fp]; ft = fb['time']
                if ft.hour >= eod_exit_hour:
                    exit_p = fb['open']; break
                if direction == 1:
                    if fb['low'] <= sl: exit_p = sl; break
                    if fb['high'] >= tp: exit_p = tp; break
                else:
                    if fb['high'] >= sl: exit_p = sl; break
                    if fb['low'] <= tp: exit_p = tp; break

            if exit_p is None: continue
            pnl_pips = direction * (exit_p - entry) / pip
            trades.append({'entry_time': bar['time'], 'direction': direction, 'pnl_pips': pnl_pips})
            traded = True

    return pd.DataFrame(trades)


def evaluate_with_cost(trades_df: pd.DataFrame, cost_pips: float):
    if len(trades_df) == 0: return 0, 0.0, 0.0, 0.0, 0.0
    adjusted = trades_df['pnl_pips'] - 2 * cost_pips  # entry + exit cost
    n = len(adjusted)
    wins = adjusted[adjusted > 0]
    losses = adjusted[adjusted <= 0]
    gp = wins.sum(); gl = abs(losses.sum())
    pf = gp / gl if gl > 0 else float('inf')
    wr = len(wins) / n
    return n, wr, gp, gl, pf


if __name__ == "__main__":
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    print("=" * 75)
    print("Phase 3 — Honest Cost Sweep on GBPUSD 2015-2024")
    print("=" * 75)

    h1 = load_mt5_csv_pair(DATA_DIR, 'GBPUSD').loc['2015-01-01':'2024-12-31']
    print(f"Loaded {len(h1)} H1 bars")
    print("Running base backtest (one pass)...")
    trades = run_zero_cost_backtest(h1, 'GBPUSD')
    print(f"Generated {len(trades)} raw trades\n")

    cost_levels = [0.0, 1.0, 1.5, 2.5, 4.0, 5.5, 7.0, 10.0]
    print(f"{'Cost (RTT pips)':<18}{'N':<8}{'Win%':<8}{'GP':<10}{'GL':<10}{'PF':<10}{'Verdict'}")
    print("-" * 75)

    rows = []
    for c in cost_levels:
        n, wr, gp, gl, pf = evaluate_with_cost(trades, c)
        if pf >= 1.5: v = "STRONG"
        elif pf >= 1.3: v = "OK"
        elif pf >= 1.1: v = "MARGINAL"
        elif pf >= 1.0: v = "BREAKEVEN"
        else: v = "LOSING"
        print(f"{c:<18.1f}{n:<8}{wr*100:<8.1f}{gp:<10.0f}{gl:<10.0f}{pf:<10.3f}{v}")
        rows.append({'cost_pips': c, 'n_trades': n, 'win_rate': wr,
                    'gross_profit': gp, 'gross_loss': gl, 'pf': pf})

    out_df = pd.DataFrame(rows)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'phase3_cost_sweep_GBPUSD_2015_2024.csv')
    out_df.to_csv(out_path, index=False)
    print(f"\nSweep CSV: {out_path}\n")

    pf_5_5 = out_df[out_df['cost_pips'] == 5.5]['pf'].iloc[0]
    pf_1_0 = out_df[out_df['cost_pips'] == 1.0]['pf'].iloc[0]
    breakeven = next((r['cost_pips'] for r in rows if r['pf'] < 1.0), None)

    print("=" * 75)
    print("COUNCIL GATE")
    print("=" * 75)
    print(f"  PF at 1.0 pip RTT (validation):    {pf_1_0:.3f}")
    print(f"  PF at 5.5 pip RTT (realistic):     {pf_5_5:.3f}")
    if breakeven: print(f"  Breakeven cost (PF→1.0):           ~{breakeven} pips RTT")
    else: print(f"  Edge survives even at 10 pips RTT")
    print()
    if pf_5_5 >= 1.3:
        print(f"  ✅ PASS — Edge survives honest costs (PF {pf_5_5:.2f} ≥ 1.3)")
        print(f"     → PROCEED to Phase 2 (parity oracle + v8)")
    elif pf_5_5 >= 1.0:
        print(f"  ⚠️  MARGINAL — Edge breaks even (PF {pf_5_5:.2f})")
        print(f"     → Try limit-on-retest entries to reduce cost")
    else:
        print(f"  ❌ FAIL — Edge collapses (PF {pf_5_5:.2f} < 1.0). Cost-illusion.")
    print("=" * 75)
