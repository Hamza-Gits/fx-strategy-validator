"""
Phase 2 — Parity Oracle: Python Decision Trace Emitter
=======================================================
Emits a per-bar decision trace CSV with identical schema to the MQL5 trace.
Run this on GBPUSD 2023 (or any year), then diff against MQL5's trace to find
the exact bar where Python and MT5 diverge.

Output: diagnostic/traces/decision_trace_python_GBPUSD_YYYY.csv
Schema (matches MQL5 trace):
  date, bar_time_gmt, bar_close, w1_ema, trend_dir,
  asian_high, asian_low, asian_range_pips, range_ok,
  in_london, signal, skip_reason, entry_price, sl, tp
"""

import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'validation_harness'))
from harness import load_mt5_csv_pair

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'traces')
os.makedirs(OUT_DIR, exist_ok=True)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def run_python_trace(symbol: str, year: int, data_dir: str,
                     tp_mult: float = 1.5,
                     w1_ema_period: int = 26,
                     min_range_pips: float = 15.0,
                     max_range_pips: float = 60.0,
                     asian_start: int = 0, asian_end: int = 7,
                     london_start: int = 7, london_end: int = 10,
                     eod_exit_hour: int = 17):

    print(f"\n=== Phase 2 Python Trace — {symbol} {year} ===")
    is_jpy = 'JPY' in symbol.upper()
    pip = 0.01 if is_jpy else 0.0001

    h1 = load_mt5_csv_pair(data_dir, symbol)
    # Include prior year for EMA warmup
    h1 = h1.loc[f'{year-1}-01-01':f'{year}-12-31'].copy()

    # Build W1 EMA
    w1 = h1.resample('1W').agg({'open': 'first', 'high': 'max',
                                 'low': 'min', 'close': 'last',
                                 'volume': 'sum'}).dropna()
    w1['w1_ema'] = ema(w1['close'], w1_ema_period)
    h1['w1_ema'] = w1['w1_ema'].reindex(h1.index, method='ffill')
    h1 = h1.dropna(subset=['w1_ema'])

    # Trim to target year
    h1 = h1.loc[f'{year}-01-01':f'{year}-12-31']
    print(f"Loaded {len(h1)} H1 bars: {h1.index[0]} to {h1.index[-1]}")

    h1_arr = h1.reset_index()
    h1_arr.rename(columns={'time': 'bar_time'}, inplace=True)

    rows = []
    trade_taken_today = False
    current_date = None
    asian_high = None
    asian_low = None
    asian_range_ok = False
    active_trade = None  # dict with entry_price, sl, tp, direction

    for i, row in h1_arr.iterrows():
        bar_time = row['bar_time']
        # Treat index as GMT (MT5 CSV data is GMT)
        if hasattr(bar_time, 'hour'):
            hour = bar_time.hour
        else:
            hour = pd.Timestamp(bar_time).hour

        date = bar_time.date() if hasattr(bar_time, 'date') else pd.Timestamp(bar_time).date()

        # New day reset
        if date != current_date:
            current_date = date
            trade_taken_today = False
            asian_high = None
            asian_low = None
            asian_range_ok = False
            asian_bars = []

        # Close active trade at EOD
        if active_trade is not None and hour >= eod_exit_hour:
            active_trade = None

        # --- Asian session: collect range ---
        if asian_start <= hour < asian_end:
            if asian_high is None:
                asian_high = row['high']
                asian_low = row['low']
            else:
                asian_high = max(asian_high, row['high'])
                asian_low = min(asian_low, row['low'])
            # Record as asian bar (not a signal bar)
            rows.append({
                'date': str(date),
                'bar_time_gmt': str(bar_time),
                'bar_close': round(row['close'], 5),
                'w1_ema': round(row['w1_ema'], 5),
                'trend_dir': '',
                'asian_high': '',
                'asian_low': '',
                'asian_range_pips': '',
                'range_ok': '',
                'in_london': 'NO',
                'signal': 'NONE',
                'skip_reason': 'ASIAN_SESSION',
                'entry_price': '',
                'sl': '',
                'tp': '',
            })
            continue

        # --- After Asian session: compute range quality ---
        if asian_high is None:
            rows.append({
                'date': str(date), 'bar_time_gmt': str(bar_time),
                'bar_close': round(row['close'], 5),
                'w1_ema': round(row['w1_ema'], 5),
                'trend_dir': '', 'asian_high': '', 'asian_low': '',
                'asian_range_pips': '', 'range_ok': '',
                'in_london': 'NO', 'signal': 'NONE',
                'skip_reason': 'NO_ASIAN_DATA', 'entry_price': '', 'sl': '', 'tp': '',
            })
            continue

        asian_range_pips = (asian_high - asian_low) / pip
        range_ok = min_range_pips <= asian_range_pips <= max_range_pips

        # Trend direction (W1 EMA-26, no ambiguity zone)
        w1_close_this_week = w1['close'].reindex([bar_time], method='ffill')
        if len(w1_close_this_week) > 0 and not pd.isna(w1_close_this_week.iloc[0]):
            w1_close = w1_close_this_week.iloc[0]
        else:
            w1_close = row['close']

        w1_ema_val = row['w1_ema']
        if w1_close > w1_ema_val:
            trend_dir = 'LONG'
        elif w1_close < w1_ema_val:
            trend_dir = 'SHORT'
        else:
            trend_dir = 'FLAT'

        in_london = london_start <= hour < london_end
        signal = 'NONE'
        skip_reason = ''
        entry_price = ''
        sl_price = ''
        tp_price = ''

        if not range_ok:
            skip_reason = f'RANGE_FILTER_{asian_range_pips:.1f}pips'
        elif not in_london:
            skip_reason = 'OUTSIDE_LONDON'
        elif trade_taken_today:
            skip_reason = 'TRADE_ALREADY_TAKEN'
        elif active_trade is not None:
            skip_reason = 'TRADE_ACTIVE'
        else:
            bar_close = row['close']
            # Long signal: bar close above asian_high AND trend allows
            if bar_close > asian_high and trend_dir != 'SHORT':
                signal = 'LONG'
                entry = asian_high
                sl_dist = asian_high - asian_low
                entry_price = round(entry, 5)
                sl_price = round(entry - sl_dist, 5)
                tp_price = round(entry + sl_dist * tp_mult, 5)
                trade_taken_today = True
                active_trade = {'dir': 'LONG', 'entry': entry,
                                'sl': sl_price, 'tp': tp_price}
            # Short signal: bar close below asian_low AND trend allows
            elif bar_close < asian_low and trend_dir != 'LONG':
                signal = 'SHORT'
                entry = asian_low
                sl_dist = asian_high - asian_low
                entry_price = round(entry, 5)
                sl_price = round(entry + sl_dist, 5)
                tp_price = round(entry - sl_dist * tp_mult, 5)
                trade_taken_today = True
                active_trade = {'dir': 'SHORT', 'entry': entry,
                                'sl': sl_price, 'tp': tp_price}
            else:
                skip_reason = 'NO_BREAKOUT'

        rows.append({
            'date': str(date),
            'bar_time_gmt': str(bar_time),
            'bar_close': round(row['close'], 5),
            'w1_ema': round(row['w1_ema'], 5),
            'trend_dir': trend_dir,
            'asian_high': round(asian_high, 5),
            'asian_low': round(asian_low, 5),
            'asian_range_pips': round(asian_range_pips, 1),
            'range_ok': 'YES' if range_ok else 'NO',
            'in_london': 'YES' if in_london else 'NO',
            'signal': signal,
            'skip_reason': skip_reason,
            'entry_price': entry_price,
            'sl': sl_price,
            'tp': tp_price,
        })

    df = pd.DataFrame(rows)
    out_path = os.path.join(OUT_DIR, f'decision_trace_python_{symbol}_{year}.csv')
    df.to_csv(out_path, index=False)

    signals = df[df['signal'].isin(['LONG', 'SHORT'])]
    print(f"\nTrace written: {out_path}")
    print(f"Total bars traced:  {len(df)}")
    print(f"Signal bars:        {len(signals)}")
    print(f"  LONG:  {len(signals[signals['signal']=='LONG'])}")
    print(f"  SHORT: {len(signals[signals['signal']=='SHORT'])}")
    print(f"\nSkip reason breakdown:")
    skip_df = df[df['signal']=='NONE']
    print(skip_df['skip_reason'].value_counts().to_string())
    return df


if __name__ == '__main__':
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'GBPUSD'
    year   = int(sys.argv[2]) if len(sys.argv) > 2 else 2023
    run_python_trace(symbol, year, DATA_DIR)
