"""
Phase 1 — Sanity check the 667 trade claim
==========================================
Counts EXPECTED signal days in 2023 GBPUSD H1 under v7's exact spec:
  - Asian session 00:00-07:00 GMT
  - London window 07:00-10:00 GMT
  - W1 EMA-26 trend filter (NO ambiguity zone)
  - Range filter 15-60 pips
  - Entry: first H1 bar that CLOSES above asian_high or below asian_low

Outputs CSV with day-by-day reason for each skip and entry.
This is the empirical ground truth that decides whether Python (667 trades)
or MT5 (56 trades) is closer to reality.

Council kill criterion:
  - If 2023 produces ~15-30 signal days → MT5 was right, Python is fiction → kill strategy
  - If 2023 produces ~150+ signal days → Python plausible → build parity oracle
"""

import sys, os
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'validation_harness'))
from harness import load_mt5_csv_pair


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def run_diagnostic(symbol: str, year: int, data_dir: str,
                   tp_mult: float = 1.5,
                   w1_ema_period: int = 26,
                   min_range_pips: float = 15.0,
                   max_range_pips: float = 60.0,
                   asian_start: int = 0, asian_end: int = 7,
                   london_start: int = 7, london_end: int = 10,
                   eod_exit_hour: int = 17):

    print(f"\n=== Phase 1 Diagnostic — {symbol} {year} ===")
    print(f"Spec: TP={tp_mult}x, EMA-{w1_ema_period}, Range {min_range_pips}-{max_range_pips} pips")
    print(f"Asian {asian_start:02d}-{asian_end:02d} GMT, London {london_start:02d}-{london_end:02d} GMT, EOD {eod_exit_hour:02d}\n")

    h1 = load_mt5_csv_pair(data_dir, symbol)
    h1 = h1.loc[f'{year}-01-01':f'{year}-12-31'].copy()
    print(f"Loaded {len(h1)} H1 bars from {h1.index[0]} to {h1.index[-1]}")

    is_jpy = 'JPY' in symbol.upper()
    pip = 0.01 if is_jpy else 0.0001

    # Build W1 trend filter (using full history before slicing for EMA warmup)
    h1_full = load_mt5_csv_pair(data_dir, symbol)
    w1 = h1_full.resample('1W').agg({'open':'first','high':'max','low':'min',
                                      'close':'last','volume':'sum'}).dropna()
    w1['w1_ema'] = ema(w1['close'], w1_ema_period)
    h1['w1_ema'] = w1['w1_ema'].reindex(h1.index, method='ffill')
    h1['w1_close'] = w1['close'].reindex(h1.index, method='ffill')
    h1 = h1.dropna(subset=['w1_ema'])

    h1_arr = h1.reset_index()
    h1_idx = pd.DatetimeIndex(h1_arr['time'])
    h1_arr.index = range(len(h1_arr))

    dates = sorted(set(h1_idx.date))
    print(f"Days in dataset: {len(dates)}\n")

    rows = []
    counters = {
        'total_days': 0,
        'weekend_or_holiday_skip': 0,
        'asian_bars_insufficient': 0,
        'range_too_small': 0,
        'range_too_large': 0,
        'no_london_bars': 0,
        'trend_filter_no_long_no_short': 0,
        'trend_filter_skip_no_breakout': 0,
        'signal_long': 0,
        'signal_short': 0,
        'no_breakout_in_window': 0,
    }

    for day in dates:
        counters['total_days'] += 1
        row = {'date': day, 'reason': '', 'asian_high': None, 'asian_low': None,
               'range_pips': None, 'w1_close': None, 'w1_ema': None, 'trend': '',
               'london_close_07': None, 'london_close_08': None, 'london_close_09': None,
               'signal_dir': 0, 'entry_price': None}

        asian_mask = (h1_idx.date == day) & (h1_idx.hour >= asian_start) & (h1_idx.hour < asian_end)
        asian_pos = np.where(asian_mask)[0]
        if len(asian_pos) < 3:
            row['reason'] = 'asian_bars_insufficient'
            counters['asian_bars_insufficient'] += 1
            rows.append(row); continue

        asian_high = h1_arr.loc[asian_pos, 'high'].max()
        asian_low = h1_arr.loc[asian_pos, 'low'].min()
        asian_range = asian_high - asian_low
        range_pips = asian_range / pip

        row['asian_high'] = round(asian_high, 5)
        row['asian_low'] = round(asian_low, 5)
        row['range_pips'] = round(range_pips, 1)

        if range_pips < min_range_pips:
            row['reason'] = 'range_too_small'
            counters['range_too_small'] += 1
            rows.append(row); continue
        if range_pips > max_range_pips:
            row['reason'] = 'range_too_large'
            counters['range_too_large'] += 1
            rows.append(row); continue

        london_mask = (h1_idx.date == day) & (h1_idx.hour >= london_start) & (h1_idx.hour < london_end)
        london_pos = np.where(london_mask)[0]
        if len(london_pos) == 0:
            row['reason'] = 'no_london_bars'
            counters['no_london_bars'] += 1
            rows.append(row); continue

        ref = h1_arr.loc[asian_pos[-1]]
        w1_close = ref.get('w1_close', ref['close'])
        w1_ema_v = ref.get('w1_ema', ref['close'])
        allow_long = w1_close > w1_ema_v
        allow_short = w1_close < w1_ema_v

        row['w1_close'] = round(w1_close, 5)
        row['w1_ema'] = round(w1_ema_v, 5)
        row['trend'] = 'long' if allow_long else ('short' if allow_short else 'flat')

        for lp in london_pos:
            bar = h1_arr.loc[lp]
            hr = bar['time'].hour
            if hr == 7: row['london_close_07'] = round(bar['close'], 5)
            elif hr == 8: row['london_close_08'] = round(bar['close'], 5)
            elif hr == 9: row['london_close_09'] = round(bar['close'], 5)

        if not allow_long and not allow_short:
            row['reason'] = 'trend_filter_no_long_no_short'
            counters['trend_filter_no_long_no_short'] += 1
            rows.append(row); continue

        signal_taken = False
        for lp in london_pos:
            bar = h1_arr.loc[lp]
            if allow_long and bar['close'] > asian_high:
                row['signal_dir'] = 1
                row['entry_price'] = asian_high
                row['reason'] = 'signal_long'
                counters['signal_long'] += 1
                signal_taken = True
                break
            elif allow_short and bar['close'] < asian_low:
                row['signal_dir'] = -1
                row['entry_price'] = asian_low
                row['reason'] = 'signal_short'
                counters['signal_short'] += 1
                signal_taken = True
                break

        if not signal_taken:
            row['reason'] = 'no_breakout_in_window'
            counters['no_breakout_in_window'] += 1
        rows.append(row)

    df = pd.DataFrame(rows)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             f'phase1_signal_diagnostic_{symbol}_{year}.csv')
    df.to_csv(out_path, index=False)

    total_signals = counters['signal_long'] + counters['signal_short']
    print(f"=== {symbol} {year} Diagnostic Summary ===")
    print(f"Total calendar days analysed:    {counters['total_days']}")
    print(f"  Asian bars insufficient:       {counters['asian_bars_insufficient']:>4}  (weekend/holiday)")
    print(f"  Range < {min_range_pips} pips:           {counters['range_too_small']:>4}")
    print(f"  Range > {max_range_pips} pips:           {counters['range_too_large']:>4}")
    print(f"  No London bars:                {counters['no_london_bars']:>4}")
    print(f"  Trend filter blocked both:     {counters['trend_filter_no_long_no_short']:>4}")
    print(f"  No breakout in 07-10 GMT:      {counters['no_breakout_in_window']:>4}")
    print(f"  --- SIGNAL DAYS ---")
    print(f"  Long signals:                  {counters['signal_long']:>4}")
    print(f"  Short signals:                 {counters['signal_short']:>4}")
    print(f"  TOTAL SIGNALS:                 {total_signals:>4}")
    print(f"\nDiagnostic CSV: {out_path}")

    return counters, df


if __name__ == "__main__":
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    counters, df = run_diagnostic('GBPUSD', 2023, DATA_DIR)

    total_signals = counters['signal_long'] + counters['signal_short']
    print("\n" + "=" * 60)
    print("COUNCIL VERDICT GATE:")
    print("=" * 60)
    if total_signals < 50:
        print(f"  → {total_signals} signal days. MT5's 56 was likely RIGHT.")
        print("  → Python's 667 was likely FICTION. Strategy basis collapses.")
        print("  → RECOMMENDED: kill strategy, pivot to new edge.")
    elif total_signals < 100:
        print(f"  → {total_signals} signal days. Inconclusive zone.")
        print("  → Investigate cost-realistic PF before committing more time.")
    else:
        print(f"  → {total_signals} signal days. Python 667 is plausible.")
        print("  → MT5's 56 was the bug. PROCEED to Phase 2 (parity oracle).")
    print("=" * 60)
