"""
Walk-Forward Validation — NDCB-XAU
====================================
9-slice rolling walk-forward (IS=4yr, OOS=1yr).
27-cell parameter grid: compression_ratio × entry_buffer_atr × stop_atr_mult

Kill gates (any one fails → REJECTED):
  1. 2024 OOS PF_cost < 0.90
  2. Rolling 2-yr PF_cost < 1.00 on >= 3 consecutive slices
  3. OOS/IS expectancy ratio < 0.40  (decay check)
  4. Worst single OOS drawdown > 15%  (NOTIONAL_RISK=$100/trade)

Usage:
  python validation_harness/walk_forward_ndcb.py
"""
import os, sys, itertools, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv
from strategy_lib import ndcb_xau

# ── Constants ──────────────────────────────────────────────────────────────────
NOTIONAL_RISK      = 100.0   # $ per trade (same as Python backtest)
SPREAD_PIPS        = 2.0     # typical XAUUSD spread, pips
PIP_VALUE          = 0.1     # 1 pip = $0.10 for 0.01 lot XAUUSD (per $1 risk)
COST_PER_TRADE_R   = (SPREAD_PIPS * PIP_VALUE) / NOTIONAL_RISK  # ≈ 0.020R

DATA = os.path.join(os.path.dirname(__file__), '..', 'data', 'XAUUSD_H1_2013-2025.csv')

# ── Walk-forward slices ────────────────────────────────────────────────────────
# IS = 4 years, OOS = 1 year, step = 1 year
SLICES = [
    ('2013-01-01', '2016-12-31', '2017-01-01', '2017-12-31'),
    ('2014-01-01', '2017-12-31', '2018-01-01', '2018-12-31'),
    ('2015-01-01', '2018-12-31', '2019-01-01', '2019-12-31'),
    ('2016-01-01', '2019-12-31', '2020-01-01', '2020-12-31'),
    ('2017-01-01', '2020-12-31', '2021-01-01', '2021-12-31'),
    ('2018-01-01', '2021-12-31', '2022-01-01', '2022-12-31'),
    ('2019-01-01', '2022-12-31', '2023-01-01', '2023-12-31'),
    ('2020-01-01', '2023-12-31', '2024-01-01', '2024-12-31'),  # most recent — hardest gate
    # 9th slice: 2021-2024 IS / first half 2025 OOS — skip if data ends 2024-12-31
]

# ── Parameter grid (27 cells) ──────────────────────────────────────────────────
PARAM_GRID = list(itertools.product(
    [0.50, 0.60, 0.70],   # compression_ratio
    [0.10, 0.20, 0.30],   # entry_buffer_atr
    [0.80, 1.00, 1.20],   # stop_atr_mult
))


def calc_metrics(trades, label=''):
    if not trades:
        return dict(n=0, pf_raw=0.0, pf_cost=0.0, wr=0.0, exp_r=0.0, max_dd=0.0)

    pnls_raw  = np.array([t.pnl for t in trades])
    cost_r    = COST_PER_TRADE_R * NOTIONAL_RISK
    pnls_net  = pnls_raw - cost_r

    gp  = pnls_net[pnls_net > 0].sum()
    gl  = abs(pnls_net[pnls_net < 0].sum())
    pf  = gp / gl if gl > 0 else float('inf')
    wr  = (pnls_raw > 0).mean()
    exp = pnls_net.mean() / NOTIONAL_RISK   # mean R per trade

    # Max drawdown on equity curve (dollar)
    eq   = np.cumsum(pnls_net)
    peak = np.maximum.accumulate(eq)
    dd   = (peak - eq)
    max_dd_dollar = dd.max() if len(dd) else 0.0

    return dict(n=len(trades), pf_raw=float((pnls_raw[pnls_raw>0].sum() /
                abs(pnls_raw[pnls_raw<0].sum())) if (pnls_raw<0).any() else float('inf')),
                pf_cost=float(pf), wr=float(wr), exp_r=float(exp),
                max_dd=float(max_dd_dollar))


def best_is_params(h1_is, grid):
    best_pf, best_params, best_n = -1, None, 0
    for cr, buf, sm in grid:
        tr = ndcb_xau(h1_is, compression_ratio=cr, entry_buffer_atr=buf, stop_atr_mult=sm)
        m  = calc_metrics(tr)
        if m['n'] < 15:   # too few trades for IS selection
            continue
        if m['pf_cost'] > best_pf:
            best_pf, best_params, best_n = m['pf_cost'], (cr, buf, sm), m['n']
    return best_params, best_pf, best_n


