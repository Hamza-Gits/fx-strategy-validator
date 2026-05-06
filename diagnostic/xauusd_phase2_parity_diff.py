"""
XAUUSD Phase 2 — Parity Oracle Diff
====================================
Compares decision_trace_python_XAUUSD_YEAR.csv vs
            decision_trace_mql5_XAUUSD_YEAR.csv

Schema (both files):
  date, bar_time_gmt, bar_close, bar_high, bar_low,
  atr_d1, prev_high, prev_low, w1_close, w1_ema, trend_dir,
  threshold, allow_long, allow_short, signal, skip_reason,
  entry_price, sl, tp

Usage:
  python xauusd_phase2_parity_diff.py [YEAR]
"""
import sys, os
import pandas as pd
import numpy as np

TRACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'traces')

CMP_COLS = ['signal', 'trend_dir', 'allow_long', 'allow_short', 'skip_reason']
NUM_COLS = ['bar_close', 'atr_d1', 'prev_high', 'prev_low',
            'w1_close', 'w1_ema', 'threshold',
            'entry_price', 'sl', 'tp']
NUM_TOL  = 0.05  # 5 cents on gold ($1800+, so ~0.003%)


def load_trace(path: str, label: str) -> pd.DataFrame:
    if not os.path.exists(path):
        print(f"ERROR: {label} trace not found at:\n  {path}")
        sys.exit(1)
    df = pd.read_csv(path, dtype=str)
    df['bar_time_gmt'] = pd.to_datetime(df['bar_time_gmt'])
    df = df.set_index('bar_time_gmt').sort_index()
    print(f"Loaded {len(df)} rows from {label}")
    return df


def diff_traces(py_df: pd.DataFrame, mql_df: pd.DataFrame, year: int) -> pd.DataFrame:
    common_idx = py_df.index.intersection(mql_df.index)
    missing_py  = len(mql_df.index.difference(py_df.index))
    missing_mql = len(py_df.index.difference(mql_df.index))

    print(f"\nTimestamp alignment:")
    print(f"  Common bars:         {len(common_idx)}")
    print(f"  In MQL5 not Python:  {missing_py}")
    print(f"  In Python not MQL5:  {missing_mql}")

    py  = py_df.loc[common_idx]
    mql = mql_df.loc[common_idx]

    diff_rows = []

    for ts in py.index:
        py_row  = py.loc[ts]
        mql_row = mql.loc[ts]
        divergences = []

        for col in CMP_COLS:
            pv = str(py_row.get(col, '')).strip()
            mv = str(mql_row.get(col, '')).strip()
            if pv != mv:
                divergences.append(f"{col}: PY={pv!r} vs MQL={mv!r}")

        for col in NUM_COLS:
            pv_s = str(py_row.get(col, '')).strip()
            mv_s = str(mql_row.get(col, '')).strip()
            if pv_s in ('', 'nan') and mv_s in ('', 'nan'):
                continue
            try:
                pv = float(pv_s) if pv_s not in ('', 'nan') else np.nan
                mv = float(mv_s) if mv_s not in ('', 'nan') else np.nan
                if pd.isna(pv) and pd.isna(mv):
                    continue
                if pd.isna(pv) or pd.isna(mv):
                    divergences.append(f"{col}: PY={pv} vs MQL={mv}")
                    continue
                if abs(pv - mv) > NUM_TOL:
                    divergences.append(f"{col}: PY={pv:.3f} vs MQL={mv:.3f}")
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
                'divergences': ' | '.join(divergences),
            })

    diff_df = pd.DataFrame(diff_rows)
    py_signals  = (py['signal'].isin(['LONG','SHORT'])).sum()
    mql_signals = (mql['signal'].isin(['LONG','SHORT'])).sum()

    print(f"\n{'='*70}")
    print(f"  XAUUSD PARITY ORACLE — {year}")
    print(f"{'='*70}")
    print(f"  Python signals:  {py_signals}")
    print(f"  MQL5 signals:    {mql_signals}")
    print(f"  Gap:             {abs(py_signals - mql_signals)} signals")
    print(f"  Divergent bars:  {len(diff_df)}")

    if len(diff_df) == 0:
        print("\n  PERFECT PARITY — Python and MQL5 identical")
    else:
        print(f"\n  DIVERGENCES FOUND — first 10 bars:")
        for _, r in diff_df.head(10).iterrows():
            print(f"  {r['bar_time_gmt']}  PY={r['py_signal']}/{r['py_skip']}  "
                  f"MQL={r['mql_signal']}/{r['mql_skip']}")
            print(f"    Detail: {r['divergences'][:140]}")

        signal_diffs = diff_df[diff_df['py_signal'] != diff_df['mql_signal']]
        print(f"\n  Signal mismatches: {len(signal_diffs)} bars")
        trend_diffs  = diff_df[diff_df['py_trend']  != diff_df['mql_trend']]
        print(f"  Trend mismatches:  {len(trend_diffs)} bars")

    out_path = os.path.join(TRACE_DIR, f'parity_diff_XAUUSD_{year}.csv')
    diff_df.to_csv(out_path, index=False)
    print(f"\nFull diff saved: {out_path}")
    print('='*70)
    return diff_df


if __name__ == '__main__':
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    py_path  = os.path.join(TRACE_DIR, f'decision_trace_python_XAUUSD_{year}.csv')
    mql_path = os.path.join(TRACE_DIR, f'decision_trace_mql5_XAUUSD_{year}.csv')

    print(f"XAUUSD Parity Oracle: {year}")
    print(f"  Python trace: {py_path}")
    print(f"  MQL5 trace:   {mql_path}")

    py_df  = load_trace(py_path, 'Python')
    mql_df = load_trace(mql_path, 'MQL5')
    diff_traces(py_df, mql_df, year)
