#!/usr/bin/env python3
"""
Phase 1 EURUSD: Asian Range Window Sweep
IS data only: 2013-2020. OOS (2021-2025) locked — do not touch.

Tests 4 Asian range windows to find which produces the cleanest
compression-expansion structure on EURUSD for the London Breakout.

Gates:
  - Signal count < 400 across IS period => KILL (structure doesn't exist)
  - Continuation rate < 55%             => KILL (mechanic broken)
"""
import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime, date

# Point to harness
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'validation_harness'))
from harness import load_mt5_csv

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
IS_FILE = os.path.join(DATA_DIR, 'EURUSD_H1_2013-2020.csv')

# Strategy constants (identical to GBPUSD v8)
TP_MULT       = 1.5
W1_EMA_PERIOD = 26
MIN_RANGE_PIP = 15.0
MAX_RANGE_PIP = 60.0
LONDON_START  = 7
LONDON_END    = 10
EOD_EXIT      = 17
PIP           = 0.0001

# Windows to test: (asian_start_hour, asian_end_hour, label)
WINDOWS = [
    (0, 7,  "00:00-07:00 (GBPUSD default)"),
    (3, 7,  "03:00-07:00 (tighter pre-London)"),
    (4, 7,  "04:00-07:00 (narrowest pre-London)"),
    (0, 8,  "00:00-08:00 (include Frankfurt)"),
]


def build_w1_ema(h1: pd.DataFrame) -> pd.Series:
    w1 = h1.resample('1W').agg({'open': 'first', 'high': 'max',
                                 'low': 'min', 'close': 'last'}).dropna()
    w1_ema = w1['close'].ewm(span=W1_EMA_PERIOD, adjust=False).mean()
    return w1_ema.reindex(h1.index, method='ffill')


def run_window(h1: pd.DataFrame, asian_start: int, asian_end: int) -> dict:
    h1 = h1.copy()
    h1['w1_ema'] = build_w1_ema(h1)
    h1['w1_close'] = h1['close'].resample('1W').last().reindex(h1.index, method='ffill')
    h1 = h1.dropna(subset=['w1_ema'])

    arr = h1.reset_index()
    idx = pd.DatetimeIndex(arr['time'])
    arr.index = range(len(arr))

    signals = []
    range_sizes = []
    days = sorted(set(idx.date))

    for day in days:
        asian_mask = (idx.date == day) & (idx.hour >= asian_start) & (idx.hour < asian_end)
        asian_pos  = np.where(asian_mask)[0]
        if len(asian_pos) < 2:
            continue

        a_high = arr.loc[asian_pos, 'high'].max()
        a_low  = arr.loc[asian_pos, 'low'].min()
        a_range = a_high - a_low
        range_pips = a_range / PIP

        if range_pips < MIN_RANGE_PIP or range_pips > MAX_RANGE_PIP:
            continue

        range_sizes.append(range_pips)

        london_mask = (idx.date == day) & (idx.hour >= LONDON_START) & (idx.hour < LONDON_END)
        london_pos  = np.where(london_mask)[0]
        if len(london_pos) == 0:
            continue

        ref         = arr.loc[asian_pos[-1]]
        allow_long  = ref['w1_close'] > ref['w1_ema']
        allow_short = ref['w1_close'] < ref['w1_ema']

        for lp in london_pos:
            bar = arr.loc[lp]
            direction = 0

            if allow_long and bar['close'] > a_high:
                direction   = 1
                entry_price = a_high
                sl          = a_low
                tp          = entry_price + TP_MULT * a_range
            elif allow_short and bar['close'] < a_low:
                direction   = -1
                entry_price = a_low
                sl          = a_high
                tp          = entry_price - TP_MULT * a_range
            else:
                continue

            # Scan forward for exit
            forward = np.where(idx > bar['time'])[0]
            next_day = np.where(idx.date > day)[0]
            scan_end = next_day[0] if len(next_day) > 0 else len(arr)
            scan_pos = forward[forward < scan_end]

            exit_price = None
            exit_time  = None
            # Track whether price moved in our direction on the bar after entry
            continuation = None

            for i, fp in enumerate(scan_pos):
                fbar = arr.loc[fp]
                ft   = fbar['time']

                # Continuation: next bar close vs entry price
                if i == 0:
                    move = direction * (fbar['close'] - entry_price)
                    continuation = (move > 0)

                if ft.hour >= EOD_EXIT:
                    exit_price = fbar['open']
                    exit_time  = ft
                    break

                if direction == 1:
                    if fbar['low'] <= sl:
                        exit_price = sl; exit_time = ft; break
                    if fbar['high'] >= tp:
                        exit_price = tp; exit_time = ft; break
                else:
                    if fbar['high'] >= sl:
                        exit_price = sl; exit_time = ft; break
                    if fbar['low'] <= tp:
                        exit_price = tp; exit_time = ft; break

            if exit_price is None:
                break  # no exit found — skip day

            stop_dist = abs(entry_price - sl)
            pnl_r = direction * (exit_price - entry_price) / stop_dist

            signals.append({
                'date':         day,
                'direction':    direction,
                'entry_price':  entry_price,
                'exit_price':   exit_price,
                'pnl_r':        pnl_r,
                'continuation': continuation,
                'range_pips':   range_pips,
            })
            break  # one trade per day

    if not signals:
        return {'count': 0, 'pf': 0, 'cont_rate': 0, 'range_mean': 0, 'range_std': 0}

    df_sig = pd.DataFrame(signals)
    wins   = df_sig.loc[df_sig['pnl_r'] > 0, 'pnl_r'].sum()
    losses = df_sig.loc[df_sig['pnl_r'] < 0, 'pnl_r'].abs().sum()
    pf     = wins / losses if losses > 0 else float('inf')
    cont   = df_sig['continuation'].mean() * 100

    return {
        'count':      len(df_sig),
        'pf':         pf,
        'cont_rate':  cont,
        'range_mean': np.mean(range_sizes),
        'range_std':  np.std(range_sizes),
    }


