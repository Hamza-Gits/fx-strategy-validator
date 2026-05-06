"""
Phase C — Deep Validation for Surviving Strategies
====================================================
Inputs: a strategy name + best params (from battery)
Tests: multi-instrument, regime split, Monte Carlo trade resampling, news filter

A real edge survives all four. Overfit edges fail at least one.

Usage:
  python validation_harness/strategy_deep_validate.py \\
      --strategy triple_screen --params "{'d1_ema_period':50,'sl_mult':1.5,'tp_mult':3.0}"

  # Or auto-pick top 3 survivors from battery results:
  python validation_harness/strategy_deep_validate.py --auto-top 3
"""
import os
import sys
import argparse
import ast
import json
from typing import Optional
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_mt5_csv, load_mt5_csv_pair
from strategy_lib import STRATEGIES, NOTIONAL_RISK
from strategy_battery import (compute_pf, compute_avg_r, compute_max_dd_pct,
                               trades_to_pnls, cost_per_trade_R, cost_adjust_pnls)


DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
INSTRUMENTS = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY']


# ─── 1. MULTI-INSTRUMENT CROSS-CHECK ──────────────────────────────────────────

def multi_instrument_test(strategy_name: str, params: dict,
                           start: str = '2014-01-01', end: str = '2024-12-31',
                           is_fraction: float = 0.7) -> pd.DataFrame:
    """Run same strategy + params on each instrument. True edge generalizes."""
    fn = STRATEGIES[strategy_name]['fn']
    rows = []
    for sym in INSTRUMENTS:
        try:
            if sym == 'XAUUSD':
                fp = os.path.join(DATA_DIR, 'XAUUSD_H1_2013-2025.csv')
                h1 = load_mt5_csv(fp)
            else:
                h1 = load_mt5_csv_pair(DATA_DIR, sym)
            h1 = h1.loc[start:end]
        except Exception as e:
            rows.append({'symbol': sym, 'error': str(e)})
            continue
        split = int(len(h1) * is_fraction)
        is_data, oos_data = h1.iloc[:split], h1.iloc[split:]
        for label, data in [('IS', is_data), ('OOS', oos_data)]:
            try:
                trades = fn(data, **params)
            except Exception as e:
                rows.append({'symbol': sym, 'period': label, 'error': str(e)})
                continue
            pnls = trades_to_pnls(trades)
            if len(pnls) == 0:
                rows.append({'symbol': sym, 'period': label, 'n': 0, 'pf': 0,
                             'avg_r': 0, 'wr': 0, 'pf_cost': 0})
                continue
            cost_pnls = cost_adjust_pnls(pnls, cost_per_trade_R())
            rows.append({
                'symbol': sym, 'period': label,
                'n': len(trades),
                'pf': round(compute_pf(pnls), 3),
                'pf_cost': round(compute_pf(cost_pnls), 3),
                'avg_r': round(compute_avg_r(pnls), 4),
                'wr': round((pnls > 0).mean() * 100, 1),
            })
    return pd.DataFrame(rows)


# ─── 2. REGIME SPLIT (gold trend bull/bear/range) ─────────────────────────────

def regime_split_test(strategy_name: str, params: dict,
                       start: str = '2014-01-01', end: str = '2024-12-31') -> pd.DataFrame:
    """Segment OOS trades by D1-200MA regime: bull (price>MA, slope+),
    bear (price<MA, slope-), range (otherwise). True edge works in 2+ regimes."""
    fn = STRATEGIES[strategy_name]['fn']
    fp = os.path.join(DATA_DIR, 'XAUUSD_H1_2013-2025.csv')
    h1 = load_mt5_csv(fp).loc[start:end]
    # OOS portion only
    split = int(len(h1) * 0.7)
    oos = h1.iloc[split:].copy()

    # Regime classification on D1 200-MA
    d1 = oos.resample('1D').agg({'open':'first','high':'max','low':'min',
                                  'close':'last'}).dropna()
    d1['ma'] = d1['close'].rolling(200, min_periods=50).mean()
    d1['ma_slope'] = d1['ma'].diff(10)
    def classify(row):
        if pd.isna(row['ma']):
            return 'unknown'
        if row['close'] > row['ma'] and row['ma_slope'] > 0:
            return 'bull'
        if row['close'] < row['ma'] and row['ma_slope'] < 0:
            return 'bear'
        return 'range'
    d1['regime'] = d1.apply(classify, axis=1)
    regime_h1 = d1['regime'].reindex(oos.index, method='ffill')

    trades = fn(oos, **params)
    rows = []
    by_regime = {'bull': [], 'bear': [], 'range': [], 'unknown': []}
    for t in trades:
        r = regime_h1.loc[t.entry_time] if t.entry_time in regime_h1.index else 'unknown'
        by_regime[r].append(t)
    for regime, ts in by_regime.items():
        pnls = trades_to_pnls(ts)
        if len(pnls) == 0:
            rows.append({'regime': regime, 'n': 0, 'pf': 0, 'avg_r': 0, 'wr': 0})
            continue
        rows.append({
            'regime': regime, 'n': len(ts),
            'pf': round(compute_pf(pnls), 3),
            'avg_r': round(compute_avg_r(pnls), 4),
            'wr': round((pnls > 0).mean() * 100, 1),
        })
    return pd.DataFrame(rows)


