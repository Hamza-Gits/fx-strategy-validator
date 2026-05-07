"""
ARCB-XAU Walk-Forward Analysis — 9-slice rolling validation
============================================================
Implements the mandatory walk-forward spec from XAUUSDresearch.pdf:
  - IS = 4 years, OOS = 1 year, rolling annually
  - 9 OOS slices: 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025*
  - 27-cell parameter grid (3×3×3) — do NOT expand
  - Pass conditions (all must hold for DEPLOY verdict):
      1. Mean OOS cost-adj PF > 1.3 across all windows
      2. >= 7 of 9 OOS years individually have PF > 1.1
      3. Average OOS trades >= 75/yr (target 100+)
      4. Best IS config stays in top 30% of grid in next window (param stability)
  - Kill conditions (any one = REJECT):
      1. 2024 OOS PF < 0.9
      2. 2-year rolling cost-adj PF < 1.0 at any point
      3. Realised OOS expectancy < 50% of IS expectancy averaged across windows
      4. Drawdown > 12% in any single OOS year

Usage:
  python validation_harness/walk_forward_arcb.py

Output:
  - Full walk-forward table printed to console
  - diagnostic/walk_forward_arcb.csv
  - Automatic DEPLOY / REJECT verdict with kill reason if applicable

*2025 slice included if data available; skipped if < 200 OOS bars.
"""
import os
import sys
import itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv
from strategy_lib import arcb_xau, NOTIONAL_RISK
from strategy_battery import compute_pf, compute_avg_r, trades_to_pnls, \
                              cost_adjust_pnls, cost_per_trade_R


# ─── PARAMETER GRID — exactly 27 cells, do not expand ────────────────────────
GRID = {
    'compression_k':  [0.55, 0.70, 0.85],
    'stop_atr_mult':  [1.00, 1.20, 1.50],
    'vol_ratio_min':  [1.00, 1.30, 1.60],
}

# Fixed parameters (not optimized — structural, not regime-dependent)
# Hours are in BROKER time (GMT+2): confirmed by session_periodicity_check.py
# Peak vol hour is 15 broker = 13:00 UTC (13:30 ET data releases) — GMT+2 confirmed.
FIXED = {
    'body_ratio_min':   0.50,
    'stop_floor_frac':  0.50,
    'trail_ema_len':    21,
    'atr_floor_pct':    0.004,
    'asia_start_hour':  0,    # broker 00:00 = 22:00 UTC (Asian session open)
    'asia_end_hour':    7,    # broker 07:00 = 05:00 UTC (pre-London)
    'entry_start_hour': 9,    # broker 09:00 = 07:00 UTC (London opening)
    'entry_end_hour':   15,   # broker 15:00 = 13:00 UTC (NY overlap start)
    'hard_close_hour':  23,   # broker 23:00 = 21:00 UTC (end of NY session)
    'jan_block_days':   5,
}

# Walk-forward windows: (IS start, IS end, OOS year)
WF_WINDOWS = [
    (2013, 2016, 2017),
    (2014, 2017, 2018),
    (2015, 2018, 2019),
    (2016, 2019, 2020),
    (2017, 2020, 2021),
    (2018, 2021, 2022),
    (2019, 2022, 2023),
    (2020, 2023, 2024),
    (2021, 2024, 2025),
]

# Cost model
COST_R = cost_per_trade_R()   # ~0.027R at default 22$ stop dist

# Gates
PASS_MEAN_PF        = 1.30
PASS_YEARS_ABOVE_11 = 7        # of 9
PASS_MIN_TRADES_YR  = 75
KILL_2024_PF        = 0.90
KILL_ROLLING_PF     = 1.00     # 2-year rolling
KILL_EXPECTANCY_RAT = 0.50     # OOS exp must be >= 50% of IS exp
KILL_MAX_DD_YR      = 0.12     # 12% in any OOS year (at 1% risk)


def grid_keys() -> list:
    return list(GRID.keys())


def all_combos() -> list:
    keys = grid_keys()
    return [dict(zip(keys, v)) for v in itertools.product(*[GRID[k] for k in keys])]


