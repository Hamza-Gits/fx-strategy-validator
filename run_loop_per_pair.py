"""
Per-Pair Parameter Grid Search (Council Gate 1+2 follow-up)
============================================================
The pooled audit revealed only GBPUSD has cost-adjusted edge across both
periods. EUR and JPY may need different parameters. This script searches
the parameter grid SEPARATELY for each pair, applies realistic costs
(1.5 pip spread + 0.5 pip slippage), and reports the best params per pair.

For each pair, tests both Period A (2015-19) and Period B (2020-24).
A pair PASSES only if BOTH periods pass net of costs at the same params.

Output:
  results/per_pair_best.json — best params per pair (or "no edge found")
  results/per_pair_log.md     — full iteration log
"""

import sys
import os
import json
import re
import argparse
from datetime import datetime, timezone
from itertools import product

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf-16'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 'validation_harness'))
from harness import StrategyResult, Trade, run_validation, GateConfig, load_mt5_csv_pair
from strategy_london_breakout import _run_single_symbol
from audit_per_pair import apply_cost_to_trades

REPO_ROOT   = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(REPO_ROOT, 'results')
LOG_FILE    = os.path.join(RESULTS_DIR, 'per_pair_log.md')
BEST_FILE   = os.path.join(RESULTS_DIR, 'per_pair_best.json')
DATA_DIR    = os.path.join(REPO_ROOT, 'data')

PERIOD_A = ('2015-01-01', '2019-12-31')
PERIOD_B = ('2020-01-01', '2024-12-31')

SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY']

# Parameter grid (5 x 4 x 4 = 80 combos)
PARAM_GRID = list(product(
    [1.0, 1.5, 2.0, 2.5, 3.0],   # tp_mult (target as multiple of Asian range)
    [5, 10, 15, 20],              # min_range_pips
    [60, 80, 100, 120],           # max_range_pips
))


def log(msg):
    print(msg)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def evaluate(symbol, h1, period, params, cost_pips=2.0):
    """Run strategy on one pair, one period, with given params and costs."""
    start, end = period
    sliced = h1.loc[start:end].copy()
    if len(sliced) < 100:
        return None

    raw = _run_single_symbol(
        sliced, symbol,
        tp_mult=params['tp_mult'],
        use_trend_filter=False,
        min_range_pips=params['min_range'],
        max_range_pips=params['max_range'],
    )
    if len(raw) < 30:
        return {'passed': False, 'oos_pf': None, 'oos_n': len(raw),
                'is_pf': None, 'bootstrap': None, 'dsr_p': None,
                'reason': f'only {len(raw)} total trades'}

    cost_trades = apply_cost_to_trades(raw, symbol, cost_pips=cost_pips)

    def strategy_fn(anchor):
        s = anchor.index[0]
        e = anchor.index[-1]
        in_window = sorted([t for t in cost_trades if s <= t.entry_time <= e],
                          key=lambda x: x.entry_time)
        return StrategyResult(trades=in_window, name=f'london_{symbol}')

    config = GateConfig(
        min_oos_trades=30,
        bootstrap_percentile=95.0,
        deflated_sharpe_pvalue=0.007,
        max_is_oos_degradation=0.30,
        n_resamples=2000,
        num_prior_trials=9,
    )

    v = run_validation(strategy_fn, sliced, config=config, verbose=False)
    return {
        'passed': v.passed, 'is_pf': round(v.is_pf, 3),
        'oos_pf': round(v.oos_pf, 3), 'oos_n': v.oos_n,
        'bootstrap': round(v.bootstrap_percentile_achieved, 1),
        'dsr_p': round(v.deflated_pvalue, 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cost-pips', type=float, default=2.0)
    args = parser.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)

    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            f.write("# Per-Pair Parameter Grid Log\n\n")
            f.write(f"Started: {now}\n")
            f.write(f"Cost: {args.cost_pips} pips round-trip\n")
            f.write(f"Grid: {len(PARAM_GRID)} combos per pair x {len(SYMBOLS)} pairs\n\n")

    pair_data = {}
    for s in SYMBOLS:
        pair_data[s] = load_mt5_csv_pair(DATA_DIR, s)
        print(f"  {s}: {len(pair_data[s])} bars")

    best_per_pair = {}

    for symbol in SYMBOLS:
        log(f"\n\n{'='*60}\n## {symbol}\n{'='*60}")
        h1 = pair_data[symbol]
        best = None

        for idx, (tp_mult, min_r, max_r) in enumerate(PARAM_GRID):
            params = {'tp_mult': tp_mult, 'min_range': min_r, 'max_range': max_r}

            ra = evaluate(symbol, h1, PERIOD_A, params, cost_pips=args.cost_pips)
            if ra is None:
                continue
            rb = None
            if ra['passed']:
                rb = evaluate(symbol, h1, PERIOD_B, params, cost_pips=args.cost_pips)

            both_pass = ra['passed'] and rb is not None and rb['passed']

            tag = "BOTH PASS" if both_pass else ("A pass only" if ra['passed'] else "fail")
            log(f"  [{idx+1:>2}/{len(PARAM_GRID)}] TP={tp_mult} minR={min_r} maxR={max_r}  "
                f"A: PF={ra['oos_pf']} N={ra['oos_n']} BS={ra['bootstrap']}% p={ra['dsr_p']}"
                + (f"  B: PF={rb['oos_pf']} N={rb['oos_n']} BS={rb['bootstrap']}% p={rb['dsr_p']}"
                   if rb else "")
                + f"  -> {tag}")

            if both_pass:
                avg_pf = (ra['oos_pf'] + rb['oos_pf']) / 2
                if best is None or avg_pf > best['avg_oos_pf']:
                    best = {
                        'symbol': symbol,
                        'params': params,
                        'period_a': ra,
                        'period_b': rb,
                        'avg_oos_pf': round(avg_pf, 3),
                    }

        if best:
            log(f"\n>>> {symbol} BEST: TP={best['params']['tp_mult']} "
                f"minR={best['params']['min_range']} maxR={best['params']['max_range']}  "
                f"avg OOS PF={best['avg_oos_pf']}")
            best_per_pair[symbol] = best
        else:
            log(f"\n>>> {symbol}: NO PARAMS PASS BOTH PERIODS NET OF COSTS")
            best_per_pair[symbol] = {'symbol': symbol, 'no_edge_found': True}

    # Final summary
    log(f"\n\n{'#'*60}\n# FINAL VERDICT\n{'#'*60}\n")
    survivors = []
    for s in SYMBOLS:
        b = best_per_pair[s]
        if b.get('no_edge_found'):
            log(f"  {s}: NO EDGE — strategy does not survive costs on this pair.")
        else:
            survivors.append(s)
            log(f"  {s}: TP={b['params']['tp_mult']}x  "
                f"range={b['params']['min_range']}-{b['params']['max_range']}pips  "
                f"avg OOS PF={b['avg_oos_pf']}")
            log(f"        A: OOS PF={b['period_a']['oos_pf']} N={b['period_a']['oos_n']} "
                f"BS={b['period_a']['bootstrap']}%")
            log(f"        B: OOS PF={b['period_b']['oos_pf']} N={b['period_b']['oos_n']} "
                f"BS={b['period_b']['bootstrap']}%")

    log(f"\nSurvivors: {len(survivors)}/{len(SYMBOLS)} pairs ({', '.join(survivors) or 'none'})")

    with open(BEST_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'cost_pips': args.cost_pips,
            'survivors': survivors,
            'per_pair': best_per_pair,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)
    log(f"\nResults saved to {BEST_FILE}")


if __name__ == '__main__':
    main()
