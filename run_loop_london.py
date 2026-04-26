"""
London Breakout Iteration Loop
================================
Systematic parameter search for the London session breakout strategy.
Auto-resumes from existing log. Tests Period A then Period B.

Usage:
    python run_loop_london.py
    python run_loop_london.py --max-iter 200
"""

import sys
import os
import json
import re
import subprocess
import argparse
from datetime import datetime, timezone
from itertools import product

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf-16'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

REPO_ROOT   = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.join(REPO_ROOT, 'validation_harness')
RESULTS_DIR = os.path.join(REPO_ROOT, 'results')
LOG_FILE    = os.path.join(RESULTS_DIR, 'london_iteration_log.md')
BEST_FILE   = os.path.join(RESULTS_DIR, 'london_best_params.json')
DATA_DIR_DEFAULT = os.path.join(REPO_ROOT, 'data')

PERIOD_A = ('2015-01-01', '2019-12-31')
PERIOD_B = ('2020-01-01', '2024-12-31')

# Parameter grid
# (tp_mult, trend_filter, w1_ema, min_range, max_range)
PARAM_GRID = []
for tp_mult in [1.0, 1.5, 2.0, 2.5, 3.0]:
    for trend_filter in [0, 1]:
        if trend_filter == 0:
            for min_r in [5, 10, 15, 20]:
                PARAM_GRID.append((tp_mult, trend_filter, 20, min_r, 80))
        else:
            for w1_ema in [10, 20, 26]:
                for min_r in [5, 10, 15, 20]:
                    PARAM_GRID.append((tp_mult, trend_filter, w1_ema, min_r, 80))

# Total: 5 tp × (4 no-filter + 3×4 filter) = 5 × 16 = 80 combinations


def ensure_dirs():
    os.makedirs(RESULTS_DIR, exist_ok=True)


def log(msg: str):
    print(msg)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def run_strategy(label, start, end, tp_mult, trend_filter, w1_ema, min_range, max_range,
                 data_dir, num_trials=9) -> dict:
    cmd = [
        sys.executable,
        os.path.join(HARNESS_DIR, 'strategy_london_breakout.py'),
        '--start',        start,
        '--end',          end,
        '--data-dir',     data_dir,
        '--label',        label,
        '--tp-mult',      str(tp_mult),
        '--trend-filter', str(trend_filter),
        '--w1-ema',       str(w1_ema),
        '--min-range',    str(min_range),
        '--max-range',    str(max_range),
        '--num-trials',   str(num_trials),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=HARNESS_DIR)
    output = result.stdout + result.stderr

    metrics = {
        'label': label, 'start': start, 'end': end,
        'passed': result.returncode == 0, 'output': output,
        'is_pf': None, 'oos_pf': None, 'oos_n': None,
        'bootstrap': None, 'dsr_p': None,
    }
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('IS:') and 'PF=' in line:
            try:
                metrics['is_pf'] = float(line.split('PF=')[1].split(',')[0])
            except Exception:
                pass
        if line.startswith('OOS:') and 'PF=' in line:
            try:
                metrics['oos_n']  = int(line.split('OOS:')[1].split('trades')[0].strip())
                metrics['oos_pf'] = float(line.split('PF=')[1].split(',')[0])
            except Exception:
                pass
        if 'Bootstrap:' in line and 'beats' in line:
            try:
                metrics['bootstrap'] = float(line.split('beats')[1].split('%')[0].strip())
            except Exception:
                pass
        if 'Deflated Sharpe:' in line and 'p=' in line:
            try:
                metrics['dsr_p'] = float(line.split('p=')[1].rstrip(')'))
            except Exception:
                pass
    return metrics