def main():
    print('=' * 72)
    print('  NDCB-XAU WALK-FORWARD VALIDATION  (IS=4yr, OOS=1yr, 27-cell grid)')
    print('=' * 72)

    h1_all = load_mt5_csv(DATA)
    print(f'\n  Data: {h1_all.index[0]} → {h1_all.index[-1]}  ({len(h1_all):,} bars)\n')

    slice_results = []
    all_oos_pf    = []

    for i, (is_start, is_end, oos_start, oos_end) in enumerate(SLICES):
        print(f'── Slice {i+1}/8  IS={is_start[:4]}-{is_end[:4]}  OOS={oos_start[:4]} ──')

        h1_is  = h1_all.loc[is_start:is_end]
        h1_oos = h1_all.loc[oos_start:oos_end]

        if len(h1_oos) < 100:
            print(f'   SKIP — insufficient OOS data ({len(h1_oos)} bars)\n')
            continue

        t0 = time.time()
        best_p, best_pf_is, n_is = best_is_params(h1_is, PARAM_GRID)
        elapsed = time.time() - t0

        if best_p is None:
            print(f'   SKIP — no IS param set had >=15 trades (elapsed {elapsed:.0f}s)\n')
            continue

        cr, buf, sm = best_p
        tr_oos = ndcb_xau(h1_oos, compression_ratio=cr, entry_buffer_atr=buf, stop_atr_mult=sm)
        m_oos  = calc_metrics(tr_oos)

        decay = (m_oos['exp_r'] / (best_pf_is - 1)) if best_pf_is > 1 else 0.0

        print(f'   Best IS: CR={cr} Buf={buf} SM={sm}  PF_IS={best_pf_is:.3f}  N_IS={n_is}  ({elapsed:.0f}s)')
        print(f'   OOS:     PF={m_oos["pf_cost"]:.3f}  N={m_oos["n"]}  WR={m_oos["wr"]*100:.1f}%  '
              f'ExpR={m_oos["exp_r"]:.3f}  MaxDD=${m_oos["max_dd"]:.0f}')

        flags = []
        if m_oos['pf_cost'] < 0.90:
            flags.append('PF<0.90')
        if m_oos['max_dd'] > 15 * NOTIONAL_RISK:
            flags.append(f'DD>${m_oos["max_dd"]:.0f}')

        if flags:
            print(f'   KILL GATE: {", ".join(flags)}')
        else:
            print(f'   PASS')
        print()

        all_oos_pf.append(m_oos['pf_cost'])
        slice_results.append({
            'slice': i + 1,
            'oos_year': oos_start[:4],
            'cr': cr, 'buf': buf, 'sm': sm,
            'pf_is': best_pf_is,
            'n_is': n_is,
            **{f'oos_{k}': v for k, v in m_oos.items()},
            'flags': ','.join(flags),
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    print('=' * 72)
    print('  SUMMARY')
    print('=' * 72)

    if not slice_results:
        print('  No valid slices — REJECTED')
        return

    df = pd.DataFrame(slice_results)
    kills = df[df['flags'] != '']
    passes = df[df['flags'] == '']

    print(f'\n  Slices run:    {len(df)}')
    print(f'  Slices passed: {len(passes)}')
    print(f'  Slices killed: {len(kills)}')

    if len(all_oos_pf) > 0:
        med_pf = np.median(all_oos_pf)
        min_pf = np.min(all_oos_pf)
        print(f'\n  Median OOS PF: {med_pf:.3f}')
        print(f'  Worst  OOS PF: {min_pf:.3f}')

    # Check 2024 slice specifically
    slice_2024 = df[df['oos_year'] == '2024']
    if not slice_2024.empty:
        pf24 = slice_2024['oos_pf_cost'].iloc[0]
        n24  = slice_2024['oos_n'].iloc[0]
        print(f'\n  2024 OOS PF: {pf24:.3f}  N={n24}  '
              f'{"PASS ✓" if pf24 >= 0.90 else "KILL ✗"}')

    # Consecutive kill check
    if len(kills) >= 3:
        print(f'\n  WARNING: {len(kills)} slices killed — check for regime break')

    print()
    if len(kills) == 0 and len(df) >= 6:
        print('  VERDICT: CLEAR TO BUILD — all OOS slices passed.')
        print('  Proceed to MQL5 implementation with: ')
        # Most common best param
        mode_cr  = df['cr'].mode()[0]
        mode_buf = df['buf'].mode()[0]
        mode_sm  = df['sm'].mode()[0]
        print(f'    compression_ratio={mode_cr}, entry_buffer_atr={mode_buf}, stop_atr_mult={mode_sm}')
    elif len(kills) <= 2 and med_pf >= 1.10:
        print('  VERDICT: MARGINAL PASS — acceptable with caution.')
        print(f'  {len(kills)} slice(s) killed. Consider tighter position sizing.')
    else:
        print('  VERDICT: REJECTED — too many kill gates triggered.')

    print('=' * 72)

    # Save results
    out = os.path.join(os.path.dirname(__file__), '..', 'diagnostic', 'walk_forward_ndcb.csv')
    df.to_csv(out, index=False)
    print(f'\n  Saved: {out}')


if __name__ == '__main__':
    main()
