"""
Walk-Forward Comparison — NDCB-XAU v2 Improvement Variants
============================================================
Tests 5 variants on the same 8-slice walk-forward (IS=4yr, OOS=1yr).
Uses FIXED structural parameters from v1.2 validation (CR=0.70 baseline).
Improvement variants add on top of CR=0.70 baseline.

Variants:
  A. Baseline     : CR=0.70, no extras  (v1.2 reference)
  B. CR=0.55      : tighter compression only
  C. CR+DIR       : CR=0.55 + D1 EMA-50 direction filter
  D. CR+DIR+PART  : CR=0.55 + direction filter + partial close (50% @ 1R, runner @ 3R)
  E. CR+PART      : CR=0.55 + partial close only (no direction filter)

Output: comparison table + per-slice breakdown.

Usage:
  python validation_harness/walk_forward_ndcb_v2.py
"""
import os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv
from strategy_lib import ndcb_xau

# ── Constants ──────────────────────────────────────────────────────────────────
NOTIONAL_RISK    = 100.0
SPREAD_PIPS      = 2.0
PIP_VALUE        = 0.1
COST_PER_TRADE_R = (SPREAD_PIPS * PIP_VALUE) / NOTIONAL_RISK

DATA = os.path.join(os.path.dirname(__file__), '..', 'data', 'XAUUSD_H1_2013-2025.csv')

SLICES = [
    ('2013-01-01', '2016-12-31', '2017-01-01', '2017-12-31'),
    ('2014-01-01', '2017-12-31', '2018-01-01', '2018-12-31'),
    ('2015-01-01', '2018-12-31', '2019-01-01', '2019-12-31'),
    ('2016-01-01', '2019-12-31', '2020-01-01', '2020-12-31'),
    ('2017-01-01', '2020-12-31', '2021-01-01', '2021-12-31'),
    ('2018-01-01', '2021-12-31', '2022-01-01', '2022-12-31'),
    ('2019-01-01', '2022-12-31', '2023-01-01', '2023-12-31'),
    ('2020-01-01', '2023-12-31', '2024-01-01', '2024-12-31'),
]

# ── Variant definitions ────────────────────────────────────────────────────────
VARIANTS = {
    'A_Baseline': dict(
        compression_ratio=0.70,
        direction_filter=False,
        partial_close_r=0.0,
    ),
    'B_CR055': dict(
        compression_ratio=0.55,
        direction_filter=False,
        partial_close_r=0.0,
    ),
    'C_CR055+Dir': dict(
        compression_ratio=0.55,
        direction_filter=True,
        partial_close_r=0.0,
    ),
    'D_CR055+Dir+Part': dict(
        compression_ratio=0.55,
        direction_filter=True,
        partial_close_r=1.0,   # close 50% at 1R
        partial_frac=0.50,
        tp_runner_r=3.0,       # runner goes to 3R
        tp_r=2.0,              # kept for fallback
    ),
    'E_CR055+Part': dict(
        compression_ratio=0.55,
        direction_filter=False,
        partial_close_r=1.0,
        partial_frac=0.50,
        tp_runner_r=3.0,
        tp_r=2.0,
    ),
}

# Fixed params shared by all variants
BASE_PARAMS = dict(
    entry_buffer_atr = 0.20,
    stop_atr_mult    = 1.00,
    atr_period       = 14,
    tp_r             = 2.00,
    trail_trigger_r  = 1.50,
    trail_atr_mult   = 1.00,
)


# ─────────────────────────────────────────────────────────────────────────────

def calc_metrics(trades):
    if not trades:
        return dict(n=0, pf=0.0, wr=0.0, exp_r=0.0, max_dd_r=0.0, total_r=0.0)
    pnls_raw  = np.array([t.pnl for t in trades])
    cost      = COST_PER_TRADE_R * NOTIONAL_RISK
    pnls_net  = pnls_raw - cost
    gp  = pnls_net[pnls_net > 0].sum()
    gl  = abs(pnls_net[pnls_net < 0].sum())
    pf  = gp / gl if gl > 0 else float('inf')
    wr  = float((pnls_raw > 0).mean())
    exp = float(pnls_net.mean() / NOTIONAL_RISK)
    eq  = np.cumsum(pnls_net)
    pk  = np.maximum.accumulate(eq)
    dd  = (pk - eq)
    return dict(
        n=len(trades),
        pf=float(pf),
        wr=float(wr),
        exp_r=exp,
        max_dd_r=float(dd.max() / NOTIONAL_RISK) if len(dd) else 0.0,
        total_r=float(pnls_net.sum() / NOTIONAL_RISK),
    )


def run_variant_on_oos(h1_oos, variant_name, variant_extra):
    params = {**BASE_PARAMS, **variant_extra}
    trades = ndcb_xau(h1_oos, **params)
    return calc_metrics(trades)


