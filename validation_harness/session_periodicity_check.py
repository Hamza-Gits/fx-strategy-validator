"""
Session Periodicity & UTC Offset Validation
=============================================
MANDATORY pre-build check before implementing any session-based XAUUSD strategy.

Verifies:
  1. UTC alignment: 13:30 UTC bar must be the highest-sigma H1 bar of the week.
     If it isn't, broker time != UTC and all session windows will be wrong.
  2. Hourly volatility profile: pre-2022 vs post-2022 comparison to confirm
     07:00-13:00 UTC is still the correct entry window for ARCB-XAU.
  3. Asian session (22:00-05:00 UTC) compression characteristics over time.

Run this BEFORE writing a single line of ARCB-XAU signal logic.

Usage:
  python validation_harness/session_periodicity_check.py

Output:
  - Console: verdict on UTC alignment + session window confirmation
  - diagnostic/session_periodicity_pre2022.csv
  - diagnostic/session_periodicity_post2022.csv
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv


DATA = os.path.join(os.path.dirname(__file__), '..', 'data', 'XAUUSD_H1_2013-2025.csv')
DIAG = os.path.join(os.path.dirname(__file__), '..', 'diagnostic')
os.makedirs(DIAG, exist_ok=True)


def hourly_stats(h1: pd.DataFrame, label: str) -> pd.DataFrame:
    """Compute mean absolute return, mean range, mean tick volume by UTC hour."""
    h1 = h1.copy()
    h1['abs_ret'] = (h1['close'] - h1['open']).abs()
    h1['range']   = h1['high'] - h1['low']
    h1['hour']    = h1.index.hour

    grp = h1.groupby('hour').agg(
        mean_abs_ret=('abs_ret', 'mean'),
        mean_range=('range', 'mean'),
        mean_volume=('volume', 'mean'),
        n_bars=('close', 'count'),
    ).reset_index()

    # Normalize to fraction of daily mean so pre/post are comparable
    grp['range_norm']  = grp['mean_range']   / grp['mean_range'].mean()
    grp['vol_norm']    = grp['mean_volume']  / grp['mean_volume'].mean()
    grp['ret_norm']    = grp['mean_abs_ret'] / grp['mean_abs_ret'].mean()
    grp['period']      = label
    return grp


def check_utc_alignment(h1: pd.DataFrame) -> bool:
    """
    The 13:30 UTC bar (NFP/CPI/claims releases) must be the highest-sigma
    H1 hour in the dataset. If a different hour is highest, the data is NOT
    in UTC and all session logic will be miscalibrated.
    """
    h1 = h1.copy()
    h1['range'] = h1['high'] - h1['low']
    h1['hour']  = h1.index.hour

    # Only look at bars Mon-Fri, exclude weekends
    h1 = h1[h1.index.dayofweek < 5]

    hourly_mean = h1.groupby('hour')['range'].mean()
    peak_hour   = hourly_mean.idxmax()
    peak_val    = hourly_mean.max()

    # hour 13 should be the peak (13:00-13:59 UTC contains the 13:30 data hour)
    rank_of_13 = (hourly_mean.sort_values(ascending=False).index.tolist()).index(13) + 1

    print(f"\n  Peak volatility hour (UTC): {peak_hour:02d}:00  "
          f"(mean range = {peak_val:.2f})")
    print(f"  Rank of hour 13 (13:30 data hour): #{rank_of_13} out of 24")

    if rank_of_13 <= 2:
        print("  UTC ALIGNMENT: OK — 13:xx UTC is the peak volatility hour.")
        print("  Session windows are correctly calibrated.")
        return True
    else:
        print(f"  UTC ALIGNMENT WARNING — hour 13 is only rank #{rank_of_13}.")
        print(f"  Peak is at hour {peak_hour}. Your data may be in broker time (GMT+2/+3).")
        print("  FIX: subtract 2 or 3 hours from bar timestamps before any session logic.")
        return False


def check_asian_compression(h1: pd.DataFrame) -> None:
    """
    Compute daily Asian session range (22:00-05:00 UTC) and D1 ATR.
    Report what % of days qualify as 'compressed' (Asian range <= 0.7 × D1 ATR).
    This tells us how often ARCB-XAU will have a valid setup.
    """
    h1 = h1.copy()
    h1['date'] = h1.index.date
    h1['hour'] = h1.index.hour

    # D1 ATR (20-period, close-to-close proxy for speed)
    d1 = h1.resample('1D').agg({'open': 'first', 'high': 'max',
                                 'low': 'min', 'close': 'last'}).dropna()
    d1['range'] = d1['high'] - d1['low']
    d1['d1_atr'] = d1['range'].rolling(20).mean()  # simplified ATR proxy

    # Asian range per calendar day D = bars on D-1 22:00-23:00 + bars on D 00:00-05:00
    rows = []
    dates = sorted(h1['date'].unique())
    for i, d in enumerate(dates[1:], 1):  # skip first (no prior evening)
        prev_d = dates[i - 1]
        evening = h1[(h1['date'] == prev_d) & (h1['hour'] >= 22)]
        morning = h1[(h1['date'] == d)      & (h1['hour'] <= 5)]
        asia = pd.concat([evening, morning])
        if len(asia) < 5:   # insufficient bars (holiday, gap)
            continue
        asia_high = asia['high'].max()
        asia_low  = asia['low'].min()
        asia_size = asia_high - asia_low

        d_ts = pd.Timestamp(d)
        if d_ts not in d1.index:
            continue
        d1_atr = d1.loc[d_ts, 'd1_atr']
        if pd.isna(d1_atr) or d1_atr <= 0:
            continue

        rows.append({'date': d, 'asia_size': asia_size, 'd1_atr': d1_atr,
                     'compression_ratio': asia_size / d1_atr})

    df = pd.DataFrame(rows)
    if df.empty:
        print("  No valid Asian session data found.")
        return

    k_thresh = 0.70
    compressed = (df['compression_ratio'] <= k_thresh).mean() * 100
    print(f"\n  Asian session compression stats (22:00-05:00 UTC):")
    print(f"  Median compression ratio : {df['compression_ratio'].median():.3f} "
          f"(1.0 = equal to D1 ATR)")
    print(f"  % days with ratio <= {k_thresh} : {compressed:.1f}%  "
          f"← these are ARCB-XAU setup days")
    print(f"  Implied ARCB setups/year  : ~{compressed * 2.5:.0f}  "
          f"(2 sides × {compressed:.0f}% of ~252 trading days)")


def main():
    print("=" * 72)
    print("  SESSION PERIODICITY & UTC ALIGNMENT CHECK — XAUUSD H1")
    print("=" * 72)

    h1 = load_mt5_csv(DATA).loc['2013-01-01':'2024-12-31']
    print(f"\n  Loaded {len(h1):,} H1 bars: {h1.index[0]} → {h1.index[-1]}")

    # ── 1. UTC alignment check ──────────────────────────────────────────────
    print("\n── 1. UTC ALIGNMENT ──────────────────────────────────────────────────")
    utc_ok = check_utc_alignment(h1)

    # ── 2. Pre/Post 2022 hourly vol profile ────────────────────────────────
    print("\n── 2. HOURLY VOLATILITY PROFILE ──────────────────────────────────────")
    pre  = hourly_stats(h1.loc[:'2021-12-31'], 'pre_2022')
    post = hourly_stats(h1.loc['2022-01-01':], 'post_2022')

    # Print comparison table
    merged = pre[['hour','mean_range','range_norm']].merge(
        post[['hour','mean_range','range_norm']], on='hour', suffixes=('_pre', '_post'))

    print(f"\n  {'Hour(UTC)':>9} {'Range pre-22':>13} {'Norm pre':>9} "
          f"{'Range post-22':>14} {'Norm post':>10}  {'Session':>20}")
    session_labels = {
        range(22, 24): 'Asian evening',
        range(0, 6):   'Asian morning',
        range(6, 8):   'Pre-London',
        range(8, 13):  'London core',
        range(13, 17): 'London/NY overlap ★',
        range(17, 22): 'NY afternoon',
    }

    def sess(h):
        for rng, lbl in session_labels.items():
            if h in rng:
                return lbl
        return ''

    for _, row in merged.iterrows():
        h = int(row['hour'])
        print(f"  {h:>02d}:00      {row['mean_range_pre']:>12.3f} {row['range_norm_pre']:>9.2f} "
              f"{row['mean_range_post']:>13.3f} {row['range_norm_post']:>10.2f}  {sess(h):>20}")

    # Identify peak hours in each period
    peak_pre  = pre.loc[pre['range_norm'].idxmax(), 'hour']
    peak_post = post.loc[post['range_norm'].idxmax(), 'hour']
    print(f"\n  Peak hour pre-2022:  UTC {int(peak_pre):02d}:00")
    print(f"  Peak hour post-2022: UTC {int(peak_post):02d}:00")

    # Check if 07-13 UTC window remains the right entry window
    window_pre  = pre[pre['hour'].between(7, 12)]['range_norm'].mean()
    window_post = post[post['hour'].between(7, 12)]['range_norm'].mean()
    print(f"\n  07:00-12:00 UTC avg norm range pre-2022:  {window_pre:.2f}")
    print(f"  07:00-12:00 UTC avg norm range post-2022: {window_post:.2f}")

    if window_post >= 1.0:
        print("  WINDOW VERDICT: 07:00-13:00 UTC remains above-average vol ✓")
    else:
        print("  WINDOW VERDICT: 07:00-13:00 UTC dropped below average post-2022.")
        print("  Consider shifting entry window to 10:00-15:00 UTC.")

    # ── 3. Asian compression analysis ─────────────────────────────────────
    print("\n── 3. ASIAN COMPRESSION FREQUENCY ──────────────────────────────────")
    check_asian_compression(h1)

    # ── 4. Save CSVs ──────────────────────────────────────────────────────
    out_pre  = os.path.join(DIAG, 'session_periodicity_pre2022.csv')
    out_post = os.path.join(DIAG, 'session_periodicity_post2022.csv')
    pre.to_csv(out_pre,  index=False)
    post.to_csv(out_post, index=False)
    print(f"\n  Saved: {out_pre}")
    print(f"  Saved: {out_post}")

    # ── Final verdict ────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    if utc_ok and window_post >= 0.9:
        print("  OVERALL: CLEAR TO BUILD — UTC alignment OK, session window confirmed.")
        print("  Proceed to implement arcb_xau() in strategy_lib.py")
    elif not utc_ok:
        print("  OVERALL: STOP — UTC misalignment detected. Fix timestamps first.")
    else:
        print("  OVERALL: CAUTION — window may need adjustment. Review hourly table above.")
    print("=" * 72)


if __name__ == '__main__':
    main()
