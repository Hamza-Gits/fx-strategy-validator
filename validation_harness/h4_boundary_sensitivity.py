"""
H4 boundary sensitivity test.
Council unanimous concern: pandas H4 bins (00/04/08 UTC) may not match broker H4 bars.
If PF is sensitive to the resample offset, the strategy is bin-specific (overfit to UTC alignment).
True edge should survive ±3hr offset shifts within similar PF range.

Tests ema_cross_adx with offsets 0,1,2,3 hours on full 2014-2024 XAUUSD data.
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv
from strategy_lib import ema_cross_adx, ema, adx, atr, _simulate_trade, NOTIONAL_RISK
from strategy_battery import compute_pf, compute_avg_r, trades_to_pnls, cost_per_trade_R, cost_adjust_pnls


DATA = os.path.join(os.path.dirname(__file__), '..', 'data',
                    'XAUUSD_H1_2013-2025.csv')


def resample_h4_with_offset(h1: pd.DataFrame, offset_hours: int) -> pd.DataFrame:
    """Resample H1 to H4 with a custom anchor offset (0 = UTC midnight, 1 = 01:00 UTC, etc)."""
    if offset_hours == 0:
        return h1.resample('4h').agg({
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum'
        }).dropna()
    # Use pandas offset parameter
    return h1.resample('4h', offset=f'{offset_hours}h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()


def ema_cross_adx_with_offset(h1, offset_hours, fast=10, slow=50, adx_period=14, adx_min=20.0,
                               atr_period=14, sl_mult=1.5, tp_mult=2.0, max_hold_bars=240):
    """Re-implementation of ema_cross_adx but with configurable H4 anchor offset."""
    h4 = resample_h4_with_offset(h1, offset_hours)
    h4['fast'] = ema(h4['close'], fast)
    h4['slow'] = ema(h4['close'], slow)
    h4['adx'] = adx(h4, adx_period)
    h4['atr'] = atr(h4, atr_period)
    h4 = h4.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    for i in range(1, len(h4)):
        ts = h4.index[i]
        if ts < in_trade_until:
            continue
        prev = h4.iloc[i - 1]
        bar = h4.iloc[i]
        if bar['adx'] < adx_min:
            continue
        cross_up = prev['fast'] <= prev['slow'] and bar['fast'] > bar['slow']
        cross_dn = prev['fast'] >= prev['slow'] and bar['fast'] < bar['slow']
        if not (cross_up or cross_dn):
            continue
        h1_idx = h1.index.searchsorted(ts)
        if h1_idx >= len(h1):
            continue
        entry = h1.iloc[h1_idx]['open']
        if cross_up:
            sl = entry - sl_mult * bar['atr']
            tp = entry + tp_mult * bar['atr']
            t = _simulate_trade(h1, h1_idx, 1, entry, sl, tp, max_hold_bars)
        else:
            sl = entry + sl_mult * bar['atr']
            tp = entry - tp_mult * bar['atr']
            t = _simulate_trade(h1, h1_idx, -1, entry, sl, tp, max_hold_bars)
        if t:
            trades.append(t)
            in_trade_until = t.exit_time
    return trades


def main():
    h1 = load_mt5_csv(DATA).loc['2014-01-01':'2024-12-31']
    is_split = int(len(h1) * 0.7)
    is_h1 = h1.iloc[:is_split]
    oos_h1 = h1.iloc[is_split:]

    print("="*78)
    print("  H4 BOUNDARY SENSITIVITY — ema_cross_adx (XAUUSD 2014-2024)")
    print("  Tests 4 offsets (0,1,2,3hr). If PF varies > 0.5, bin-specific overfit.")
    print("="*78)
    print(f"\n  IS:  {is_h1.index[0].date()} → {is_h1.index[-1].date()}  ({len(is_h1)} bars)")
    print(f"  OOS: {oos_h1.index[0].date()} → {oos_h1.index[-1].date()}  ({len(oos_h1)} bars)\n")

    cost_R = cost_per_trade_R()
    rows = []
    for offset in [0, 1, 2, 3]:
        for label, data in [('IS', is_h1), ('OOS', oos_h1)]:
            trades = ema_cross_adx_with_offset(data, offset)
            pnls = trades_to_pnls(trades)
            if len(pnls) == 0:
                rows.append({'offset_h': offset, 'period': label, 'n': 0,
                             'pf': 0, 'pf_cost': 0, 'avg_r': 0, 'wr': 0})
                continue
            cost_pnls = cost_adjust_pnls(pnls, cost_R)
            rows.append({
                'offset_h': offset, 'period': label,
                'n': len(trades),
                'pf': round(compute_pf(pnls), 3),
                'pf_cost': round(compute_pf(cost_pnls), 3),
                'avg_r': round(compute_avg_r(pnls), 4),
                'wr': round((pnls > 0).mean() * 100, 1),
            })
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()

    # Verdict
    oos_pfs = df[df['period'] == 'OOS']['pf_cost']
    oos_ns = df[df['period'] == 'OOS']['n']
    spread = oos_pfs.max() - oos_pfs.min()
    print(f"  OOS cost-adj PF range: {oos_pfs.min():.2f} → {oos_pfs.max():.2f}  (spread={spread:.2f})")
    print(f"  OOS N range:           {oos_ns.min()} → {oos_ns.max()}")
    if spread > 0.5:
        print(f"  VERDICT: BIN-SPECIFIC — strategy depends on H4 anchor. PF varies by {spread:.2f}.")
        print(f"           Original PF 2.46 may not survive on broker-aligned H4.")
    elif spread > 0.3:
        print(f"  VERDICT: MARGINAL — some bin sensitivity (spread {spread:.2f}). Caution.")
    else:
        print(f"  VERDICT: ROBUST — strategy invariant to H4 anchor (spread {spread:.2f}).")

    out = os.path.join(os.path.dirname(__file__), '..', 'diagnostic',
                       'h4_boundary_sensitivity.csv')
    df.to_csv(out, index=False)
    print(f"\n  Saved: {out}")


if __name__ == '__main__':
    main()
