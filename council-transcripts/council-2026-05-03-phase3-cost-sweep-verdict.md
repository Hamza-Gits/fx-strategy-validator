# Council Verdict — Phase 3 Cost Sweep — Path Forward
**Date:** 2026-05-03
**Question:** Should we proceed to Phase 4 (300+ parameter sweep) or kill/modify the GBPUSD London Breakout EA, given Phase 3 cost sweep results?

**Phase 3 Results:** 667 trades, 2015-2024. PF 1.628 (0 pip RTT) → 1.335 (1.5 pip) → 1.171 (2.5 pip) → 0.965 (4.0 pip) → 0.794 (5.5 pip). Breakeven at 4 pip RTT. Realistic execution: 1.5-2.5 pip.

---

## Where the Council Agrees

**Execution cost is the load-bearing variable.** Every advisor and reviewer converges on this. The strategy lives or dies on RTT, not parameter tuning. MetaQuotes infrastructure puts you at 3.5-4.5 pip RTT, landing at or below the 4.0 pip breakeven (PF 0.965). Phase 4 on current broker infrastructure is pointless — sweeping parameters around a dead execution environment.

**The dataset is burned.** 55+ iterations on GBPUSD 2015-2024. Any parameter found in Phase 4 is artifact, not signal.

**The core logic has positive expected value.** Zero-cost PF 1.628 on 667 trades is not noise. The strategy concept works. The problem is infrastructure and execution cost, not the breakout logic itself.

---

## Where the Council Clashes

**EURUSD substitution: real test or theater?**
Quant calls it rigorous. Pragmatic Dev calls it cheap and fast. Four of five peer reviewers call it potentially meaningless — GBPUSD London breakout exploits BOE-driven volatility at 07:00 GMT. EURUSD does not have the same structural driver. A EURUSD pass does not confirm GBPUSD edge transfers. A fail does not kill it. Result is ambiguous regardless.

**Phase 4: kill or redirect?**
Risk Manager and Quant: dead (overfitting). Systems Architect: Python sweep fine, cheap — infrastructure is the problem. Pragmatic Dev: skip 300 iterations, run 5-10 targeted on EURUSD. Resolution: Phase 4 on GBPUSD is dead. Targeted sweep on fresh instrument after parity is a different operation.

**Implementation fixes: premature or essential?**
Systems Architect wants logger fix, DST test, ECN demo first. Risk Manager says fixing infrastructure for potentially dead strategy is backwards. Resolution: ECN cost validation first. If it kills the strategy, infrastructure is irrelevant. If ECN confirms viability, fix infrastructure before prop eval.

---

## Blind Spots the Council Caught

**Spread is not static — timing is catastrophic.** At London open 07:00-07:30 GMT, spread widens 3-8x before normalizing. Entries trigger in this exact window. The 1.5 pip "realistic ECN" assumption may be optimistic for the precise execution bar. Nobody has measured actual spread at entry bar open across 50+ historical instances. This potentially invalidates the ECN viability case entirely.

**DST handling may have corrupted historical PF numbers at source.** If session times are not properly GMT-anchored and DST transitions shifted session boundaries, portions of the 2015-2024 backtest used wrong entry windows. The PF numbers may themselves be wrong before any cost adjustment.

**Time-of-day attribution never tested.** Is the edge from 07:00 timing, the breakout magnitude filter, or the combination? If the edge is timing-dependent with pair-specific behavior, EURUSD substitution fails silently.

---

## The Recommendation

**Do not run Phase 4. Do not run EURUSD substitution yet. Do one thing first.**

The entire decision tree hinges on one empirical fact nobody has measured: **what is the actual spread at entry bar open on a legitimate ECN broker (IC Markets or Pepperstone raw) at London open, across 50+ historical instances?**

- If actual ECN RTT at entry is **1.3-2.0 pip**: strategy alive. EURUSD parity test becomes relevant. ECN-only deployment viable.
- If actual ECN RTT at entry is **2.5-3.5 pip** due to open-window spread spikes: strategy dead at current parameters on any broker. No sweep, no pair substitution, no infrastructure fix changes this.

Secondary: **verify DST handling in the EA** and confirm backtest session times were correctly anchored across 2015-2024. If DST was wrong, PF numbers are unreliable and all downstream decisions are built on corrupted inputs.

---

## The One Thing to Do First

**Open IC Markets or Pepperstone raw demo account. Run 20-30 live observations at London open (07:00-07:10 GMT). Record actual spread at the moment the entry bar opens. Calculate empirical RTT including commission. Compare against the PF-vs-RTT curve.**

Not Python. Not MT5. Not parameter sweep. Empirical spread measurement during the entry window on real ECN infrastructure.

That single measurement collapses the decision tree into a binary: the edge survives execution cost, or it does not.

- **RTT ≤ 2.0 pip:** Proceed → EURUSD parity confirmation → lock OOS holdout → 5-10 targeted tests → fix DST + logger → The5ers eval
- **RTT > 2.5 pip:** Kill GBPUSD cleanly → evaluate 5-minute delayed entry (post-spread normalization) before committing to new instrument

---

## Phase Plan Update

| Phase | Status | Result |
|-------|--------|--------|
| 1 | ✅ DONE | 86 signals in 2023 (inconclusive) |
| 2 | ✅ DONE | 98.6% Python/MQL5 alignment confirmed |
| 3 | ✅ DONE | Breakeven at 4 pip RTT; realistic 1.5-2.5 pip |
| **ECN Gate** | **⬅ NEXT** | **Measure actual London open spread on ECN** |
| 4 | ⏳ IF ECN PASSES | 5-10 targeted tests on fresh instrument (not 300+ sweep) |
| 5 | ⏳ AFTER | Lock OOS holdout (2025-2026, never touched) |
