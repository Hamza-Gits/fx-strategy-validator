# Council Verdict — XAUUSD Strategy Direction
**Date:** 2026-05-05
**Question:** For an XAUUSD MQL5 EA on Aqua Funded ($5k, 1:50 leverage, 3% daily DD, 6% total DD, 12-month 2-3x target, news-avoidance), pick Option 1 (NY-session breakout) or Option 2 (Daily ATR breakout)?

---

## Vote Tally
- Option 1 (NY-Session Breakout): 1 vote (Executor — code reuse)
- Option 2 (Daily ATR Breakout): 4 votes (Contrarian, First Principles, Outsider, Expansionist as fallback)

## Where the Council Agrees
Four of five advisors converge on OPTION 2 or a variant. Agreement is not about strategy elegance — it's about variance survival under Aqua's 3%/6% DD. Trade frequency is a liability on a $5k prop account. All five agree regime filtering matters more than strategy selection.

## Where the Council Clashes
**Shipping speed vs structural fit.** Executor: 85% v8 code reuse, 1 day vs 2 weeks. Reviewers unanimously flagged this as sunk-cost reasoning.
**Simplicity vs stacking.** Expansionist wants both + DXY classifier + variable Kelly. Others want one clean rule set.

## Blind Spots Caught
1. **Aqua's non-edge rules** (consistency rule, min trading days, weekend holding) constrain choice BEFORE expectancy math.
2. **No ruin-probability Monte Carlo.** 2-3x in 12 months with 6% hard floor is path-dependent.
3. **Target may be unreachable.** ~1% monthly at sub-3% daily variance = Sharpe regime retail EAs rarely hit.

## The Recommendation
**OPTION 2 (Daily ATR Breakout)** with these adjustments:

**Required additions:**
- D1 ADX > 20 regime filter (kills chop)
- -2% daily soft circuit (1% buffer below Aqua's 3% hard rule)

**Required deletion:**
- Drop EITHER W1 EMA-26 bias OR ATR magnitude filter (running both starves signal count). Forward-test which to keep.

**Rejected:**
- Option 1 (Executor's vote) — gold's session liquidity profile structurally weak for 13:00 GMT range break
- Expansionist stacking — premature complexity for $5k account without validated parity oracle

## The One Thing to Do First
**Run 5-year Monte Carlo on Option 2 historical signals BEFORE writing MQL5.**

Inputs: 0.5% risk, 1:2 R/R, assumed 40% WR, Aqua 3%/6% DD rules, consistency/min-days constraints.

Required outputs:
- P(blowup) < 15%
- P(2x in 12 months) ≥ 25%
- P(3x in 12 months) — informational

If gates fail: honest answer is "lower target or increase capital." Build parity oracle only after gate passes.

---

## Monte Carlo Result (Phase 0)

**100k simulations across 5 risk levels × 4 win rates:**

At 0.5% risk (original plan), even 55% WR gives only P(2x) = 5.94% with median final $8,532 (1.7x). Targets are unreachable.

**Sweet spot: 1.0% risk per trade + ≥50% win rate:**
- P(blowup) = 5.98%
- P(2x) = 68.6%
- P(3x) = 5.7%
- Median final equity = $10,896

**Risk architecture updated:**
- Risk per trade: 0.5% → **1.0%** flat
- Daily soft circuit: 2.0% → **2.5%** (1% buffer below Aqua's 3% hard rule)
- Total DD circuit: 5.0% (unchanged, 1% buffer below Aqua's 6%)
- Required WR threshold: must demonstrate ≥50% WR in validation. If <50%, lower target to 1.5x.

## Phase Plan Update

| Phase | Status | Note |
|---|---|---|
| 0a — Data + OOS lock | ✅ DONE | XAUUSD H1 2013-2025 committed; 2021-2025 sealed |
| 0b — Council vote | ✅ DONE | Option 2 (Daily ATR Breakout) wins |
| 0c — Monte Carlo ruin gate | ✅ DONE | 1% risk + 50% WR required for 2x viability |
| **1 — Phase 1 signal sweep** | **⬅ NEXT** | Sweep ATR threshold + W1/D1 trend filters on IS data |
| 2 — Phase 3 cost sweep | ⏳ | Aqua-specific spread + commission (XAUUSD pip = $0.01) |
| 3 — Phase 4 OOS validation | ⏳ | Bootstrap PF, Deflated Sharpe, ≥50% WR confirmation |
| 4 — MQL5 EA build | ⏳ | XAUUSD_v1.mq5 with hardcoded Aqua DD limits |
| 5 — User MT5 backtest | ⏳ | Final verdict |
