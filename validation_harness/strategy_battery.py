"""
Strategy Battery — Systematic XAUUSD Edge Research
====================================================
Runs every strategy in strategy_lib.STRATEGIES through:
  1. IS parameter grid sweep
  2. Walk-forward OOS validation with IS-best params
  3. Robustness score (PF stability across neighborhood)
  4. Cost-adjusted PF (XAUUSD-realistic spread + slippage + commission)
  5. Hard reject gates (N>=100 OOS, PF>=1.3, robustness>=0.6)

Output: diagnostic/strategy_battery_results.csv ranked by cost-adjusted OOS PF.

Usage:
  python validation_harness/strategy_battery.py
  python validation_harness/strategy_battery.py --strategy triple_screen --verbose

Hard rules baked in:
  - OOS N < 100 → REJECT (no more N=34 traps)
  - OOS PF (cost-adj) < 1.3 → REJECT
  - Robustness < 0.6 → REJECT (cherry-pick)
  - Max DD > 15% → REJECT
"""
import os
import sys
import argparse
import itertools
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Trade, load_mt5_csv
from strategy_lib import STRATEGIES, NOTIONAL_RISK


# ─── COST MODEL ───────────────────────────────────────────────────────────────
# XAUUSD realistic execution: 40c spread + 20c slippage (10c/side) + 2% R commission
# On a 1.5*ATR(14) stop ≈ $20-25 stop distance, that's ~3% of R per trade
SPREAD_PER_TRADE = 0.40
SLIPPAGE_PER_TRADE = 0.20
COMMISSION_R_FRAC = 0.02


def cost_per_trade_R(stop_dist_dollars: float = 22.0) -> float:
    """Cost per round-trip in R units (where R = stop distance in $)."""
    if stop_dist_dollars <= 0:
        return 0.05
    return (SPREAD_PER_TRADE + SLIPPAGE_PER_TRADE) / stop_dist_dollars + COMMISSION_R_FRAC


# ─── METRICS ──────────────────────────────────────────────────────────────────

def trades_to_pnls(trades: list) -> np.ndarray:
    return np.array([t.pnl for t in trades]) if trades else np.array([])


def compute_pf(pnls: np.ndarray) -> float:
    if len(pnls) == 0:
        return 0.0
    gp = pnls[pnls > 0].sum()
    gl = abs(pnls[pnls < 0].sum())
    return gp / gl if gl > 0 else float('inf')


def compute_avg_r(pnls: np.ndarray) -> float:
    if len(pnls) == 0:
        return 0.0
    return pnls.mean() / NOTIONAL_RISK


def compute_max_dd_pct(pnls: np.ndarray, start_equity: float = 50000.0) -> float:
    if len(pnls) == 0:
        return 0.0
    eq = start_equity + np.cumsum(pnls)
    eq = np.concatenate([[start_equity], eq])
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    return float(dd.max() * 100)


def cost_adjust_pnls(pnls: np.ndarray, cost_R: float = 0.05) -> np.ndarray:
    """Subtract cost in R-units (cost_R * NOTIONAL_RISK) from each trade."""
    if len(pnls) == 0:
        return pnls
    return pnls - cost_R * NOTIONAL_RISK


# ─── PARAMETER GRIDS ──────────────────────────────────────────────────────────
# Small grids per strategy — enough for robustness analysis without combinatorial blow-up.
# Format: param_name -> list of values to test. Cartesian product = grid points.

PARAM_GRIDS: dict = {
    'donchian': {
        'lookback':  [10, 15, 20, 30],
        'sl_mult':   [1.5, 2.0],
        'tp_mult':   [2.0, 3.0],
    },
    'ema_cross': {
        'fast':     [10, 20],
        'slow':     [30, 50],
        'adx_min':  [20.0, 25.0],
        'sl_mult':  [1.5, 2.0],
        'tp_mult':  [2.0, 3.0],
    },
    'triple_screen': {
        'd1_ema_period':   [20, 50],
        'h4_rsi_long_max': [35.0, 40.0, 45.0],
        'sl_mult':         [1.0, 1.5, 2.0],
        'tp_mult':         [2.0, 3.0],
    },
    'keltner': {
        'ema_period': [10, 20, 30],
        'band_mult':  [1.5, 2.0, 2.5],
        'sl_mult':    [1.0, 1.5],
        'tp_mult':    [2.0, 3.0],
    },
    'bb_squeeze': {
        'bb_period':         [15, 20],
        'squeeze_lookback':  [40, 60, 80],
        'sl_mult':           [1.0, 1.5],
        'tp_mult':           [2.0, 3.0],
    },
    'nr7': {
        'lookback':     [5, 7, 10],
        'sl_atr_mult':  [1.0, 1.5],
        'tp_atr_mult':  [2.0, 3.0],
    },
    'opening_range': {
        'range_start':   [6, 7],
        'range_end':     [8, 9],
        'sl_atr_mult':   [0.5, 1.0],
        'tp_atr_mult':   [1.5, 2.0],
    },
    'london_open': {
        'london_hour':    [7, 8, 9],
        'min_range_atr':  [0.3, 0.5, 0.7],
        'sl_atr_mult':    [1.0, 1.5],
        'tp_atr_mult':    [2.0, 3.0],
    },
    'gold_am_fix': {
        'range_start':    [9, 10],
        'range_end':      [11, 12],
        'sl_atr_mult':    [0.5, 1.0],
        'tp_atr_mult':    [2.0, 2.5],
    },
    'ny_london_overlap': {
        'overlap_start':  [12, 13],
        'momentum_atr':   [0.7, 1.0, 1.3],
        'sl_atr_mult':    [0.7, 1.0],
        'tp_atr_mult':    [1.5, 2.0],
    },
    'rsi2_connors': {
        'oversold':         [5, 10, 15],
        'trend_ma_period':  [100, 200],
        'sl_atr_mult':      [1.5, 2.0],
        'tp_atr_mult':      [1.0, 1.5],
    },
    'bb_reversion': {
        'bb_period':         [15, 20],
        'bb_std':            [1.8, 2.0, 2.2],
        'sl_atr_mult':       [1.5, 2.0],
        'trend_ema_period':  [100, 200],
    },
}


