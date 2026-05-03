"""
Phase 2 — Parity Oracle: Diff Python vs MQL5 Decision Traces
=============================================================
Compares decision_trace_python_SYMBOL_YEAR.csv vs
            decision_trace_mql5_SYMBOL_YEAR.csv

Both files must have identical schema:
  date, bar_time_gmt, bar_close, w1_ema, trend_dir,
  asian_high, asian_low, asian_range_pips, range_ok,
  in_london, signal, skip_reason, entry_price, sl, tp

Usage:
  python phase2_parity_diff.py [SYMBOL] [YEAR]
  python phase2_parity_diff.py GBPUSD 2023

Output:
  - Console summary: first divergence + counts
  - diagnostic/traces/parity_diff_SYMBOL_YEAR.csv (all divergent rows)
"""

import sys, os
import pandas as pd
import numpy as np

TRACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'traces')

# Columns to compare (skip asian-session-only rows for cleaner diff)
CMP_COLS = ['signal', 'trend_dir', 'range_ok', 'in_london', 'skip_reason']
NUM_COLS = ['bar_close', 'w1_ema', 'asian_high', 'asian_low', 'asian_range_pips',
            'entry_price', 'sl', 'tp']
NUM_TOL  = 0.00002  # 0.2 pip tolerance for float comparison


def load_trace(path: str, label: str) -> pd.DataFrame:
    if not os.path.exists(path):
        print(f"ERROR: {label} trace not found at:\n  {path}")
        sys.exit(1)
    df = pd.read_csv(path, dtype=str)
    df['bar_time_gmt'] = pd.to_datetime(df['bar_time_gmt'])
    df = df.set_index('bar_time_gmt').sort_index()
    print(f"Loaded {len(df)} rows from {label}")
    return df


def diff_traces(py_df: pd.DataFrame, mql_df: pd.DataFrame,
                symbol: str, year: int) -> pd.DataFrame:

    # Align on common timestamps only
    common_idx = py_df.index.intersection(mql_df.index)
    missing_py  = len(mql_df.index.difference(py_df.index))
    missing_mql = len(py_df.index.difference(mql_df.index))

    print(f"\nTimestamp alignment:")
    print(f"  Common bars:         {len(common_idx)}")
    print(f"  In MQL5 not Python:  {missing_py}  (MQL5 processed extra bars)")
    print(f"  In Python not MQL5:  {missing_mql}  (Python processed extra bars)")

    py  = py_df.loc[common_idx]
    mql = mql_df.loc[common_idx]

    # Focus on London + post-Asian bars (skip pure asian session rows)
    london_mask = py['in_london'].isin(['YES', 'NO']) & ~py['skip_reason'].eq('ASIAN_SESSION')
    py  = py[london_mask]
    mql = mql[london_mask]

    print(f"  London+Post-Asian bars: {len(py)}")

    diff_rows = []

    for ts in py.index:
        py_row  = py.loc[ts]
        mql_row = mql.loc[ts]

        divergences = []

        # Compare categorical columns
        for col in CMP_COLS:
            pv = str(py_row.get(col, '')).strip()
            mv = str(mql_row.get(col, '')).strip()
            if pv != mv:
                divergences.append(f"{col}: PY={pv!r} vs MQL={mv!r}")

        # Compare numeric columns with tolerance
        for col in NUM_COLS:
            pv_s = str(py_row.get(col, '')).strip()
            mv_s = str(mql_row.get(col, '')).strip()
            if pv_s == '' and mv_s == '':
                continue
            try:
                pv = float(pv_s) if pv_s else np.nan
                mv = float(mv_s) if mv_s else np.nan
                if abs(pv - mv) > NUM_TOL:
                    divergences.append(f"{col}: PY={pv:.5f} vs MQL={mv:.5f}")
            except ValueError:
                if pv_s != mv_s:
                    divergences.append(f"{col}: PY={pv_s!r} vs MQL={mv_s!r}")

        if divergences:
            diff_rows.append({
                'bar_time_gmt': ts,
                'date': str(ts.date()),
                'py_signal':  py_row.get('signal', ''),
                'mql_signal': mql_row.get('signal', ''),
                'py_skip':    py_row.get('skip_reason', ''),
                'mql_skip':   mql_row.get('skip_reason', ''),
                'py_trend':   py_row.get('trend_dir', ''),
                'mql_trend':  mql_row.get('trend_dir', ''),
                'py_range_ok':  py_row.get('range_ok', ''),
                'mql_range_ok': mql_row.get('range_ok', ''),
                'divergences': ' | '.join(divergences),
            })

    diff_df = pd.DataFrame(diff_rows)

    # Summary
    py_signals  = (py['signal'].isin(['LONG', 'SHORT'])).sum()
    mql_signals = (mql['signal'].isin(['LONG', 'SHORT'])).sum()

    print(f"\n{'='*60}")
    print(f"  PARITY ORACLE SUMMARY — {symbol} {year}")
    print(f"{'='*60}")
    print(f"  Python signals:  {py_signals}")
    print(f"  MQL5 signals:    {mql_signals}")
    print(f"  Gap:             {abs(py_signals - mql_signals)} signals")
    print(f"  Divergent bars:  {len(diff_df)}")

    if len(diff_df) == 0:
        print("\n  ✅ PERFECT PARITY — Python and MQL5 are identical")
    else:
        print(f"\n  ❌ DIVERGENCES FOUND")
        print(f"\nFirst 10 divergent bars:")
        for _, r in diff_df.head(10).iterrows():
            print(f"  {r['bar_time_gmt']}  PY={r['py_signal']}/{r['py_skip']}  "
                  f"MQL={r['mql_signal']}/{r['mql_skip']}")
            print(f"    Detail: {r['divergences'][:120]}")

        # Most common divergence type
        print(f"\nTop divergence patterns:")
        signal_diffs = diff_df[diff_df['py_signal'] != diff_df['mql_signal']]
        print(f"  Signal mismatch: {len(signal_diffs)} bars")
        if len(signal_diffs) > 0:
            from collections import Counter
            combos = Counter(zip(signal_diffs['py_signal'], signal_diffs['mql_signal']))
            for (py_s, mql_s), cnt in combos.most_common(5):
                print(f"    PY={py_s!r} -> MQL={mql_s!r}: {cnt} bars")

        trend_diffs = diff_df[diff_df['py_trend'] != diff_df['mql_trend']]
        print(f"  Trend mismatch: {len(trend_diffs)} bars")

        range_diffs = diff_df[diff_df['py_range_ok'] != diff_df['mql_range_ok']]
        print(f"  Range mismatch: {len(range_diffs)} bars")

    # Save diff
    out_path = os.path.join(TRACE_DIR, f'parity_diff_{symbol}_{year}.csv')
    diff_df.to_csv(out_path, index=False)
    print(f"\nFull diff saved: {out_path}")
    print('='*60)
    return diff_df


if __name__ == '__main__':
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'GBPUSD'
    year   = int(sys.argv[2]) if len(sys.argv) > 2 else 2023

    py_path  = os.path.join(TRACE_DIR, f'decision_trace_python_{symbol}_{year}.csv')
    mql_path = os.path.join(TRACE_DIR, f'decision_trace_mql5_{symbol}_{year}.csv')

    print(f"Parity Oracle: {symbol} {year}")
    print(f"  Python trace: {py_path}")
    print(f"  MQL5 trace:   {mql_path}")

    py_df  = load_trace(py_path, 'Python')
    mql_df = load_trace(mql_path, 'MQL5')

    diff_traces(py_df, mql_df, symbol, year)
