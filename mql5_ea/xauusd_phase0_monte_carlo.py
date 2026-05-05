#!/usr/bin/env python3
"""
XAUUSD Phase 0: Monte Carlo Ruin Gate (Council mandate 2026-05-05)
Run BEFORE any MQL5 development.

Simulates 12 months of Option 2 (Daily ATR Breakout) trading on Aqua Funded
$5k account under realistic assumptions, computing:
  P(blowup):    probability of breaching 6% total DD or 3% daily DD
  P(2x):        probability of doubling capital
  P(3x):        probability of tripling capital

Gates (Council mandate):
  P(blowup) < 15%   AND   P(2x) >= 25%

If either gate fails, the honest answer is "lower target or increase capital",
not "ship the EA anyway."
"""
import numpy as np
import sys

# ===== Inputs =====
N_SIMS              = 100_000          # 100k Monte Carlo paths
TRADES_PER_YEAR     = 200              # ~3-5 trades/wk × 50 wks ≈ 200
RISK_PER_TRADE_PCT  = 0.005            # 0.5% flat
RR_RATIO            = 2.0              # 1:2 risk:reward
WIN_RATE            = 0.40             # baseline assumption
STARTING_EQUITY     = 5000.0
DAILY_DD_LIMIT      = 0.03             # Aqua hard rule
TOTAL_DD_LIMIT      = 0.06             # Aqua hard rule
TARGET_2X           = 2.0
TARGET_3X           = 3.0

# Realistic adjustments (council blind spots)
SLIPPAGE_DRAG       = 0.10             # 10% R-degradation from spread/slippage
EFFECTIVE_RR        = RR_RATIO * (1 - SLIPPAGE_DRAG)  # 1.8R after costs

# Trades per day (assume ~1 trade/day on average, daily DD checked)
TRADES_PER_DAY      = 1


def simulate_path():
    """Run one 12-month path. Return: (final_equity, blew_up, hit_2x, hit_3x)."""
    equity      = STARTING_EQUITY
    starting_eq = STARTING_EQUITY
    daily_eq    = STARTING_EQUITY
    blew_up     = False
    hit_2x      = False
    hit_3x      = False

    n_days = TRADES_PER_YEAR // TRADES_PER_DAY

    for day in range(n_days):
        daily_eq = equity   # reset daily anchor
        # 1 trade per day at our risk level
        risk_dollars = equity * RISK_PER_TRADE_PCT
        if np.random.random() < WIN_RATE:
            equity += risk_dollars * EFFECTIVE_RR
        else:
            equity -= risk_dollars

        # Track milestones
        if equity >= STARTING_EQUITY * TARGET_2X:
            hit_2x = True
        if equity >= STARTING_EQUITY * TARGET_3X:
            hit_3x = True

        # Daily DD check
        daily_dd = (daily_eq - equity) / daily_eq
        if daily_dd > DAILY_DD_LIMIT:
            blew_up = True
            break

        # Total DD check
        total_dd = (starting_eq - equity) / starting_eq
        if total_dd > TOTAL_DD_LIMIT:
            blew_up = True
            break

    return equity, blew_up, hit_2x, hit_3x


def run_monte_carlo(win_rate, n_sims=N_SIMS):
    """Run n_sims paths at given win rate. Return dict of probabilities."""
    global WIN_RATE
    WIN_RATE = win_rate

    blew_up_count = 0
    hit_2x_count  = 0
    hit_3x_count  = 0
    final_eqs     = []

    for _ in range(n_sims):
        final_eq, blew_up, hit_2x, hit_3x = simulate_path()
        if blew_up:    blew_up_count += 1
        if hit_2x:     hit_2x_count  += 1
        if hit_3x:     hit_3x_count  += 1
        final_eqs.append(final_eq)

    return {
        'win_rate':    win_rate,
        'p_blowup':    blew_up_count / n_sims * 100,
        'p_2x':        hit_2x_count  / n_sims * 100,
        'p_3x':        hit_3x_count  / n_sims * 100,
        'median_eq':   np.median(final_eqs),
        'p25_eq':      np.percentile(final_eqs, 25),
        'p75_eq':      np.percentile(final_eqs, 75),
    }


def main():
    np.random.seed(42)

    print("=" * 75)
    print("XAUUSD PHASE 0 — MONTE CARLO RUIN GATE (Council Mandate 2026-05-05)")
    print("=" * 75)
    print(f"Simulations:        {N_SIMS:,}")
    print(f"Trades/year:        {TRADES_PER_YEAR}  (1 trade/day × 200 days)")
    print(f"Risk/trade:         {RISK_PER_TRADE_PCT*100:.2f}%")
    print(f"R:R ratio:          1:{RR_RATIO}  (effective {EFFECTIVE_RR:.2f}R after slippage)")
    print(f"Aqua DD limits:     daily {DAILY_DD_LIMIT*100:.0f}% | total {TOTAL_DD_LIMIT*100:.0f}%")
    print()

    # Sweep win rates from pessimistic to optimistic
    win_rates = [0.35, 0.38, 0.40, 0.42, 0.45, 0.50, 0.55]

    print(f"{'WR':<7} {'P(blowup)':<12} {'P(2x)':<10} {'P(3x)':<10} {'Med Eq':<10} {'P25 Eq':<10} {'P75 Eq':<10} {'Verdict'}")
    print("-" * 75)

    results = []
    for wr in win_rates:
        r = run_monte_carlo(wr)
        results.append(r)

        gate_blowup = r['p_blowup'] < 15.0
        gate_2x     = r['p_2x']     >= 25.0
        verdict = "PASS" if (gate_blowup and gate_2x) else "FAIL"

        print(f"{wr*100:<7.0f} {r['p_blowup']:<12.2f} {r['p_2x']:<10.2f} {r['p_3x']:<10.2f} "
              f"${r['median_eq']:<9.0f} ${r['p25_eq']:<9.0f} ${r['p75_eq']:<9.0f} {verdict}")

    print("=" * 75)
    print()

    # Council gates
    breakeven_wr = None
    for r in results:
        if r['p_blowup'] < 15.0 and r['p_2x'] >= 25.0:
            breakeven_wr = r['win_rate']
            break

    print("COUNCIL GATES:  P(blowup) < 15%   AND   P(2x) >= 25%")
    print()
    if breakeven_wr is not None:
        print(f"MINIMUM WIN RATE TO PASS: {breakeven_wr*100:.0f}%")
        print(f"VERDICT: PROCEED — strategy must demonstrate WR >= {breakeven_wr*100:.0f}% in Phase 1-4 validation")
    else:
        print("VERDICT: STOP — even at 55% WR, the targets are unreachable under Aqua DD constraints.")
        print("HONEST ANSWER: Lower target (1.5x), increase capital, or relax DD limits.")
    print("=" * 75)


if __name__ == '__main__':
    main()
