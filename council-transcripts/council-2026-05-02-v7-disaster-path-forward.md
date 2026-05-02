# Council Verdict — v7 EA Disaster (Report 21) — Path Forward
**Date:** 2026-05-02
**Question:** What is the correct path forward for the GBPUSD London Breakout EA after v7 (Report 21) failed catastrophically (56 trades vs Python's 667, PF 0.70, DD 11.52%, net -$2,073)? Goal: 2-3x in <3 years, max 10% DD, pass The5ers $10k eval.

---

## ⚖️ Council Verdict: v7 Disaster — Path Forward

### ✅ Where the Council Agrees

Four of five advisors converge: **do not ship v8 yet, and do not go anywhere near The5ers.** Risk Manager, Quant, Architect, and Execution Realist all agree the evidence base is corrupted — by iteration, p-hacking, execution assumptions, or missing infrastructure. The 56→667 gap is not a bug to patch; it is a signal that nobody actually knows what the strategy does on either platform.

All five agree the trend-filter logic touches the gap. None except the Pragmatic Dev believe it explains the gap.

Implicit consensus: Python's PF 1.586 / DSR p<0.0005 claim is no longer credible as OOS. Iterated against, contaminated by parameter mismatches (EMA-20 vs EMA-26), never reconciled to live execution.

### ⚔️ Where the Council Clashes

**Ship vs Audit.** Pragmatic Dev wants 1-line fix + 4-week demo. Everyone else says fixing one flag without a parity oracle just produces v8's failure instead of v7's. Four peer reviewers independently flagged Pragmatic Dev as the biggest blind spot.

**Is the edge real at all?** Quant + Execution Realist think Python's 1.586 PF is artefact (selection bias / unrealistic costs). Risk Manager treats it as possibly real but unrecoverable through iteration. Architect is agnostic — wants the harness to find out. Pragmatic Dev assumes it's real.

**Python or MT5 as ground truth.** Pragmatic Dev implicitly trusts Python. Quant explicitly distrusts it ("MT5's 56 may be more honest"). Inverts the working assumption. Unresolved and matters.

### 🔍 Blind Spots the Council Caught (via Peer Review)

1. **Physical plausibility of 667 trades.** Nobody computed whether 667 signals over 10y on one pair from a 3h window is even achievable without double-counting or intrabar leakage. Hand-count expected signal days in 2023 — 30-second sanity check that precedes everything.

2. **The dataset is burned.** 2015-2024 GBPUSD iterated 55+ times across 7 versions. No clean OOS left. Locked holdout (different pair, different years, or walk-forward) required before any further claim.

3. **Regime decay.** GBPUSD London breakout in 2015-2019 (post-Brexit, pre-algo-saturation) may not exist 2024-2026. Five advisors debated implementation; zero questioned regime.

4. **Statistical indistinguishability.** 56 trades and 667 may be draws from same underlying edge distribution once variance is accounted for. PF 0.70 vs 1.586 across different N is not necessarily contradiction — could be noise. Bootstrap CIs never computed.

5. **Opportunity cost.** Months on v8-v12 of one EA vs testing three simpler strategies on locked OOS. Sunk cost is doing real damage.

6. **The5ers structural rules.** Daily loss limits, news-event gap exposure, weekend gap risk on a breakout strategy are structural constraints, not just DD math.

### 🎯 The Recommendation

**Build the parity oracle. Do not write v8. Do not demo. Do not touch The5ers.**

Architect's response is the only one that compounds — converts the next failure from "another mystery" into "a diff." Every other path keeps us inside the broken loop where each version exposes a new bug invisible until live.

In order:

1. **Parity oracle (1-2 weeks).** Python and MQL5 both emit per-bar decision traces with identical schema. Diff them. The 12x gap resolves into specific divergences, not guesses.

2. **Sanity-check the 667.** Hand-count expected signal days in 2023 from raw price data. If closer to 56, Python is wrong and strategy basis collapses.

3. **Reconcile costs honestly.** Re-run Python with 5.5 pip RTT. If PF drops below 1.3, edge was cost illusion. Stop.

4. **Locked OOS holdout.** Validate on data never iterated against — different pair, or 2025-2026 walk-forward.

5. **Only then:** 90+ session demo on real broker (not MetaQuotes-Demo), then The5ers.

If at any step "edge doesn't survive honest accounting," kill the strategy. Risk Manager's kill criterion stands: no v9. The lost prior 2-3x EA is survivorship bias. There are other strategies.

### ▶️ The One Thing to Do First

**Hand-count from raw 2023 GBPUSD H1 data:** how many days in 2023 actually had a valid London breakout signal under v7's spec (W1 EMA trend aligned, Asian range 15-60 pips, breakout occurred in 07:00-10:00 GMT window)?

- If answer ~15-30 → MT5 is right and Python is fiction
- If answer ~150+ → Python is plausible and parity oracle finds the bug

This is a 4-hour task — runs the existing Python harness with diagnostic logging on 2023 alone. Tells us which platform is lying before writing a single line of v8.

---

## My Proposed Next-Action Plan (Awaiting Your Approval)

Based on the Chairman's verdict, here is what I will do once you say "go":

### Phase 1 — Sanity Check the 667 (Today, ~1 hour)
Run Python harness on 2023 GBPUSD only with skip-reason logging. Count expected signal days. Outcome decides everything downstream.

### Phase 2 — Parity Oracle (1-2 days)
Build trace-emitter in both Python and MQL5:
- Python: emit `decision_trace_python.csv` per bar (timestamp, ema_w1, trend_dir, asian_high/low, signal_armed, entry_price, sl, tp, skip_reason)
- MQL5: emit `decision_trace_mql5.csv` with identical schema
- Build `parity_diff.py` that line-by-line compares both and outputs first divergence

This catches the v7 `g_trade_taken_today` bug AND any future bug.

### Phase 3 — Honest Cost Re-validation (~1 hour)
Re-run Python at 5.5 pip RTT (3 spread + 2 slippage + 0.5 commission) on 2015-2024.
- PF stays >1.3 → edge survives honest costs
- PF drops <1.3 → edge was cost illusion → kill strategy

### Phase 4 — Self-Driven Iteration (300+ runs, ~2 hours)
If edge survives Phase 3, run parameter sweep using the validation harness:
- TP multipliers: 1.0, 1.25, 1.5, 1.75, 2.0
- Min range: 10, 15, 20, 25 pips
- Max range: 50, 60, 70, 80, 100 pips
- EMA period: 20, 26, 30, 50
- W1 vs D1 trend filter
- Optional limit-on-retest entry vs market-on-close
- Total: ~400 configs, walk-forward validated

### Phase 5 — Lock OOS Holdout
If sweep produces a winner, validate on:
- 2025 forward data (true OOS, never seen)
- EURUSD, USDJPY (cross-pair generalisation)

### Phase 6 — Build v8 EA
Only after Phases 1-5 produce a validated, parity-tested spec.

### Phase 7 — Long Demo Forward Test
90+ sessions on a real broker before The5ers.

---

## Critical Findings to Lock In

- **Python validation used EMA-20, EA used EMA-26** — invalidates DSR p-value
- **180 configs / 55 passed** is p-hacking — gates were loose
- **2015-2024 dataset is burned** for any strategy claim
- **The5ers attempt before parity proven = ban risk**
- **Council kill criterion in effect:** no v9 if v8 doesn't match Python within 10% trade count + PF >1.3 OOS

---

*Council session conducted using Council.md skill (5 advisors in parallel + 5 anonymised peer reviews + Chairman synthesis).*
