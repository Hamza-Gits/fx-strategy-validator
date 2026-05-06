"""
Phase 2 — XAUUSD Parity Oracle: Python Decision Trace Emitter
==============================================================
Per-bar decision trace CSV for XAUUSD Daily ATR Breakout strategy.
Output schema is identical to what the MQL5 EA will emit, so a diff
script can locate the first divergent bar.

Deploy config (Phase 4 OOS robust):
  ATR_mult = 1.0, Hold = 5d, Filter = W1_EMA-26
  SL = 1.5*ATR(14), TP = 3.0*ATR(14)
  BE move at +1.0R, trail by 1*ATR at +2.0R

Output: diagnostic/traces/decision_trace_python_XAUUSD_YYYY.csv
Schema:
  date, bar_time_gmt, bar_close, bar_high, bar_low,
  atr_d1, prev_high, prev_low, w1_close, w1_ema, trend_dir,
  threshold, allow_long, allow_short, signal, skip_reason,
  entry_price, sl, tp
"""
import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'validation_harness'))
from harness import load_mt5_csv

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'traces')
os.makedirs(OUT_DIR, exist_ok=True)

ATR_PERIOD     = 14
W1_EMA_PERIOD  = 26
ATR_MULT       = 1.0
SL_ATR_MULT    = 1.5
TP_ATR_MULT    = 3.0


def compute_d1(h1):
    d1 = h1.resample('1D').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    high, low, close = d1['high'], d1['low'], d1['close']
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    d1['atr'] = tr.ewm(alpha=1/ATR_PERIOD, adjust=False).mean()
    d1['prev_high'] = high.shift(1)
    d1['prev_low']  = low.shift(1)
    return d1


def compute_w1(h1):
    w1 = h1.resample('1W').agg({'close':'last'}).dropna()
    w1['ema'] = w1['close'].ewm(span=W1_EMA_PERIOD, adjust=False).mean()
    return w1


def run_python_trace(year: int, data_file: str):
    print(f"\n=== XAUUSD Phase 2 Python Trace — {year} ===")
    h1_full = load_mt5_csv(data_file)
    # Include warmup year
    h1 = h1_full.loc[f'{year-1}-01-01':f'{year}-12-31'].copy()

    d1 = compute_d1(h1)
    w1 = compute_w1(h1)

    h1['atr_d1']    = d1['atr'].reindex(h1.index, method='ffill')
    h1['prev_high'] = d1['prev_high'].reindex(h1.index, method='ffill')
    h1['prev_low']  = d1['prev_low'].reindex(h1.index, method='ffill')
    h1['w1_close']  = w1['close'].reindex(h1.index, method='ffill')
    h1['w1_ema']    = w1['ema'].reindex(h1.index, method='ffill')
    h1 = h1.dropna()

    # Trim to target year
    h1 = h1.loc[f'{year}-01-01':f'{year}-12-31']
    print(f"Loaded {len(h1)} H1 bars: {h1.index[0]} to {h1.index[-1]}")

    rows = []
    trade_taken_today = False
    current_date = None

    for ts, row in h1.iterrows():
        date = ts.date()
        if date != current_date:
            current_date = date
            trade_taken_today = False

        atr        = row['atr_d1']
        prev_high  = row['prev_high']
        prev_low   = row['prev_low']
        w1_close   = row['w1_close']
        w1_ema     = row['w1_ema']
        bar_close  = row['close']

        threshold = ATR_MULT * atr

        # W1 EMA filter
        if w1_close > w1_ema:
            trend_dir = 'LONG'
            allow_long, allow_short = True, False
        elif w1_close < w1_ema:
            trend_dir = 'SHORT'
            allow_long, allow_short = False, True
        else:
            trend_dir = 'FLAT'
            allow_long = allow_short = False

        signal = 'NONE'
        skip_reason = ''
        entry_price = ''
        sl_price = ''
        tp_price = ''

        if trade_taken_today:
            skip_reason = 'TRADE_ALREADY_TAKEN'
        elif allow_long and bar_close > prev_high + threshold:
            signal = 'LONG'
            entry = bar_close
            entry_price = round(entry, 2)
            sl_price = round(entry - SL_ATR_MULT * atr, 2)
            tp_price = round(entry + TP_ATR_MULT * atr, 2)
            trade_taken_today = True
        elif allow_short and bar_close < prev_low - threshold:
            signal = 'SHORT'
            entry = bar_close
            entry_price = round(entry, 2)
            sl_price = round(entry + SL_ATR_MULT * atr, 2)
            tp_price = round(entry - TP_ATR_MULT * atr, 2)
            trade_taken_today = True
        else:
            if not (allow_long or allow_short):
                skip_reason = 'TREND_FILTER'
            else:
                skip_reason = 'NO_BREAKOUT'

        rows.append({
            'date': str(date),
            'bar_time_gmt': str(ts),
            'bar_close': round(bar_close, 2),
            'bar_high':  round(row['high'], 2),
            'bar_low':   round(row['low'], 2),
            'atr_d1':    round(atr, 3),
            'prev_high': round(prev_high, 2),
            'prev_low':  round(prev_low, 2),
            'w1_close':  round(w1_close, 2),
            'w1_ema':    round(w1_ema, 3),
            'trend_dir': trend_dir,
            'threshold': round(threshold, 3),
            'allow_long':  'YES' if allow_long else 'NO',
            'allow_short': 'YES' if allow_short else 'NO',
            'signal':      signal,
            'skip_reason': skip_reason,
            'entry_price': entry_price,
            'sl':          sl_price,
            'tp':          tp_price,
        })

    df = pd.DataFrame(rows)
    out_path = os.path.join(OUT_DIR, f'decision_trace_python_XAUUSD_{year}.csv')
    df.to_csv(out_path, index=False)

    signals = df[df['signal'].isin(['LONG','SHORT'])]
    print(f"\nTrace written: {out_path}")
    print(f"Total bars traced: {len(df)}")
    print(f"Signal bars:       {len(signals)}")
    print(f"  LONG:  {len(signals[signals['signal']=='LONG'])}")
    print(f"  SHORT: {len(signals[signals['signal']=='SHORT'])}")
    print(f"\nSkip reason breakdown:")
    skip_df = df[df['signal']=='NONE']
    print(skip_df['skip_reason'].value_counts().to_string())
    return df


if __name__ == '__main__':
    DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                             'data', 'XAUUSD_H1_2013-2025.csv')
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    run_python_trace(year, DATA_FILE)