def grid_iter(grid: dict):
    """Yield dicts for each cartesian-product point in a parameter grid."""
    keys = list(grid.keys())
    for combo in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, combo))


# ─── WALK-FORWARD ─────────────────────────────────────────────────────────────

@dataclass
class StrategyVerdict:
    name: str
    family: str
    best_params: dict
    is_pf: float
    is_n: int
    oos_pf: float
    oos_n: int
    oos_pf_cost: float
    oos_avg_r: float
    oos_max_dd_pct: float
    robustness_score: float        # frac of grid points with PF >= 0.7 * best_IS_PF
    grid_size: int
    rejected: bool = False
    failures: list = field(default_factory=list)


def evaluate_strategy(name: str, fn: Callable, family: str, h1: pd.DataFrame,
                      grid: dict, is_fraction: float = 0.7,
                      verbose: bool = False) -> StrategyVerdict:
    """Run grid sweep on IS, take best params, evaluate on OOS."""
    n_bars = len(h1)
    split = int(n_bars * is_fraction)
    is_data = h1.iloc[:split].copy()
    oos_data = h1.iloc[split:].copy()

    if verbose:
        print(f"\n  IS:  {is_data.index[0].date()} -> {is_data.index[-1].date()} ({len(is_data)} bars)")
        print(f"  OOS: {oos_data.index[0].date()} -> {oos_data.index[-1].date()} ({len(oos_data)} bars)")

    # Grid sweep on IS
    grid_results = []
    for params in grid_iter(grid):
        try:
            trades = fn(is_data, **params)
        except Exception as e:
            if verbose:
                print(f"    {params} -> ERROR: {e}")
            continue
        pnls = trades_to_pnls(trades)
        pf = compute_pf(pnls)
        grid_results.append((params, pf, len(trades)))
        if verbose:
            print(f"    {params} -> N={len(trades):>5} PF={pf:5.2f}")

    if not grid_results:
        return StrategyVerdict(
            name=name, family=family, best_params={}, is_pf=0, is_n=0, oos_pf=0, oos_n=0,
            oos_pf_cost=0, oos_avg_r=0, oos_max_dd_pct=0,
            robustness_score=0, grid_size=0, rejected=True,
            failures=['no grid results'],
        )

    grid_results.sort(key=lambda x: x[1], reverse=True)
    best_params, best_is_pf, best_is_n = grid_results[0]

    # Robustness: fraction of grid points with PF >= 0.7 * best_IS_PF
    pf_threshold = 0.7 * best_is_pf
    robust_count = sum(1 for _, pf, _ in grid_results if pf >= pf_threshold)
    robustness = robust_count / len(grid_results)

    # OOS run with best params
    oos_trades = fn(oos_data, **best_params)
    oos_pnls = trades_to_pnls(oos_trades)
    oos_pf = compute_pf(oos_pnls)
    oos_n = len(oos_trades)
    oos_avg_r = compute_avg_r(oos_pnls)
    oos_max_dd = compute_max_dd_pct(oos_pnls)

    # Cost-adjusted OOS PF
    cost_R = cost_per_trade_R()
    oos_pnls_cost = cost_adjust_pnls(oos_pnls, cost_R)
    oos_pf_cost = compute_pf(oos_pnls_cost)

    # Hard gates
    failures = []
    if oos_n < 100:
        failures.append(f'OOS N={oos_n} < 100')
    if oos_pf_cost < 1.3:
        failures.append(f'OOS cost-adj PF={oos_pf_cost:.2f} < 1.3')
    if robustness < 0.6:
        failures.append(f'Robustness={robustness:.2f} < 0.6 (cherry-pick risk)')
    if oos_max_dd > 15.0:
        failures.append(f'Max DD={oos_max_dd:.1f}% > 15%')

    return StrategyVerdict(
        name=name, family=family, best_params=best_params,
        is_pf=best_is_pf, is_n=best_is_n,
        oos_pf=oos_pf, oos_n=oos_n, oos_pf_cost=oos_pf_cost,
        oos_avg_r=oos_avg_r, oos_max_dd_pct=oos_max_dd,
        robustness_score=robustness, grid_size=len(grid_results),
        rejected=bool(failures), failures=failures,
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run_battery(symbol: str = 'XAUUSD', start: str = '2014-01-01',
                end: str = '2024-12-31', is_fraction: float = 0.7,
                only_strategy: Optional[str] = None, verbose: bool = False) -> pd.DataFrame:
    DATA = os.path.join(os.path.dirname(__file__), '..', 'data',
                         f'{symbol}_H1_2013-2025.csv')
    if not os.path.exists(DATA):
        # try paired data
        from harness import load_mt5_csv_pair
        h1 = load_mt5_csv_pair(os.path.join(os.path.dirname(__file__), '..', 'data'), symbol)
    else:
        h1 = load_mt5_csv(DATA)
    h1 = h1.loc[start:end]
    print(f"\n{'='*72}")
    print(f"  STRATEGY BATTERY — {symbol}  {h1.index[0].date()} -> {h1.index[-1].date()}")
    print(f"  {len(h1)} H1 bars  |  IS={int(is_fraction*100)}%  OOS={int((1-is_fraction)*100)}%")
    print(f"  Cost model: spread={SPREAD_PER_TRADE} + slip={SLIPPAGE_PER_TRADE} + comm={COMMISSION_R_FRAC*100:.0f}%R "
          f"≈ {cost_per_trade_R():.3f}R/trade")
    print(f"{'='*72}\n")

    verdicts = []
    for name, meta in STRATEGIES.items():
        if only_strategy and name != only_strategy:
            continue
        grid = PARAM_GRIDS[name]
        n_grid = 1
        for v in grid.values():
            n_grid *= len(v)
        t0 = time.time()
        print(f"[{name}] family={meta['family']} grid={n_grid} points...")
        v = evaluate_strategy(name, meta['fn'], meta['family'], h1, grid,
                               is_fraction=is_fraction, verbose=verbose)
        dt = time.time() - t0
        verdicts.append(v)
        status = '[REJECT]' if v.rejected else '[PASS]  '
        print(f"  {status} IS_PF={v.is_pf:5.2f} (N={v.is_n})  "
              f"OOS_PF={v.oos_pf:5.2f} (N={v.oos_n})  "
              f"OOS_cost={v.oos_pf_cost:5.2f}  "
              f"AvgR={v.oos_avg_r:+.3f}  DD={v.oos_max_dd_pct:.1f}%  "
              f"Rob={v.robustness_score:.2f}  ({dt:.0f}s)")
        if v.failures:
            for f in v.failures:
                print(f"      - {f}")
        print(f"      best params: {v.best_params}\n")

    # Build DataFrame and sort
    rows = []
    for v in verdicts:
        rows.append({
            'strategy': v.name,
            'family': v.family,
            'is_pf': round(v.is_pf, 3),
            'is_n': v.is_n,
            'oos_pf': round(v.oos_pf, 3),
            'oos_pf_cost': round(v.oos_pf_cost, 3),
            'oos_n': v.oos_n,
            'oos_avg_r': round(v.oos_avg_r, 4),
            'oos_max_dd_pct': round(v.oos_max_dd_pct, 2),
            'robustness': round(v.robustness_score, 3),
            'grid_size': v.grid_size,
            'rejected': v.rejected,
            'failures': '; '.join(v.failures),
            'best_params': str(v.best_params),
        })
    df = pd.DataFrame(rows).sort_values('oos_pf_cost', ascending=False).reset_index(drop=True)

    out_dir = os.path.join(os.path.dirname(__file__), '..', 'diagnostic')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'strategy_battery_results.csv')
    df.to_csv(out_path, index=False)
    print(f"\n{'='*72}")
    print(f"  RESULTS SAVED: {out_path}")
    print(f"{'='*72}")
    print(df.to_string(index=False))
    print()
    survivors = df[~df['rejected']]
    print(f"  SURVIVORS (passed all gates): {len(survivors)}/{len(df)}")
    if len(survivors) > 0:
        print(f"  Top survivor: {survivors.iloc[0]['strategy']} "
              f"(OOS cost-adj PF={survivors.iloc[0]['oos_pf_cost']})")
    return df


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol',   default='XAUUSD')
    parser.add_argument('--start',    default='2014-01-01')
    parser.add_argument('--end',      default='2024-12-31')
    parser.add_argument('--is-frac',  type=float, default=0.7)
    parser.add_argument('--strategy', default=None,
                        help='Run only this strategy (skip others)')
    parser.add_argument('--verbose',  action='store_true')
    args = parser.parse_args()
    run_battery(args.symbol, args.start, args.end, args.is_frac,
                args.strategy, args.verbose)