def main():
    print('=' * 80)
    print('  NDCB-XAU v2 — IMPROVEMENT VARIANT WALK-FORWARD')
    print('  5 variants × 8 OOS slices  (IS=4yr, OOS=1yr)')
    print('=' * 80)

    h1_all = load_mt5_csv(DATA)
    print(f'\n  Data: {h1_all.index[0]} → {h1_all.index[-1]}  ({len(h1_all):,} bars)\n')

    # ── Per-slice results for each variant ────────────────────────────────
    results = {v: [] for v in VARIANTS}

    for i, (is_start, is_end, oos_start, oos_end) in enumerate(SLICES):
        h1_oos = h1_all.loc[oos_start:oos_end]
        if len(h1_oos) < 100:
            continue
        print(f'── OOS {oos_start[:4]} ──────────────────────────────────────────')
        for vname, vextra in VARIANTS.items():
            m = run_variant_on_oos(h1_oos, vname, vextra)
            results[vname].append({'oos_year': oos_start[:4], **m})
            kill = '✗' if m['pf'] < 0.90 and m['n'] >= 5 else ('✓' if m['n'] >= 5 else '~')
            print(f'   {vname:<22}  N={m["n"]:>3}  PF={m["pf"]:.3f}  WR={m["wr"]*100:.1f}%  '
                  f'ExpR={m["exp_r"]:+.3f}  MaxDD={m["max_dd_r"]:.1f}R  '
                  f'TotalR={m["total_r"]:+.1f}  [{kill}]')
        print()

    # ── Summary table ─────────────────────────────────────────────────────
    print('=' * 80)
    print('  SUMMARY  (across all 8 OOS slices)')
    print('=' * 80)
    print(f'\n  {"Variant":<22}  {"N/yr":>5}  {"Med PF":>7}  {"Min PF":>7}  '
          f'{"WR%":>6}  {"ExpR":>6}  {"MaxDD":>7}  {"TotR":>7}  Kills')
    print('  ' + '-' * 75)

    summary_rows = []
    for vname, slice_list in results.items():
        if not slice_list:
            continue
        df = pd.DataFrame(slice_list)
        valid = df[df['n'] >= 5]
        kills = int((valid['pf'] < 0.90).sum()) if len(valid) else 0
        row = dict(
            variant=vname,
            n_yr=df['n'].mean(),
            med_pf=df['pf'].median(),
            min_pf=df['pf'].min(),
            wr=df['wr'].mean(),
            exp_r=df['exp_r'].mean(),
            max_dd=df['max_dd_r'].max(),
            tot_r=df['total_r'].sum(),
            kills=kills,
        )
        summary_rows.append(row)
        print(f'  {vname:<22}  {row["n_yr"]:>5.0f}  {row["med_pf"]:>7.3f}  '
              f'{row["min_pf"]:>7.3f}  {row["wr"]*100:>6.1f}  '
              f'{row["exp_r"]:>+6.3f}  {row["max_dd"]:>7.1f}R  '
              f'{row["tot_r"]:>+7.1f}  {kills}/8')

    print()

    # ── Winner identification ──────────────────────────────────────────────
    if summary_rows:
        best = max(summary_rows, key=lambda r: (8 - r['kills']) * 10 + r['med_pf'])
        print(f'  Best variant by combined score: {best["variant"]}')
        print(f'    Med PF={best["med_pf"]:.3f}  Min PF={best["min_pf"]:.3f}  '
              f'Kills={best["kills"]}/8  TotR={best["tot_r"]:+.1f}R')

        if best['med_pf'] >= 1.30:
            print(f'\n  ✓ VERDICT: {best["variant"]} exceeds PF 1.30 target — ready for MQL5 port')
        elif best['med_pf'] >= 1.15:
            print(f'\n  → VERDICT: {best["variant"]} improves on v1.2 baseline (PF 1.12) — '
                  'marginal, port with caution')
        else:
            print(f'\n  ✗ VERDICT: No variant reaches PF 1.30. Best is {best["med_pf"]:.3f}. '
                  'Need deeper structural changes.')

    # ── Save results ──────────────────────────────────────────────────────
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'diagnostic')
    os.makedirs(out_dir, exist_ok=True)
    all_rows = []
    for vname, slice_list in results.items():
        for r in slice_list:
            all_rows.append({'variant': vname, **r})
    if all_rows:
        out_path = os.path.join(out_dir, 'ndcb_v2_variants.csv')
        pd.DataFrame(all_rows).to_csv(out_path, index=False)
        print(f'\n  Results saved: {out_path}')

    print('=' * 80)


if __name__ == '__main__':
    main()
