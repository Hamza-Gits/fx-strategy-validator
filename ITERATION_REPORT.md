# Iteration Report: 13 Risk Sizing Rules vs. 10% DD / <5y Constraint

**Goal:** Find a position sizing rule that achieves 2-3x growth on a $25k personal account in **<5 years** on the validated GBPUSD London breakout strategy, with a hard cap of **10% maximum drawdown**.

**Method:** Iterative backtest with 13 different position sizing rules. Strategy parameters locked (TP=1.5x range, range 15-60 pips, W1 EMA-26 trend filter). All run on the same 667 trades from GBPUSD H1 2015-2024 with 1.0 pip cost model.

---

## All 13 Iteration Results

| Iter | Sizing Rule | Final $25k → | CAGR | 2x Time | 3x Time | Max DD | <10% DD? | <5y to 2x? |
|------|------------|---------------|------|---------|---------|--------|----------|------------|
| 1 | 0.5%→1%→1.5% progressive | $197,200 | 23.1% | 2.67y | 4.85y | 19.10% | ❌ | ✅ |
| 2 | 1% base, 2% on 6-win streak | $105,088 | 15.5% | 4.67y | 8.36y | 13.03% | ❌ | ✅ |
| 3 | Flat 2% | $380,952 | 31.5% | 2.00y | 3.83y | 24.87% | ❌ | ✅ |
| 4 | Kelly adaptive (1.5% base) | $184,183 | 22.2% | 2.67y | 6.00y | 20.23% | ❌ | ✅ |
| 5 | Balanced 1%→1.5%→2% | $307,456 | 28.7% | 2.17y | 4.60y | 23.22% | ❌ | ✅ |
| 6 | Aggressive 3% flat | $1,299,518 | 48.7% | 1.70y | 2.08y | 35.54% | ❌ | ✅ |
| 7 | Aggressive progressive 2%→2.5%→3% | $1,288,768 | 48.6% | 1.71y | 2.08y | 35.54% | ❌ | ✅ |
| 8 | Fast-start 1.5%→2%→2.5% scaling | $580,734 | 37.2% | 2.00y | 3.80y | 31.94% | ❌ | ✅ |
| 9 | Flat 0.5% (The5ers-safe) | $51,103 | 7.4% | 9.80y | N/A | 6.66% | ✅ | ❌ |
| **10** | **Flat 0.75% (DD-capped pick)** | **$72,442** | **11.3%** | **7.49y** | **N/A** | **9.88%** | **✅** | ❌ |
| 11 | Flat 1.0% (target ~10% DD) | $102,110 | 15.2% | 4.70y | 8.40y | 13.03% | ❌ | ✅ |
| 12 | Slow progressive 0.5→0.75→1.0% | $101,499 | 15.1% | 4.70y | 8.42y | 13.03% | ❌ | ✅ |
| 13 | Flat 1.0% with 3-loss-streak cut | $92,653 | 14.1% | 4.90y | 8.59y | 14.46% | ❌ | ✅ |

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

### Revised After 10% DD Hard Constraint + <5y Window
With the 10% DD cap and <5y window, only **Iter 10 (flat 0.75%)** at 9.88% backtest DD survives the DD constraint — but takes 7.49y to 2x. Iter 11 (flat 1.0%) hits 2x in 4.70y but breaches at 13% DD.

Iters 10-13 were added in this round to specifically target the <5y / <10% DD combination. None achieve both.

The honest math (single GBPUSD pair):
- Edge: PF 1.535 (after 1pip cost), 56% WR, ~67 trades/year
- Required CAGR for 2x in 5y: 14.9%
- At 14% CAGR, expected DD on this strategy is ~13% (proportional to risk × variance)
- To compress DD by 25% while keeping CAGR ≥ 15%, you'd need to reduce per-trade variance — but the edge is fixed (TP/SL ratio determined by Asian range)
- Therefore: **no single-pair sizing rule satisfies both constraints**

### Path to 2-3x in <5y with <10% DD: Multi-Pair Diversification
- Validate EURUSD and USDJPY London breakout edges (or other instruments)
- Run Iter 10/11 sizing on 3+ uncorrelated systems concurrently
- Aggregate equity curve sums returns linearly; aggregate DD is bounded by sqrt(N) × per-system DD if uncorrelated
- 3 uncorrelated systems @ 13% per-system DD → aggregate ~7.5% DD
- 3 systems × 15% CAGR each = ~45% portfolio CAGR → 2x in <2y

This is the only mathematically defensible path to the user's goal.

### v5 EA Default = Iter 10
Until multi-pair validation is complete, the v5 EA defaults to **Iter 10 (flat 0.75%)** as the only single-pair iteration under the 10% DD cap. CAGR ~11%, 2x in 7.5y. Trade-off accepted explicitly: DD safety over speed.

---

## The Final Sizing Rule (v5 EA Default — Iter 10, DD-capped)

```
RiskPercent           = 0.75%       (flat — Iter 10)
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

## Equity Trajectory (Iter 10 — DD-compliant)

Starting: $25,000
- After 1 year: ~$27,800 (+11.3%)
- After 2 years: ~$30,900
- After 3 years: ~$34,400
- After 5 years: ~$42,500
- After 7.5 years: ~$50,000 (2x reached)
- After 10 years: $72,442 (+190%)

This is slow but compliant with the 10% DD cap (9.88% backtest DD).

For 2x in <5y under 10% DD, see "Path forward" above — multi-pair diversification is the only mathematically defensible answer.

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
