"""
Iterative Backtest: Find a config that 2-3x's $25k in 3-5 years.

Loop:
  1. Run backtest with current sizing/risk rules
  2. Check if 2-3x achieved within 3-5 years
  3. If not, log result for council review and adjust
  4. Repeat until target achieved or all options exhausted

Strategy params are LOCKED (TP=1.5, range 15-60, W1 EMA-26).
Only sizing/risk rules are adjusted between iterations.

Account: $25,000 starting, 1:100 leverage
Target: $50,000 - $75,000 (2-3x) in 3-5 years
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'validation_harness'))

from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from strategy_london_breakout import _run_single_symbol
from harness import load_mt5_csv_pair

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# Locked strategy params (DO NOT CHANGE)
STRATEGY = dict(
    tp_mult=1.5,
    use_trend_filter=True,
    w1_ema_period=26,
    min_range_pips=15.0,
    max_range_pips=60.0,
    asian_start=0, asian_end=7,
    london_start=7, london_end=10,
    eod_exit_hour=17,
)

# Realistic cost model: The5ers actual = 0.9-1.3 pips
# We use 1.0 pip for retail account assumption
COST_PIPS = 1.0
PIP_SIZE = 0.0001  # GBPUSD


def backtest_compounding(trades_raw, starting_equity, risk_pct_fn, cost_pips=1.0,
                          leverage=100, max_dd_halt_pct=None, label=""):
    """
    Replay trades as a compounding equity curve.

    trades_raw: list of Trade objects with normalized $100 risk pnl
    risk_pct_fn: callable(equity, days_elapsed, n_trades_so_far, win_streak, loss_streak) -> risk_pct
    cost_pips: round-trip cost in pips (deducted from each trade)
    max_dd_halt_pct: if drawdown from peak exceeds this %, halt trading (None = no halt)

    Returns: (final_equity, equity_curve, trades_executed, halted, halt_reason)
    """
    equity = starting_equity
    peak_equity = starting_equity
    equity_curve = [(trades_raw[0].entry_time if trades_raw else None, starting_equity)]
    trades_executed = []
    win_streak = 0
    loss_streak = 0
    halted = False
    halt_reason = ""
    start_date = trades_raw[0].entry_time if trades_raw else None

    for i, t in enumerate(trades_raw):
        if halted:
            break

        # Compute stop distance in pips
        stop_pips = abs(t.entry_price - (t.entry_price - t.pnl/100.0 * abs(t.entry_price - t.exit_price))) / PIP_SIZE
        # Better: derive stop from the normalized formula
        # pnl = (move/stop_dist) * 100; so stop_dist = move/pnl * 100
        move = abs(t.entry_price - t.exit_price)
        if t.pnl != 0:
            stop_dist = move / abs(t.pnl) * 100
        else:
            stop_dist = move
        stop_pips = stop_dist / PIP_SIZE
        if stop_pips < 5 or stop_pips > 200:
            stop_pips = 35  # fallback

        # Days elapsed
        days_elapsed = (t.entry_time - start_date).days if start_date else 0

        # Get current risk %
        risk_pct = risk_pct_fn(equity, days_elapsed, len(trades_executed), win_streak, loss_streak)

        # Position sizing (1 standard lot of GBPUSD = $10/pip at 1:1)
        # Real lots = (equity * risk%) / (stop_pips * $10)
        risk_dollars = equity * (risk_pct / 100.0)
        lots = risk_dollars / (stop_pips * 10.0)
        # Cap by leverage: max position size = (equity * leverage) / contract_size
        # For GBPUSD: 1 lot = 100,000 GBP ≈ $130k notional
        max_lots_by_leverage = (equity * leverage) / 130000
        lots = min(lots, max_lots_by_leverage)
        lots = max(lots, 0.01)  # min lot

        # Compute trade PnL in dollars
        # Original normalized pnl was per $100 risk; scale by actual risk
        actual_pnl = t.pnl * (risk_dollars / 100.0)

        # Subtract costs
        cost_dollars = lots * (cost_pips * 10.0)  # cost_pips * $10 per lot
        actual_pnl -= cost_dollars

        equity += actual_pnl

        # Track peak/DD
        if equity > peak_equity:
            peak_equity = equity
        dd_pct = (peak_equity - equity) / peak_equity * 100

        # Halt check
        if max_dd_halt_pct is not None and dd_pct > max_dd_halt_pct:
            halted = True
            halt_reason = f"Max DD breach: {dd_pct:.2f}% > {max_dd_halt_pct}%"
            break

        # Equity wipe check
        if equity <= starting_equity * 0.1:
            halted = True
            halt_reason = f"Account near-blown: equity=${equity:.0f}"
            break

        # Update streaks
        if actual_pnl > 0:
            win_streak += 1
            loss_streak = 0
        else:
            loss_streak += 1
            win_streak = 0

        trades_executed.append({
            'time': t.entry_time, 'pnl': actual_pnl, 'equity': equity,
            'risk_pct': risk_pct, 'lots': lots, 'dd_pct': dd_pct
        })
        equity_curve.append((t.entry_time, equity))

    return equity, equity_curve, trades_executed, halted, halt_reason


def analyze_run(trades_executed, starting_equity, equity_curve, target_low=50000, target_high=75000):
    """Return analysis dict with key metrics."""
    if not trades_executed:
        return {}

    final = trades_executed[-1]['equity']
    total_return = (final - starting_equity) / starting_equity * 100

    # Time to 2x
    time_to_2x = None
    time_to_3x = None
    for trade in trades_executed:
        if trade['equity'] >= 2 * starting_equity and time_to_2x is None:
            time_to_2x = (trade['time'] - trades_executed[0]['time']).days / 365.25
        if trade['equity'] >= 3 * starting_equity and time_to_3x is None:
            time_to_3x = (trade['time'] - trades_executed[0]['time']).days / 365.25

    # Annual return
    days = (trades_executed[-1]['time'] - trades_executed[0]['time']).days
    years = days / 365.25
    cagr = ((final / starting_equity) ** (1/years) - 1) * 100 if years > 0 else 0

    # Max DD
    max_dd = max((t['dd_pct'] for t in trades_executed), default=0)

    # Win rate
    wins = sum(1 for t in trades_executed if t['pnl'] > 0)
    wr = wins / len(trades_executed) * 100

    # Profit factor
    gross_win = sum(t['pnl'] for t in trades_executed if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in trades_executed if t['pnl'] < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')

    return {
        'final_equity': final,
        'total_return_pct': total_return,
        'cagr_pct': cagr,
        'years': years,
        'time_to_2x_years': time_to_2x,
        'time_to_3x_years': time_to_3x,
        'max_dd_pct': max_dd,
        'n_trades': len(trades_executed),
        'win_rate_pct': wr,
        'profit_factor': pf,
        'achieved_2x': final >= 2 * starting_equity,
        'achieved_3x': final >= 3 * starting_equity,
        'in_3to5_year_window': time_to_2x is not None and 3 <= time_to_2x <= 5,
    }


# === RISK RULES (each iteration tries a new one) ===

def risk_iter1_progressive(equity, days, n_trades, ws, ls):
    """Council-recommended: 0.5% → 1% after 30d, 1.5% after 90d"""
    if days < 30:
        return 0.5
    elif days < 90:
        return 1.0
    else:
        return 1.5


def risk_iter2_aggressive_compound(equity, days, n_trades, ws, ls):
    """Compound aggressively: 1% always, 2% after 6 winning trades in a row"""
    if ws >= 6:
        return 2.0
    return 1.0


def risk_iter3_two_pct_flat(equity, days, n_trades, ws, ls):
    """Flat 2% per trade — Expansionist's preferred level"""
    return 2.0