# ─── 3. MONTE CARLO TRADE RESAMPLING ──────────────────────────────────────────

def monte_carlo_test(strategy_name: str, params: dict, n_resamples: int = 1000,
                      start: str = '2014-01-01', end: str = '2024-12-31') -> dict:
    """Bootstrap OOS trade sequence 1000x. Report 5th-percentile equity curve final
    and worst drawdown distribution. True edge: 5th-pctile final > start equity."""
    fn = STRATEGIES[strategy_name]['fn']
    fp = os.path.join(DATA_DIR, 'XAUUSD_H1_2013-2025.csv')
    h1 = load_mt5_csv(fp).loc[start:end]
    split = int(len(h1) * 0.7)
    oos = h1.iloc[split:]
    trades = fn(oos, **params)
    pnls = trades_to_pnls(trades)
    if len(pnls) < 30:
        return {'error': f'OOS N={len(pnls)} too small for MC'}
    cost_pnls = cost_adjust_pnls(pnls, cost_per_trade_R())
    start_eq = 50000.0
    finals = []
    max_dds = []
    rng = np.random.default_rng(42)
    for _ in range(n_resamples):
        s = rng.choice(cost_pnls, size=len(cost_pnls), replace=True)
        eq = np.concatenate([[start_eq], start_eq + np.cumsum(s)])
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / peak
        finals.append(eq[-1])
        max_dds.append(float(dd.max() * 100))
    finals = np.array(finals); max_dds = np.array(max_dds)
    return {
        'n_trades': len(pnls),
        'cost_adj_pf': round(compute_pf(cost_pnls), 3),
        'final_p05': round(float(np.percentile(finals, 5)), 0),
        'final_p50': round(float(np.percentile(finals, 50)), 0),
        'final_p95': round(float(np.percentile(finals, 95)), 0),
        'max_dd_p50_pct': round(float(np.percentile(max_dds, 50)), 2),
        'max_dd_p95_pct': round(float(np.percentile(max_dds, 95)), 2),
        'pct_runs_above_start': round(float((finals > start_eq).mean() * 100), 1),
    }


# ─── 4. NEWS-EVENT FILTER (NFP first Friday + month-start FOMC week) ──────────

def is_nfp_first_friday(ts) -> bool:
    """First Friday of each month — NFP release window."""
    if ts.weekday() != 4:  # Friday
        return False
    return ts.day <= 7


def is_fomc_window(ts) -> bool:
    """Approximate FOMC weeks: 6 dates per year, Tue-Wed. Without real calendar
    data, skip Tue/Wed of weeks 3-4 of Mar/May/Jun/Jul/Sep/Nov as a proxy."""
    fomc_months = [1, 3, 5, 6, 7, 9, 11, 12]
    if ts.month not in fomc_months:
        return False
    if ts.weekday() not in (1, 2):
        return False
    return 14 <= ts.day <= 24


def news_filter_test(strategy_name: str, params: dict,
                      start: str = '2014-01-01', end: str = '2024-12-31') -> dict:
    """Compare OOS PF with vs without news-window trades. If filter improves PF,
    edge depends on news drift — fragile. If neutral, edge is structural."""
    fn = STRATEGIES[strategy_name]['fn']
    fp = os.path.join(DATA_DIR, 'XAUUSD_H1_2013-2025.csv')
    h1 = load_mt5_csv(fp).loc[start:end]
    split = int(len(h1) * 0.7)
    oos = h1.iloc[split:]
    trades = fn(oos, **params)
    pnls_full = trades_to_pnls(trades)
    pnls_filtered = trades_to_pnls([t for t in trades
                                     if not (is_nfp_first_friday(t.entry_time)
                                             or is_fomc_window(t.entry_time))])
    cost_R = cost_per_trade_R()
    return {
        'n_full': len(trades),
        'pf_full': round(compute_pf(cost_adjust_pnls(pnls_full, cost_R)), 3),
        'n_filtered': len(pnls_filtered),
        'pf_filtered': round(compute_pf(cost_adjust_pnls(pnls_filtered, cost_R)), 3),
        'pct_news_trades': round(100 * (len(trades) - len(pnls_filtered)) / max(len(trades), 1), 1),
    }


