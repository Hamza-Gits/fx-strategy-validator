"""
Autonomous Strategy Iteration Loop
====================================
Runs up to MAX_ITERATIONS parameter combinations.
Tests Period A (2015-2019) first, then Period B (2020-2024) only if A passes.
Stops when both periods pass OR max iterations reached.
Logs all results to results/iteration_log.md.
Best passing params saved to results/best_params.json.

Usage:
    python run_loop.py
    python run_loop.py --max-iter 50
    python run_loop.py --data-dir "C:/path/to/csvs"
"""

import sys
import os
import json
import re
import subprocess
import argparse
from datetime import datetime, timezone
from itertools import product

# Fix Windows console encoding so Unicode in log strings doesn't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf-16'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# ─── CONFIG ───────────────────────────────────────────────────────────────────

REPO_ROOT   = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.join(REPO_ROOT, 'validation_harness')
RESULTS_DIR = os.path.join(REPO_ROOT, 'results')
LOG_FILE    = os.path.join(RESULTS_DIR, 'iteration_log.md')
BEST_FILE   = os.path.join(RESULTS_DIR, 'best_params.json')

DATA_DIR_DEFAULT = os.path.join(REPO_ROOT, 'data')

PERIOD_A = ('2015-01-01', '2019-12-31')
PERIOD_B = ('2020-01-01', '2024-12-31')

# ─── PARAMETER SEARCH SPACE ───────────────────────────────────────────────────
# Council-directed systematic grid. Each entry is (w1_ema, d1_ema, atr, sl_mult, tp_mult)
# Order: shorter EMAs (faster signals) → longer EMAs (slower, cleaner signals)
# RR ratios: 1.5 (3/2), 2.0 (4/2), 2.5 (5/2)

PARAM_GRID = list(product(
    [12, 20, 26, 50],           # W1 EMA period  (removed 8 — too fast/noisy)
    [20, 50, 100, 200],         # D1 EMA period  (added 100, 200 — institutional levels)
    [14, 21],                   # ATR period
    [1.5, 2.0, 2.5],            # SL ATR multiplier
    [2.25, 3.0, 4.0, 5.0],     # TP ATR multiplier (RR = tp/sl)
))

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def ensure_dirs():
    os.makedirs(RESULTS_DIR, exist_ok=True)


