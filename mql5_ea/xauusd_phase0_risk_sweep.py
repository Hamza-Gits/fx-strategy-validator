#!/usr/bin/env python3
"""XAUUSD MC sweep across risk levels to find honest tradeoff curve."""
import numpy as np

N_SIMS         = 50_000
TRADES_PER_YEAR = 200
RR             = 2.0
SLIPPAGE_DRAG  = 0.10
EFFECTIVE_RR   = RR * (1 - SLIPPAGE_DRAG)
DAILY_DD       = 0.03
TOTAL_DD       = 0.06
START_EQ       = 5000.0


def simulate(risk_pct, win_rate):
    eq = START_EQ
    blew = False
    hit_15 = hit_2 = hit_3 = False
    for _ in range(TRADES_PER_YEAR):
        daily = eq
        r = eq * risk_pct
        if np.random.random() < win_rate:
            eq += r * EFFECTIVE_RR
        else:
            eq -= r
        if eq >= START_EQ * 1.5: hit_15 = True
        if eq >= START_EQ * 2.0: hit_2  = True
        if eq >= START_EQ * 3.0: hit_3  = True
        if (daily - eq) / daily > DAILY_DD: blew = True; break
        if (START_EQ - eq) / START_EQ > TOTAL_DD: blew = True; break
    return blew, hit_15, hit_2, hit_3, eq


def sweep(risk_pct, win_rate):
    np.random.seed(42)
    blew = h15 = h2 = h3 = 0
    final = []
    for _ in range(N_SIMS):
        b, a, c, d, e = simulate(risk_pct, win_rate)
        blew += b; h15 += a; h2 += c; h3 += d
        final.append(e)
    return {
        'risk':   risk_pct * 100,
        'wr':     win_rate * 100,
        'blow':   blew/N_SIMS*100,
        'p15':    h15/N_SIMS*100,
        'p2':     h2/N_SIMS*100,
        'p3':     h3/N_SIMS*100,
        'med':    np.median(final),
    }


def main():
    print("=" * 90)
    print("XAUUSD RISK-LEVEL SWEEP (50k sims) — Aqua $5k, 1:2 R/R, 1.8R effective, 200 trades/yr")
    print("=" * 90)
    print(f"{'Risk%':<7} {'WR%':<6} {'P(blow)':<10} {'P(1.5x)':<10} {'P(2x)':<10} {'P(3x)':<10} {'Med $':<8} {'Gate'}")
    print("-" * 90)

    for risk in [0.005, 0.0075, 0.01, 0.0125, 0.015]:
        for wr in [0.40, 0.45, 0.50, 0.55]:
            r = sweep(risk, wr)
            gate_blow = r['blow'] < 15.0
            gate_2x   = r['p2']   >= 25.0
            gate_15x  = r['p15']  >= 50.0
            verdict = ""
            if gate_blow and gate_2x:    verdict = "PASS-2X"
            elif gate_blow and gate_15x: verdict = "PASS-1.5X"
            elif not gate_blow:          verdict = "BLOWUP"
            else:                        verdict = "LOW-UPSIDE"
            print(f"{r['risk']:<7.2f} {r['wr']:<6.0f} {r['blow']:<10.2f} {r['p15']:<10.2f} "
                  f"{r['p2']:<10.2f} {r['p3']:<10.2f} ${r['med']:<7.0f} {verdict}")
        print()

    print("=" * 90)
    print("INTERPRETATION:")
    print("  PASS-2X    = P(blow)<15% AND P(2x)>=25% — original target reachable")
    print("  PASS-1.5X  = P(blow)<15% AND P(1.5x)>=50% — honest payout target")
    print("  BLOWUP     = ruin too likely")
    print("  LOW-UPSIDE = safe but won't compound to target")
    print("=" * 90)


if __name__ == '__main__':
    main()