def log_iteration(n, params, period_a, period_b):
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    bar = '-' * 60
    tf = 'ON' if params['trend_filter'] else 'OFF'
    log(f"\n{bar}")
    log(f"## Iteration {n}  --  {now}")
    log(f"**Params:** TP={params['tp_mult']}x  TrendFilter={tf}  "
        f"W1_EMA={params['w1_ema']}  MinRange={params['min_range']}pips")
    log(f"\n**Period A (2015-2019):**")
    log(f"  IS PF={period_a['is_pf']}  OOS PF={period_a['oos_pf']}  "
        f"N={period_a['oos_n']}  Bootstrap={period_a['bootstrap']}%  "
        f"p={period_a['dsr_p']}  -> {'PASS' if period_a['passed'] else 'FAIL'}")
    if period_b:
        log(f"\n**Period B (2020-2024):**")
        log(f"  IS PF={period_b['is_pf']}  OOS PF={period_b['oos_pf']}  "
            f"N={period_b['oos_n']}  Bootstrap={period_b['bootstrap']}%  "
            f"p={period_b['dsr_p']}  -> {'PASS' if period_b['passed'] else 'FAIL'}")
    else:
        log(f"\n**Period B:** skipped (Period A failed)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-iter', type=int, default=len(PARAM_GRID))
    parser.add_argument('--data-dir', default=DATA_DIR_DEFAULT)
    args = parser.parse_args()

    ensure_dirs()

    # Auto-resume
    start_from = 1
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        completed = len(re.findall(r'^## Iteration \d+', content, re.MULTILINE))
        if completed > 0:
            start_from = completed + 1
            print(f"  Auto-resume: {completed} iterations logged, starting from #{start_from}")

    print(f"\n{'='*60}")
    print(f"  LONDON BREAKOUT ITERATION LOOP")
    print(f"  Grid size:    {len(PARAM_GRID)} combinations")
    print(f"  Starting from: #{start_from}")
    print(f"  Data dir:     {args.data_dir}")
    print(f"  Log:          {LOG_FILE}")
    print(f"{'='*60}\n")

    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            f.write("# London Breakout Iteration Log\n\n")
            f.write(f"Started: {now}\n\n")

    iteration = 0
    for idx, (tp_mult, trend_filter, w1_ema, min_range, max_range) in enumerate(PARAM_GRID):
        if idx + 1 < start_from:
            continue
        iteration += 1
        if iteration > args.max_iter:
            log(f"\n{'='*60}")
            log(f"MAX ITERATIONS ({args.max_iter}) REACHED")
            log(f"{'='*60}")
            break

        params = dict(tp_mult=tp_mult, trend_filter=trend_filter, w1_ema=w1_ema,
                      min_range=min_range, max_range=max_range)

        print(f"\n>>> Iteration {iteration}/{args.max_iter}  "
              f"[grid #{idx+1}/{len(PARAM_GRID)}]  "
              f"TP={tp_mult}x  Filter={'ON' if trend_filter else 'OFF'}  "
              f"W1={w1_ema}  MinR={min_range}")

        period_a = run_strategy(
            label=f"London Iter {iteration} Period A",
            start=PERIOD_A[0], end=PERIOD_A[1],
            data_dir=args.data_dir, **params
        )

        period_b = None
        if period_a['passed']:
            print(f"    Period A PASSED -- running Period B...")
            period_b = run_strategy(
                label=f"London Iter {iteration} Period B",
                start=PERIOD_B[0], end=PERIOD_B[1],
                data_dir=args.data_dir, **params
            )

        log_iteration(iteration, params, period_a, period_b)

        if period_b and period_b['passed']:
            log(f"\n{'='*60}")
            log(f"CONVERGED AT ITERATION {iteration}")
            log(f"Both Period A and Period B PASSED.")
            log(f"\nFinal parameters:")
            log(f"  TP={tp_mult}x  TrendFilter={'ON' if trend_filter else 'OFF'}")
            log(f"  W1_EMA={w1_ema}  MinRange={min_range}pips")
            log(f"\nPeriod A: OOS PF={period_a['oos_pf']}  N={period_a['oos_n']}  "
                f"Bootstrap={period_a['bootstrap']}%  p={period_a['dsr_p']}")
            log(f"Period B: OOS PF={period_b['oos_pf']}  N={period_b['oos_n']}  "
                f"Bootstrap={period_b['bootstrap']}%  p={period_b['dsr_p']}")
            log(f"\nNext step: Forward test on 2025+ data, then port to MQL5 EA.")
            log(f"{'='*60}")

            best = {
                'converged': True,
                'iteration': iteration,
                'strategy': 'london_breakout',
                'params': params,
                'period_a': {k: v for k, v in period_a.items() if k != 'output'},
                'period_b': {k: v for k, v in period_b.items() if k != 'output'},
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
            with open(BEST_FILE, 'w') as f:
                json.dump(best, f, indent=2)
            print(f"\nBest params saved to: {BEST_FILE}")
            return
    else:
        log(f"\n{'='*60}")
        log(f"Grid exhausted after {iteration} iterations without convergence.")
        log(f"{'='*60}")


if __name__ == '__main__':
    main()