def main():
    print("Loading EURUSD IS data (2013-2020)...")
    h1 = load_mt5_csv(IS_FILE)
    print(f"  {len(h1)} bars | {h1.index[0].date()} to {h1.index[-1].date()}")
    print()

    print("=" * 72)
    print("PHASE 1: EURUSD ASIAN RANGE WINDOW SWEEP (IS: 2013-2020)")
    print("=" * 72)
    print(f"{'Window':<35} {'Signals':>7} {'PF@0':>7} {'Cont%':>7} {'RangeMean':>10} {'Verdict':>10}")
    print("-" * 72)

    results = []
    for a_start, a_end, label in WINDOWS:
        r = run_window(h1, a_start, a_end)
        sig_gate  = r['count'] >= 400
        cont_gate = r['cont_rate'] >= 55.0
        verdict = "PASS" if (sig_gate and cont_gate) else ("LOW-SIG" if not sig_gate else "LOW-CONT")
        results.append((label, r, verdict))

        print(f"{label:<35} {r['count']:>7} {r['pf']:>7.3f} {r['cont_rate']:>7.1f} "
              f"{r['range_mean']:>10.1f} {verdict:>10}")

    print("=" * 72)
    print()
    print("Gates: Signals >= 400 AND Continuation rate >= 55%")
    passing = [(l, r, v) for l, r, v in results if v == "PASS"]
    if passing:
        best = max(passing, key=lambda x: x[1]['pf'])
        print(f"BEST WINDOW: {best[0]}")
        print(f"  Signals: {best[1]['count']} | PF@0: {best[1]['pf']:.3f} | "
              f"Cont%: {best[1]['cont_rate']:.1f}% | Range: {best[1]['range_mean']:.1f} pip avg")
        print()
        print("ACTION: Use this window for Phase 2 parity oracle on EURUSD.")
    else:
        print("NO WINDOW PASSES. EURUSD London Breakout structure does not exist at these parameters.")
        print("ACTION: Kill EURUSD. Evaluate USDJPY next.")

    print("=" * 72)


if __name__ == '__main__':
    main()