def risk_iter4_kelly_inspired(equity, days, n_trades, ws, ls):
    """Kelly-inspired: scale up after wins, scale down after losses"""
    base = 1.5
    if ls >= 3:
        return base * 0.5  # cut risk on losing streak
    if ws >= 5:
        return min(base * 1.5, 3.0)
    return base


def risk_iter5_balanced(equity, days, n_trades, ws, ls):
    """Balanced: 1% start, scale up to 2% after 50 trades, cut back on 4-loss streak"""
    if ls >= 4:
        return 0.5
    if n_trades < 50:
        return 1.0
    elif n_trades < 150:
        return 1.5
    else:
        return 2.0


def risk_iter6_three_pct(equity, days, n_trades, ws, ls):
    """Aggressive 3% flat"""
    return 3.0


# === MAIN ITERATION LOOP ===

def main():
    print("=" * 80)
    print("ITERATIVE BACKTEST: $25k → 2-3x in 3-5 years")
    print("=" * 80)

    # Load GBPUSD data once
    print("\nLoading GBPUSD H1 2015-2024...")
    df = load_mt5_csv_pair(DATA_DIR, 'GBPUSD')
    df = df.loc[datetime(2015,1,1):datetime(2024,12,31)]
    print(f"  Loaded {len(df)} bars")

    print("\nRunning strategy with locked params...")
    trades_raw = _run_single_symbol(df, 'GBPUSD', **STRATEGY)
    print(f"  {len(trades_raw)} trades generated\n")

    iterations = [
        ("ITER 1: Council-progressive (0.5%→1%→1.5%)", risk_iter1_progressive, None),
        ("ITER 2: Aggressive compound (1% base, 2% on 6-win streak)", risk_iter2_aggressive_compound, None),
        ("ITER 3: Flat 2% (Expansionist)", risk_iter3_two_pct_flat, None),
        ("ITER 4: Kelly-inspired adaptive (1.5% base)", risk_iter4_kelly_inspired, None),
        ("ITER 5: Balanced scaling (1% → 1.5% → 2%)", risk_iter5_balanced, None),
        ("ITER 6: Aggressive 3% flat", risk_iter6_three_pct, None),
    ]

    results = []
    starting_equity = 25000

    for label, risk_fn, dd_halt in iterations:
        print(f"\n{'=' * 80}")
        print(f"{label}")
        print(f"{'=' * 80}")

        final_eq, curve, executed, halted, halt_reason = backtest_compounding(
            trades_raw, starting_equity, risk_fn,
            cost_pips=COST_PIPS, leverage=100, max_dd_halt_pct=dd_halt, label=label
        )

        analysis = analyze_run(executed, starting_equity, curve)
        analysis['label'] = label
        analysis['halted'] = halted
        analysis['halt_reason'] = halt_reason

        print(f"  Final equity:    ${final_eq:>12,.2f}  ({analysis.get('total_return_pct',0):+.1f}%)")
        print(f"  CAGR:            {analysis.get('cagr_pct',0):.2f}%/year over {analysis.get('years',0):.1f}y")
        print(f"  Trades:          {analysis.get('n_trades',0)}")
        print(f"  Win rate:        {analysis.get('win_rate_pct',0):.1f}%")
        print(f"  Profit factor:   {analysis.get('profit_factor',0):.3f}")
        print(f"  Max DD:          {analysis.get('max_dd_pct',0):.2f}%")

        t2x = analysis.get('time_to_2x_years')
        t3x = analysis.get('time_to_3x_years')
        print(f"  Time to 2x:      {f'{t2x:.2f} years' if t2x else 'NOT REACHED'}")
        print(f"  Time to 3x:      {f'{t3x:.2f} years' if t3x else 'NOT REACHED'}")

        if halted:
            print(f"  *** HALTED: {halt_reason} ***")

        # SUCCESS check
        success = (
            analysis.get('achieved_2x', False) and
            (t2x is not None and t2x <= 5.0) and
            not halted
        )
        ideal = (
            analysis.get('achieved_3x', False) and
            (t3x is not None and 3.0 <= t3x <= 5.0)
        )

        if ideal:
            print(f"  ✓✓ IDEAL: 3x achieved in 3-5 year window")
        elif success:
            print(f"  ✓ SUCCESS: 2x achieved within 5 years")
        else:
            print(f"  ✗ FAIL: did not hit 2x in 5 years (or halted)")

        analysis['success'] = success
        analysis['ideal'] = ideal
        results.append(analysis)

        # Continue all iterations to find the BEST one (not just first acceptable)
        if ideal:
            print(f"  >>> IDEAL: continuing to find best alternative for council comparison")

    # Save all results for council review
    output_path = os.path.join(os.path.dirname(__file__), 'results', 'iteration_to_target.json')
    with open(output_path, 'w') as f:
        json.dump({
            'starting_equity': starting_equity,
            'cost_pips': COST_PIPS,
            'leverage': 100,
            'iterations': [
                {k: (v if not callable(v) else None) for k, v in r.items()}
                for r in results
            ]
        }, f, indent=2, default=str)
    print(f"\nResults saved to: {output_path}")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'Iter':<50} {'Final $':>12} {'CAGR':>8} {'2x Yrs':>8} {'Status':>8}")
    for r in results:
        t2x = r.get('time_to_2x_years')
        status = '✓✓ IDEAL' if r.get('ideal') else ('✓' if r.get('success') else '✗')
        if r.get('halted'): status = 'HALT'
        print(f"{r['label'][:50]:<50} {r.get('final_equity',0):>12,.0f} "
              f"{r.get('cagr_pct',0):>6.1f}% {f'{t2x:.2f}' if t2x else '  N/A':>8} {status:>8}")

    return results


if __name__ == "__main__":
    main()