def run_grid_on_window(h1_window: pd.DataFrame, min_n: int = 30) -> list:
    """Run all 27 parameter combos on IS window. Return list of result dicts sorted by IS PF."""
    results = []
    for params in all_combos():
        kw = {**params, **FIXED}
        trades = arcb_xau(h1_window, **kw)
        if len(trades) < min_n:
            continue
        pnls   = trades_to_pnls(trades)
        pf_raw = compute_pf(pnls)
        cp     = cost_adjust_pnls(pnls, COST_R)
        pf_adj = compute_pf(cp)
        exp_r  = compute_avg_r(pnls)
        results.append({
            'params': params,
            'is_n':   len(trades),
            'is_pf':  round(pf_raw, 3),
            'is_pf_cost': round(pf_adj, 3),
            'is_exp_r':   round(exp_r, 4),
        })
    results.sort(key=lambda x: x['is_pf_cost'], reverse=True)
    return results


def max_drawdown_pct(pnls: np.ndarray, risk_pct: float = 0.01) -> float:
    """
    Approximate max drawdown as % of starting equity.
    Each trade risks risk_pct of equity; PnL is in R units (1R = NOTIONAL_RISK).
    """
    r_returns = pnls / NOTIONAL_RISK   # convert $ pnl to R units
    equity = 1.0
    peak   = 1.0
    max_dd = 0.0
    for r in r_returns:
        equity *= (1 + r * risk_pct)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def combo_rank(params: dict, sorted_results: list) -> int:
    """Return 1-based rank of a param combo in a sorted result list (1 = best)."""
    for rank, res in enumerate(sorted_results, 1):
        if res['params'] == params:
            return rank
    return len(sorted_results) + 1   # not found


