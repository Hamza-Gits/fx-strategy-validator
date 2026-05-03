#!/usr/bin/env python3
"""
Phase 3 EURUSD: Cost Sweep
IS data only: 2013-2020. OOS still locked.

Tests PF across RTT range 0.0 to 4.0 pip to find the breakeven
and confirm viability at The5ers-specific execution costs.

The5ers cost model:
  - Raw EURUSD ECN spread at 07:00 GMT: 0.8-1.2 pip
  - The5ers markup: ~0.5-1.0 pip
  - Slippage (bar-close market order): 0.3-0.5 pip
  - Total estimated RTT: 1.6-2.7 pip (half round-trip each side)
"""
import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'validation_harness'))
from harness import load_mt5_csv

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
IS_FILE  = os.path.join(DATA_DIR, 'EURUSD_H1_2013-2020.csv')

TP_MULT       = 1.5
W1_EMA_PERIOD = 26
MIN_RANGE_PIP = 15.0
MAX_RANGE_PIP = 60.0
ASIAN_START   = 0
ASIAN_END     = 7
LONDON_START  = 7
LONDON_END    = 10
EOD_EXIT      = 17
PIP           = 0.0001
NOTIONAL_RISK = 100.0


def build_w1_ema(h1):
    w1 = h1.resample('1W').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    w1_ema = w1['close'].ewm(span=W1_EMA_PERIOD, adjust=False).mean()
    h1 = h1.copy()
    h1['w1_ema']   = w1_ema.reindex(h1.index, method='ffill')
    h1['w1_close'] = w1['close'].reindex(h1.index, method='ffill')
    return h1.dropna(subset=['w1_ema'])


def collect_trades(h1):
    arr = h1.reset_index()
    idx = pd.DatetimeIndex(arr['time'])
    arr.index = range(len(arr))
    trades = []

    for day in sorted(set(idx.date)):
        a_mask = (idx.date == day) & (idx.hour >= ASIAN_START) & (idx.hour < ASIAN_END)
        a_pos  = np.where(a_mask)[0]
        if len(a_pos) < 2:
            continue

        a_high  = arr.loc[a_pos, 'high'].max()
        a_low   = arr.loc[a_pos, 'low'].min()
        a_range = a_high - a_low
        rpips   = a_range / PIP

        if rpips < MIN_RANGE_PIP or rpips > MAX_RANGE_PIP:
            continue

        l_mask = (idx.date == day) & (idx.hour >= LONDON_START) & (idx.hour < LONDON_END)
        l_pos  = np.where(l_mask)[0]
        if not len(l_pos):
            continue

        ref         = arr.loc[a_pos[-1]]
        allow_long  = ref['w1_close'] > ref['w1_ema']
        allow_short = ref['w1_close'] < ref['w1_ema']

        for lp in l_pos:
            bar = arr.loc[lp]
            direction = 0
            if allow_long and bar['close'] > a_high:
                direction = 1;  entry = a_high; sl = a_low;  tp = entry + TP_MULT * a_range
            elif allow_short and bar['close'] < a_low:
                direction = -1; entry = a_low;  sl = a_high; tp = entry - TP_MULT * a_range
            else:
                continue

            stop_dist = abs(entry - sl)
            fwd   = np.where(idx > bar['time'])[0]
            nxt   = np.where(idx.date > day)[0]
            s_end = nxt[0] if len(nxt) else len(arr)
            scan  = fwd[fwd < s_end]

            exit_p = None
            for fp in scan:
                fb = arr.loc[fp]
                if fb['time'].hour >= EOD_EXIT:
                    exit_p = fb['open']; break
                if direction == 1:
                    if fb['low'] <= sl:  exit_p = sl; break
                    if fb['high'] >= tp: exit_p = tp; break
                else:
                    if fb['high'] >= sl: exit_p = sl; break
                    if fb['low'] <= tp:  exit_p = tp; break

            if exit_p is None:
                break

            trades.append({
                'direction': direction,
                'entry':     entry,
                'exit':      exit_p,
                'stop_dist': stop_dist,
            })
            break

    return pd.DataFrame(trades)


def pf_at_rtt(df, rtt_pip):
    rtt = rtt_pip * PIP
    gross = df.apply(
        lambda r: r['direction'] * (r['exit'] - r['entry']) - rtt,
        axis=1
    )
    wins   = gross[gross > 0].sum()
    losses = gross[gross < 0].abs().sum()
    return wins / losses if losses > 0 else float('inf')


def main():
    print("Loading EURUSD IS data (2013-2020)...")
    h1 = load_mt5_csv(IS_FILE)
    h1 = build_w1_ema(h1)
    print(f"  {len(h1)} bars | {h1.index[0].date()} to {h1.index[-1].date()}")

    print("Collecting trades...")
    trades = collect_trades(h1)
    print(f"  {len(trades)} trades collected")
    print()

    rtt_levels = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

    print("=" * 65)
    print("PHASE 3: EURUSD COST SWEEP (IS: 2013-2020)")
    print("=" * 65)
    print(f"{'RTT (pip)':<12} {'PF':<10} {'Verdict'}")
    print("-" * 65)

    for rtt in rtt_levels:
        pf = pf_at_rtt(trades, rtt)
        if rtt == 0.0:
            tag = "zero cost baseline"
        elif 1.6 <= rtt <= 2.7:
            tag = "<= The5ers realistic range"
        elif rtt > 3.5:
            tag = "MetaQuotes infrastructure (dead)"
        else:
            tag = ""
        viable = "VIABLE" if pf >= 1.2 else ("MARGINAL" if pf >= 1.0 else "DEAD")
        print(f"{rtt:<12.1f} {pf:<10.3f} {viable}  {tag}")

    print("=" * 65)
    print()

    # The5ers specific estimate
    # ECN spread 0.8-1.2 pip + The5ers markup 0.5-1.0 pip + slippage 0.3-0.5 pip
    rtt_low  = (0.8 + 0.5 + 0.3)  # 1.6 pip optimistic
    rtt_high = (1.2 + 1.0 + 0.5)  # 2.7 pip conservative

    pf_low  = pf_at_rtt(trades, rtt_low)
    pf_high = pf_at_rtt(trades, rtt_high)

    print("THE5ERS EXECUTION ESTIMATE:")
    print(f"  Optimistic RTT ({rtt_low:.1f} pip): PF = {pf_low:.3f}")
    print(f"  Conservative RTT ({rtt_high:.1f} pip): PF = {pf_high:.3f}")
    print()

    if pf_high >= 1.2:
        verdict = "PASS — Edge survives The5ers execution costs. Proceed to OOS validation."
    elif pf_low >= 1.2:
        verdict = "CONDITIONAL PASS — Viable at optimistic cost. Verify actual The5ers spread before committing."
    else:
        verdict = "FAIL — Edge does not survive The5ers costs. Kill EURUSD."

    print(f"VERDICT: {verdict}")
    print("=" * 65)


if __name__ == '__main__':
    main()
