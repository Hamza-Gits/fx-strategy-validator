#!/usr/bin/env python3
"""
Phase 4 EURUSD: OOS Validation
Untouched holdout: 2021-2025. First and only look.

Tests the locked-in strategy parameters from Phase 1-3 against
the OOS period to confirm edge survives outside training data.

Strategy (frozen):
  - Asian range: 00:00-07:00 GMT
  - London entry: 07:00-10:00 GMT
  - W1 EMA-26 trend filter
  - Range filter: 15-60 pip
  - TP: 1.5x range
  - SL: opposite range boundary
  - EOD exit: 17:00 GMT
"""
import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'validation_harness'))
from harness import load_mt5_csv

DATA_DIR  = os.path.join(os.path.dirname(__file__), '..', 'data')
OOS_FILE  = os.path.join(DATA_DIR, 'EURUSD_H1_2021-2025.csv')

# FROZEN PARAMETERS — do not adjust based on OOS results
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
            exit_t = None
            for fp in scan:
                fb = arr.loc[fp]
                if fb['time'].hour >= EOD_EXIT:
                    exit_p = fb['open']; exit_t = fb['time']; break
                if direction == 1:
                    if fb['low'] <= sl:  exit_p = sl; exit_t = fb['time']; break
                    if fb['high'] >= tp: exit_p = tp; exit_t = fb['time']; break
                else:
                    if fb['high'] >= sl: exit_p = sl; exit_t = fb['time']; break
                    if fb['low'] <= tp:  exit_p = tp; exit_t = fb['time']; break

            if exit_p is None:
                break

            r_pnl = direction * (exit_p - entry) / stop_dist
            trades.append({
                'date':       day,
                'direction':  direction,
                'entry':      entry,
                'exit':       exit_p,
                'stop_dist':  stop_dist,
                'r_pnl':      r_pnl,
            })
            break

    return pd.DataFrame(trades)


def stats_at_rtt(df, rtt_pip, risk_pct=0.75, starting_balance=10000):
    rtt = rtt_pip * PIP
    df = df.copy()
    df['gross_price_move'] = df.apply(lambda r: r['direction'] * (r['exit'] - r['entry']), axis=1)
    df['net_price_move']   = df['gross_price_move'] - rtt
    df['r_net']            = df['net_price_move'] / df['stop_dist']

    # PF
    wins  = df.loc[df['r_net'] > 0, 'r_net'].sum()
    losses = df.loc[df['r_net'] < 0, 'r_net'].abs().sum()
    pf = wins / losses if losses > 0 else float('inf')

    # Win rate
    wr = (df['r_net'] > 0).mean() * 100

    # Equity curve & DD (compounding at risk_pct)
    eq = [starting_balance]
    for r in df['r_net']:
        eq.append(eq[-1] * (1 + r * (risk_pct / 100)))
    eq = pd.Series(eq)
    peak = eq.cummax()
    dd = ((peak - eq) / peak * 100).max()

    total_return = (eq.iloc[-1] / starting_balance - 1) * 100

    # Annualized stats
    years = (df['date'].max() - df['date'].min()).days / 365.25
    cagr = ((eq.iloc[-1] / starting_balance) ** (1 / years) - 1) * 100 if years > 0 else 0

    return {
        'trades':       len(df),
        'pf':           pf,
        'wr':           wr,
        'total_ret':    total_return,
        'cagr':         cagr,
        'max_dd':       dd,
        'years':        years,
    }


def main():
    print("=" * 70)
    print("PHASE 4: EURUSD OOS VALIDATION (LOCKED HOLDOUT 2021-2025)")
    print("=" * 70)
    print()
    print("Loading EURUSD OOS data...")
    h1 = load_mt5_csv(OOS_FILE)
    h1 = build_w1_ema(h1)
    print(f"  {len(h1)} bars | {h1.index[0].date()} to {h1.index[-1].date()}")

    print("Collecting trades with FROZEN parameters...")
    trades = collect_trades(h1)
    print(f"  {len(trades)} OOS trades collected")
    print()

    # Headline at zero cost
    s0 = stats_at_rtt(trades, 0.0)
    print(f"OOS Period: {trades['date'].min()} to {trades['date'].max()} ({s0['years']:.1f} years)")
    print()

    print("=" * 70)
    print(f"{'RTT (pip)':<12} {'Trades':<8} {'PF':<7} {'WR%':<7} "
          f"{'TotalRet%':<11} {'CAGR%':<8} {'MaxDD%':<8} {'Verdict'}")
    print("-" * 70)

    rtt_levels = [0.0, 1.0, 1.6, 2.0, 2.5, 2.7, 3.0]

    for rtt in rtt_levels:
        s = stats_at_rtt(trades, rtt)
        # The5ers gates: 8% monthly target ~96% annual, 10% max DD
        the5ers_dd_ok = s['max_dd'] <= 10.0
        edge_ok       = s['pf'] >= 1.2

        if edge_ok and the5ers_dd_ok:
            verdict = "PASS"
        elif edge_ok and not the5ers_dd_ok:
            verdict = "DD-FAIL"
        elif not edge_ok and the5ers_dd_ok:
            verdict = "EDGE-FAIL"
        else:
            verdict = "FAIL"

        tag = ""
        if 1.6 <= rtt <= 2.7:
            tag = " <-- The5ers range"

        print(f"{rtt:<12.1f} {s['trades']:<8} {s['pf']:<7.3f} {s['wr']:<7.1f} "
              f"{s['total_ret']:<11.1f} {s['cagr']:<8.1f} {s['max_dd']:<8.2f} {verdict}{tag}")

    print("=" * 70)
    print()

    # The5ers headline numbers
    s_realistic = stats_at_rtt(trades, 2.2)
    print("THE5ERS REALISTIC CASE (RTT 2.2 pip):")
    print(f"  Trades:        {s_realistic['trades']}")
    print(f"  PF:            {s_realistic['pf']:.3f}")
    print(f"  Win rate:      {s_realistic['wr']:.1f}%")
    print(f"  Total return:  {s_realistic['total_ret']:.1f}% over {s_realistic['years']:.1f} years")
    print(f"  CAGR:          {s_realistic['cagr']:.1f}%")
    print(f"  Max DD:        {s_realistic['max_dd']:.2f}%")
    print()

    # Compare IS vs OOS
    print("IS vs OOS comparison:")
    print(f"  IS  (2013-2020) PF @ 0 cost: 1.584")
    print(f"  OOS (2021-2025) PF @ 0 cost: {s0['pf']:.3f}")
    deg = (1.584 - s0['pf']) / 1.584 * 100
    print(f"  Degradation: {deg:.1f}% (gate: <30%)")
    print("=" * 70)


if __name__ == '__main__':
    main()