# ─── DRIVER ───────────────────────────────────────────────────────────────────

def deep_validate(strategy_name: str, params: dict) -> dict:
    print(f"\n{'='*72}")
    print(f"  DEEP VALIDATION — {strategy_name}")
    print(f"  Params: {params}")
    print(f"{'='*72}")

    print("\n[1/4] Multi-instrument cross-check (XAUUSD + EUR/GBP/JPY)...")
    mi = multi_instrument_test(strategy_name, params)
    print(mi.to_string(index=False))

    print("\n[2/4] Regime split (XAUUSD OOS by D1-200MA bull/bear/range)...")
    rs = regime_split_test(strategy_name, params)
    print(rs.to_string(index=False))

    print("\n[3/4] Monte Carlo trade resampling (1000 bootstraps, OOS, cost-adj)...")
    mc = monte_carlo_test(strategy_name, params, n_resamples=1000)
    for k, v in mc.items():
        print(f"  {k:<25} {v}")

    print("\n[4/4] News-window filter (NFP Fri + FOMC Tue/Wed exclusion)...")
    nf = news_filter_test(strategy_name, params)
    for k, v in nf.items():
        print(f"  {k:<25} {v}")

    # Verdict
    print(f"\n{'-'*72}")
    failures = []
    # Multi-instrument: at least 2 of 4 OOS cost-adj PF > 1.2
    oos_rows = mi[mi.get('period', '') == 'OOS']
    if 'pf_cost' in oos_rows.columns:
        oos_passing = (oos_rows['pf_cost'] > 1.2).sum()
        if oos_passing < 2:
            failures.append(f'multi-instrument: only {oos_passing}/4 OOS PF>1.2')
    # Regime: at least 2 of 3 (bull/bear/range) PF > 1.0
    real_regimes = rs[rs['regime'].isin(['bull', 'bear', 'range']) & (rs['n'] >= 5)]
    regime_pass = (real_regimes['pf'] > 1.0).sum() if len(real_regimes) > 0 else 0
    if regime_pass < 2:
        failures.append(f'regime: only {regime_pass} regime(s) with PF>1.0')
    # MC: 5th percentile final > start equity
    if 'final_p05' in mc and mc.get('final_p05', 0) < 50000:
        failures.append(f"MC 5th-pctile final ${mc['final_p05']} < $50k start")
    # News filter: trades_after_filter / trades_before > 0.85 (not >15% news-trade dependent)
    if nf['pct_news_trades'] > 15:
        failures.append(f"news-trade share {nf['pct_news_trades']}% > 15% (fragile)")

    if failures:
        print(f"  VERDICT: REJECT — {len(failures)} gate(s) failed")
        for f in failures:
            print(f"    - {f}")
    else:
        print(f"  VERDICT: PROMOTE TO PHASE D (MQL5 build)")
    print(f"{'-'*72}\n")

    return {
        'strategy': strategy_name, 'params': params,
        'multi_instrument': mi.to_dict(orient='records'),
        'regime_split': rs.to_dict(orient='records'),
        'monte_carlo': mc, 'news_filter': nf,
        'rejected': bool(failures),
        'failures': failures,
    }


def auto_top_from_battery(top_n: int = 3) -> list:
    """Read battery results CSV, return top N non-rejected strategies."""
    path = os.path.join(os.path.dirname(__file__), '..', 'diagnostic',
                         'strategy_battery_results.csv')
    if not os.path.exists(path):
        print(f"ERROR: battery results not found at {path}")
        sys.exit(1)
    df = pd.read_csv(path)
    survivors = df[~df['rejected']].head(top_n)
    if len(survivors) == 0:
        print("WARNING: no battery survivors — picking top N by OOS cost-adj PF anyway")
        survivors = df.head(top_n)
    out = []
    for _, r in survivors.iterrows():
        out.append((r['strategy'], ast.literal_eval(r['best_params'])))
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--strategy', default=None)
    parser.add_argument('--params',   default=None,
                        help='Python literal dict, e.g. "{\'sl_mult\':1.5}"')
    parser.add_argument('--auto-top', type=int, default=0,
                        help='Auto-pick top N survivors from battery results')
    args = parser.parse_args()

    if args.auto_top:
        candidates = auto_top_from_battery(args.auto_top)
    elif args.strategy and args.params:
        candidates = [(args.strategy, ast.literal_eval(args.params))]
    else:
        parser.error('Provide --strategy + --params, or --auto-top N')

    all_results = []
    for name, params in candidates:
        r = deep_validate(name, params)
        all_results.append(r)

    out_path = os.path.join(os.path.dirname(__file__), '..', 'diagnostic',
                             'deep_validation_results.json')
    with open(out_path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"Saved: {out_path}")