def log(msg: str):
    print(msg)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def run_strategy(label, start, end, w1_ema, d1_ema, atr, sl_mult, tp_mult,
                 data_dir, num_trials=9) -> dict:
    """Run strategy_template.py as a subprocess, capture stdout, parse results."""
    cmd = [
        sys.executable,
        os.path.join(HARNESS_DIR, 'strategy_template.py'),
        '--start',      start,
        '--end',        end,
        '--data-dir',   data_dir,
        '--label',      label,
        '--w1-ema',     str(w1_ema),
        '--d1-ema',     str(d1_ema),
        '--atr',        str(atr),
        '--sl-mult',    str(sl_mult),
        '--tp-mult',    str(tp_mult),
        '--num-trials', str(num_trials),
    ]
    env = os.environ.copy()
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=HARNESS_DIR)
    output = result.stdout + result.stderr

    # Parse key metrics from harness output
    metrics = {
        'label':     label,
        'start':     start,
        'end':       end,
        'passed':    result.returncode == 0,
        'output':    output,
        'is_pf':     None,
        'oos_pf':    None,
        'oos_n':     None,
        'bootstrap': None,
        'dsr_p':     None,
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


def log_iteration(n: int, params: dict, period_a: dict, period_b: dict | None):
    bar = '-' * 60
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    log(f"\n{bar}")
    log(f"## Iteration {n}  --  {now}")
    log(f"**Params:** W1_EMA={params['w1_ema']}  D1_EMA={params['d1_ema']}  "
        f"ATR={params['atr']}  SL={params['sl_mult']}x  TP={params['tp_mult']}x  "
        f"RR={params['tp_mult']/params['sl_mult']:.2f}")
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


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-iter', type=int, default=384)
    parser.add_argument('--data-dir', default=DATA_DIR_DEFAULT)
    parser.add_argument('--start-from', type=int, default=None,
                        help='Resume from this grid index (1-based). Auto-detected from log if omitted.')
    parser.add_argument('--resume', action='store_true',
                        help='Auto-read log and resume after last completed iteration')
    args = parser.parse_args()

    ensure_dirs()

    # Auto-detect resume point from existing log
    start_from = args.start_from or 1
    if args.resume or (args.start_from is None and os.path.exists(LOG_FILE)):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        completed = len(re.findall(r'^## Iteration \d+', content, re.MULTILINE))
        if completed > 0:
            start_from = completed + 1
            print(f"  Auto-resume: {completed} iterations already logged, starting from #{start_from}")

    print(f"\n{'='*60}")
    print(f"  AUTONOMOUS STRATEGY ITERATION LOOP")
    print(f"  Max iterations: {args.max_iter}")
    print(f"  Parameter grid: {len(PARAM_GRID)} combinations")
    print(f"  Starting from:  #{start_from}")
    print(f"  Data dir: {args.data_dir}")
    print(f"  Log: {LOG_FILE}")
    print(f"{'='*60}\n")

    # Write log header if new file
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write("# Strategy Iteration Log\n\n")
            now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            f.write(f"Started: {now}\n\n")

    iteration = 0
    for idx, (w1_ema, d1_ema, atr, sl_mult, tp_mult) in enumerate(PARAM_GRID):
        if idx + 1 < start_from:
            continue
        iteration += 1
        if iteration > args.max_iter:
            log(f"\n{'='*60}")
            log(f"MAX ITERATIONS ({args.max_iter}) REACHED WITHOUT CONVERGENCE")
            log(f"{'='*60}")
            break

        params = dict(w1_ema=w1_ema, d1_ema=d1_ema, atr=atr,
                      sl_mult=sl_mult, tp_mult=tp_mult)

        print(f"\n>>> Iteration {iteration}/{args.max_iter}  "
              f"[grid #{idx+1}/{len(PARAM_GRID)}]  "
              f"W1={w1_ema} D1={d1_ema} ATR={atr} SL={sl_mult} TP={tp_mult}")

        # ── Period A ──
        period_a = run_strategy(
            label=f"Iter {iteration} Period A",
            start=PERIOD_A[0], end=PERIOD_A[1],
            data_dir=args.data_dir,
            **params
        )

        # ── Period B (only if A passed) ──
        period_b = None
        if period_a['passed']:
            print(f"    Period A PASSED — running Period B...")
            period_b = run_strategy(
                label=f"Iter {iteration} Period B",
                start=PERIOD_B[0], end=PERIOD_B[1],
                data_dir=args.data_dir,
                **params
            )

        log_iteration(iteration, params, period_a, period_b)

        # ── SUCCESS ──
        if period_b and period_b['passed']:
            log(f"\n{'='*60}")
            log(f"CONVERGED AT ITERATION {iteration}")
            log(f"Both Period A (2015-2019) and Period B (2020-2024) PASSED.")
            log(f"\nFinal parameters:")
            log(f"  W1_EMA={w1_ema}  D1_EMA={d1_ema}  ATR={atr}")
            log(f"  SL={sl_mult}x ATR  TP={tp_mult}x ATR  RR={tp_mult/sl_mult:.2f}")
            log(f"\nPeriod A: OOS PF={period_a['oos_pf']}  N={period_a['oos_n']}  "
                f"Bootstrap={period_a['bootstrap']}%  p={period_a['dsr_p']}")
            log(f"Period B: OOS PF={period_b['oos_pf']}  N={period_b['oos_n']}  "
                f"Bootstrap={period_b['bootstrap']}%  p={period_b['dsr_p']}")
            log(f"\nNext step: Forward test on 2025+ data before prop firm deployment.")
            log(f"{'='*60}")

            best = {
                'converged': True,
                'iteration': iteration,
                'params': params,
                'period_a': {k: v for k, v in period_a.items() if k != 'output'},
                'period_b': {k: v for k, v in period_b.items() if k != 'output'},
                'timestamp': datetime.utcnow().isoformat(),
            }
            with open(BEST_FILE, 'w') as f:
                json.dump(best, f, indent=2)
            print(f"\nBest params saved to: {BEST_FILE}")
            return

    else:
        log(f"\n{'='*60}")
        log(f"Grid exhausted after {iteration} iterations without convergence.")
        log(f"Consider expanding the parameter grid in PARAM_GRID.")
        log(f"{'='*60}")


if __name__ == '__main__':
    main()