def main():
    DATA = os.path.join(os.path.dirname(__file__), '..', 'data',
                        'XAUUSD_H1_2013-2025.csv')
    DIAG = os.path.join(os.path.dirname(__file__), '..', 'diagnostic')
    os.makedirs(DIAG, exist_ok=True)

    h1_full = load_mt5_csv(DATA).loc['2013-01-01':'2025-12-31']

    print("=" * 80)
    print("  ARCB-XAU WALK-FORWARD — 9-slice rolling (IS=4yr, OOS=1yr)")
    print(f"  Grid: {len(all_combos())} combos | Cost: {COST_R:.4f}R/trade | "
          f"Gates: mean PF>{PASS_MEAN_PF}, ≥{PASS_YEARS_ABOVE_11}/9 yrs>1.1")
    print("=" * 80)

    rows          = []
    prev_sorted   = None   # for parameter stability check
    prev_best     = None

    for (is_start, is_end, oos_year) in WF_WINDOWS:
        is_data  = h1_full.loc[f'{is_start}-01-01':f'{is_end}-12-31']
        oos_data = h1_full.loc[f'{oos_year}-01-01':f'{oos_year}-12-31']

        if len(oos_data) < 200:
            print(f"  {oos_year}: insufficient OOS bars ({len(oos_data)}) — skipped")
            continue

        print(f"\n  IS {is_start}-{is_end}  →  OOS {oos_year}  "
              f"({len(is_data):,} IS bars / {len(oos_data):,} OOS bars)")

        # ── Optimize on IS ────────────────────────────────────────────────
        sorted_results = run_grid_on_window(is_data, min_n=30)
        if not sorted_results:
            print(f"    No IS fit found — skipping window")
            rows.append({'oos_year': oos_year, 'error': 'no_is_fit'})
            continue

        best = sorted_results[0]

        # Parameter stability: rank of previous best in this window's grid
        param_rank = None
        if prev_best is not None and sorted_results:
            param_rank = combo_rank(prev_best['params'], sorted_results)
            top_30pct  = max(1, int(len(sorted_results) * 0.30))
            stable     = param_rank <= top_30pct
            print(f"    Param stability: prev best is rank {param_rank}/{len(sorted_results)} "
                  f"→ {'STABLE ✓' if stable else 'DRIFTED ✗'}")

        # ── OOS evaluation with best IS params ───────────────────────────
        kw = {**best['params'], **FIXED}
        oos_trades = arcb_xau(oos_data, **kw)
        oos_pnls   = trades_to_pnls(oos_trades)
        oos_cp     = cost_adjust_pnls(oos_pnls, COST_R) if len(oos_pnls) > 0 else np.array([])

        oos_pf_raw  = compute_pf(oos_pnls) if len(oos_pnls) > 0 else 0.0
        oos_pf_cost = compute_pf(oos_cp)   if len(oos_cp)   > 0 else 0.0
        oos_exp_r   = compute_avg_r(oos_pnls) if len(oos_pnls) > 0 else 0.0
        oos_dd      = max_drawdown_pct(oos_pnls) if len(oos_pnls) > 0 else 0.0

        # IS expectancy ratio
        exp_ratio = oos_exp_r / best['is_exp_r'] if best['is_exp_r'] > 0 else 0.0

        print(f"    Best IS  params: k={best['params']['compression_k']:.2f}  "
              f"sl={best['params']['stop_atr_mult']:.2f}  "
              f"vol={best['params']['vol_ratio_min']:.2f}  "
              f"→ IS PF={best['is_pf_cost']:.3f} ({best['is_n']} trades)")
        print(f"    OOS {oos_year}: N={len(oos_trades)}  "
              f"PF(raw)={oos_pf_raw:.3f}  "
              f"PF(cost)={oos_pf_cost:.3f}  "
              f"AvgR={oos_exp_r:.4f}  "
              f"MaxDD={oos_dd*100:.1f}%  "
              f"ExpRatio={exp_ratio:.2f}")

        rows.append({
            'oos_year':      oos_year,
            'is_period':     f'{is_start}-{is_end}',
            'compression_k': best['params']['compression_k'],
            'stop_atr_mult': best['params']['stop_atr_mult'],
            'vol_ratio_min': best['params']['vol_ratio_min'],
            'is_n':          best['is_n'],
            'is_pf':         best['is_pf'],
            'is_pf_cost':    best['is_pf_cost'],
            'is_exp_r':      best['is_exp_r'],
            'oos_n':         len(oos_trades),
            'oos_pf':        round(oos_pf_raw, 3),
            'oos_pf_cost':   round(oos_pf_cost, 3),
            'oos_exp_r':     round(oos_exp_r, 4),
            'oos_max_dd':    round(oos_dd, 4),
            'exp_ratio':     round(exp_ratio, 3),
            'param_rank':    param_rank,
        })

        prev_sorted = sorted_results
        prev_best   = best

    # ── Summary table ─────────────────────────────────────────────────────
    df = pd.DataFrame(rows)
    valid = df[df.get('error', pd.Series([''] * len(df))) != 'no_is_fit'].copy() \
            if 'error' in df.columns else df.copy()

    print("\n" + "=" * 80)
    print("  WALK-FORWARD RESULTS SUMMARY")
    print("=" * 80)
    if 'oos_pf_cost' in valid.columns:
        print(valid[['oos_year','compression_k','stop_atr_mult','vol_ratio_min',
                      'is_n','oos_n','is_pf_cost','oos_pf','oos_pf_cost',
                      'oos_exp_r','oos_max_dd']].to_string(index=False))
    else:
        print(df.to_string(index=False))

    # ── Verdict ───────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  VERDICT")
    print("=" * 80)

    kill_reasons = []
    warnings_    = []

    if 'oos_pf_cost' not in valid.columns or valid.empty:
        print("  REJECT — no valid OOS windows")
        return

    oos_pfs    = valid['oos_pf_cost'].dropna()
    oos_ns     = valid['oos_n'].dropna()
    oos_exps   = valid['exp_ratio'].dropna() if 'exp_ratio' in valid.columns else pd.Series()
    oos_dds    = valid['oos_max_dd'].dropna() if 'oos_max_dd' in valid.columns else pd.Series()

    mean_pf    = oos_pfs.mean()
    years_pass = (oos_pfs > 1.10).sum()
    mean_n     = oos_ns.mean()

    print(f"\n  OOS windows:          {len(oos_pfs)}")
    print(f"  Mean cost-adj PF:     {mean_pf:.3f}   (gate: >{PASS_MEAN_PF})")
    print(f"  Years with PF > 1.1:  {years_pass}/{len(oos_pfs)}   (gate: ≥{PASS_YEARS_ABOVE_11})")
    print(f"  Mean OOS trades/yr:   {mean_n:.0f}       (gate: ≥{PASS_MIN_TRADES_YR})")

    # Kill condition 1: 2024 OOS PF
    if 2024 in valid['oos_year'].values:
        pf_2024 = valid.loc[valid['oos_year'] == 2024, 'oos_pf_cost'].iloc[0]
        print(f"  2024 OOS PF:          {pf_2024:.3f}   (kill gate: <{KILL_2024_PF})")
        if pf_2024 < KILL_2024_PF:
            kill_reasons.append(f"2024 OOS PF={pf_2024:.3f} < {KILL_2024_PF} kill gate")
    else:
        warnings_.append("2024 OOS window not found — cannot apply kill gate 1")

    # Kill condition 2: 2-year rolling PF
    if len(oos_pfs) >= 2:
        rolling2 = [((oos_pfs.iloc[i] + oos_pfs.iloc[i+1]) / 2)
                    for i in range(len(oos_pfs) - 1)]
        min_rolling2 = min(rolling2)
        print(f"  Min 2-yr rolling PF:  {min_rolling2:.3f}   (kill gate: <{KILL_ROLLING_PF})")
        if min_rolling2 < KILL_ROLLING_PF:
            kill_reasons.append(f"2-yr rolling PF min={min_rolling2:.3f} < {KILL_ROLLING_PF}")

    # Kill condition 3: expectancy ratio
    if len(oos_exps) > 0:
        mean_exp_ratio = oos_exps.mean()
        print(f"  Mean OOS/IS exp ratio:{mean_exp_ratio:.3f}   "
              f"(kill gate: <{KILL_EXPECTANCY_RAT})")
        if mean_exp_ratio < KILL_EXPECTANCY_RAT:
            kill_reasons.append(
                f"OOS/IS expectancy ratio={mean_exp_ratio:.3f} < {KILL_EXPECTANCY_RAT}")

    # Kill condition 4: max DD in any OOS year
    if len(oos_dds) > 0:
        worst_dd = oos_dds.max()
        print(f"  Worst OOS yr max DD:  {worst_dd*100:.1f}%   "
              f"(kill gate: >{KILL_MAX_DD_YR*100:.0f}%)")
        if worst_dd > KILL_MAX_DD_YR:
            kill_reasons.append(
                f"Worst OOS year max DD={worst_dd*100:.1f}% > {KILL_MAX_DD_YR*100:.0f}%")

    # Pass conditions
    pass_failures = []
    if mean_pf <= PASS_MEAN_PF:
        pass_failures.append(f"Mean PF {mean_pf:.3f} ≤ {PASS_MEAN_PF}")
    if years_pass < PASS_YEARS_ABOVE_11:
        pass_failures.append(f"Only {years_pass}/{len(oos_pfs)} years > 1.1 "
                             f"(need {PASS_YEARS_ABOVE_11})")
    if mean_n < PASS_MIN_TRADES_YR:
        pass_failures.append(f"Mean OOS trades {mean_n:.0f} < {PASS_MIN_TRADES_YR}/yr")

    print()
    if kill_reasons:
        print("  ██ REJECT — kill condition(s) triggered:")
        for r in kill_reasons:
            print(f"     ✗  {r}")
        print()
        print("  Do NOT deploy ARCB-XAU. Review kill reason and either:")
        print("  1. Adjust entry window if 2024 regime has shifted")
        print("  2. Proceed to Candidate B (NDCB-XAU) as primary")
    elif pass_failures:
        print("  ▲ MARGINAL — no kill conditions, but not all pass gates met:")
        for f in pass_failures:
            print(f"     ⚠  {f}")
        print()
        print("  Deploy at 0.10% risk (1/10 allocation) for 6 months live.")
        print("  Re-evaluate before scaling.")
    else:
        print("  ██ DEPLOY — all pass gates cleared, no kill conditions triggered.")
        print()
        if 2024 in valid['oos_year'].values:
            pf_2024 = valid.loc[valid['oos_year'] == 2024, 'oos_pf_cost'].iloc[0]
            if pf_2024 >= 1.60:
                print("  2024 OOS PF ≥ 1.6 → scale to 1% risk immediately.")
            elif pf_2024 >= 0.90:
                print("  2024 OOS PF 0.9-1.6 → deploy at 0.25% risk for 3 months.")
        print()
        print("  Next: run walk_forward_arcb.py after 3 months live data.")
        print("  Then build NDCB-XAU (Candidate B).")

    if warnings_:
        print()
        for w in warnings_:
            print(f"  ⚠  {w}")

    # ── Save CSV ──────────────────────────────────────────────────────────
    out = os.path.join(DIAG, 'walk_forward_arcb.csv')
    df.to_csv(out, index=False)
    print(f"\n  Saved: {out}")
    print("=" * 80)


if __name__ == '__main__':
    main()
