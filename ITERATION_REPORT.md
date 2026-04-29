# Iteration Report: 9 Risk Sizing Rules vs. 10% DD Constraint

**Goal:** Find a position sizing rule that achieves 2-3x growth on a $25k personal account in <3 years on the validated GBPUSD London breakout strategy, with a hard cap of **10% maximum drawdown**.

**Method:** Iterative backtest with 9 different position sizing rules. Strategy parameters locked (TP=1.5x range, range 15-60 pips, W1 EMA-26 trend filter). All run on the same 667 trades from GBPUSD H1 2015-2024 with 1.0 pip cost model.

---

## All 9 Iteration Results

| Iter | Sizing Rule | Final $25k → | CAGR | 2x Time | 3x Time | Max DD | <10% DD? |
|------|------------|---------------|------|---------|---------|--------|----------|
| 1 | 0.5%→1%→1.5% progressive | $197,200 | 23.1% | 2.67y | 4.85y | 19.1% | ❌ |
| 2 | 1% base, 2% on 6-win streak | $105,088 | 15.5% | 4.67y | 8.36y | 13.0% | ❌ |
| 3 | Flat 2% | $380,952 | 31.5% | 2.00y | 3.83y | 24.9% | ❌ |
| 4 | Kelly adaptive (1.5% base) | $184,183 | 22.2% | 2.67y | 6.00y | 20.2% | ❌ |
| 5 | Balanced 1%→1.5%→2% | $307,456 | 28.7% | 2.17y | 4.60y | 23.2% | ❌ |
| 6 | Aggressive 3% flat | $1,299,518 | 48.7% | 1.70y | 2.08y | 35.5% | ❌ |
| 7 | Aggressive progressive 2%→2.5%→3% | $1,288,768 | 48.6% | 1.71y | 2.08y | 35.5% | ❌ |
| 8 | Fast-start 1.5%→2%→2.5% scaling | $580,734 | 37.2% | 2.00y | 3.80y | 31.9% | ❌ |
| **9** | **Flat 0.5% (The5ers-safe)** | **$51,103** | **7.4%** | **9.80y** | **N/A** | **6.66%** | **✅** |

---

## Council Verdict (5 advisors + peer review + chairman, 2026-04-28)

The council was first asked to pick the optimal iteration *without* the 10% DD constraint. Their analysis:

### Where the Council Agreed
- **9 iterations on the same 667 trades is one experiment with 9 leverage multipliers.** Sharpe, PF, edge are identical across rows — only position sizing varies. Apparent decision space collapses.
- **The 30% backtest decay assumption, taken seriously, kills Iter 6/7's <3-year claim.** 35.5% backtest DD becomes ~46% live; 2.08y to 3x becomes ~3y at best with no margin.
- **Iter 6 and Iter 7 are statistically identical** ($1.30M vs $1.29M, same DD). Iter 7's "progressive" wrapper buys nothing.
- **Response B (Expansionist) had the biggest blind spot:** treated DSR p<0.0005 as "institutional certainty" — but DSR controls for selection bias on the strategy, not for live execution decay or regime change.

### Blind Spots Caught in Peer Review
1. No Monte Carlo trade-shuffle was run. The 35.5% DD is one sample from a distribution; 95th percentile likely 45-55%.
2. Soft-recovery halt is non-linear in risk %. At 3% it likely fires 3-4 times over the horizon, each requiring 54% recovery at 0.5%. Erases compounding advantage.
3. GBPUSD London Breakout regime dependency — edge is structurally tied to post-2016 Brexit volatility and pre-algo-crowding liquidity. May have shifted since 2018.
4. Broker mechanics: spread widening at 08:00 GMT, slippage on London open breakouts, margin/stop-out before soft-recovery fires.
5. Real psychological DD tolerance on $25k personal = 15-20%, not 35%.

### The Initial Recommendation (no DD cap): Iter 1 (0.5%→1%→1.5%)
*"Iter 6 is the right answer to the wrong question. Iter 1 is the right answer to the question you should have asked."* The council reasoned that once 30% live decay is taken seriously, half-Kelly on the degraded edge sits in the 0.8-1.2% band, which Iter 1 brackets. Iter 1's 19% backtest DD becomes ~25% live — uncomfortable but inside the behavioral envelope.

### Revised After 10% DD Hard Constraint
The user has since imposed an absolute 10% DD cap. **This rules out Iter 1 (19.1% DD).** Out of all 9 iterations, only **Iter 9 (flat 0.5%)** at 6.66% backtest DD survives the constraint.

The honest math:
- This strategy's edge cannot deliver 2-3x in <3y *and* stay under 10% DD simultaneously
- Iter 9 delivers <10% DD but only 7.4% CAGR (2x at 9.8y)
- Iter 1 delivers 2x in 2.67y but 19% DD (violates cap)
- No iteration squares this circle on a single instrument

### Path to 2-3x in <3y with <10% DD
Multi-pair / multi-strategy diversification:
- Run Iter 9 (or similar low-risk progressive) on 3+ uncorrelated systems
- Aggregate return scales roughly linearly while aggregate DD stays bounded (uncorrelated drawdowns)
- Requires validation of additional pairs (EURUSD, USDJPY, etc.) — currently only GBPUSD has the validated edge

---

## The Final Sizing Rule (v4 EA Default — Iter 9, DD-capped)

```
RiskPercent           = 0.5%        (flat — no Phase 2/3 escalation)
UseProgressiveRisk    = false
DailyLossLimitPct     = 3.0%        (resets daily)
TrailingDDPct         = 8.0%        (soft recovery fires before 10% cap)
HardHalt              = false       (personal account: soft recovery)
RecoveryDays          = 30
RecoveryRiskPct       = 0.25%       (halved during recovery)
```

**Trailing DD ladder vs 10% cap:**
- Soft recovery triggers at 8% DD → drops to 0.25% risk for 30 days → resets watermark
- Worst-case trough between trigger and recovery: ~10% (margin: 2pp)
- For paranoid setting, drop `TrailingDDPct` to 7.0%

---

## Equity Trajectory (Iter 9 — DD-compliant)

Starting: $25,000
- After 1 year: ~$26,800 (+7.4%)
- After 2 years: ~$28,800
- After 3 years: ~$30,900
- After 5 years: ~$35,700
- After 10 years: $51,103 (+104%)

This is slow. Honest. And it stays under 10% DD.

For 2-3x in <3y with this DD constraint, see "Path to 2-3x" above — multi-pair diversification is the only mathematically defensible answer.

---

## Files

| File | Purpose |
|------|---------|
| `iterate_to_target.py` | Backtest simulator — reproduces all 9 iterations |
| `mql5_ea/LondonBreakout_v4.mq5` | EA with Iter 9 default + soft recovery + The5ers mode |
| `results/iteration_to_target.json` | Full numerical results for all 9 iterations |
| `results/trades_all.xlsx` | All 667 trades with 22 columns (Iter 1 reference) |

---

## Next Steps

1. **Backtest v4 on MT5 Strategy Tester** with Iter 9 settings + 8% trailing DD — verify backtest DD stays under 10%
2. **Validate 2025 forward test** on Python (truly unseen data)
3. **Demo trade 30 days** at 0.5% on The5ers demo
4. **Multi-pair research** — only viable path to 2-3x in <3y under 10% DD cap

---

**Report Date:** 2026-04-28
**Status:** Council-approved (with 10% DD revision), v4 EA built, ready for MT5 backtest
