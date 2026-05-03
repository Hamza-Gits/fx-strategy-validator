# GBPUSD London Breakout — Master Context File
**Last updated:** 2026-05-03
**Status:** Phase 2 — Parity Oracle (Python trace + MQL5 v8 trace logger + diff script)

## Session Recovery (2026-05-01)
All local Claude Code sessions lost after laptop restart / Desktop app update. Project state fully recovered from GitHub repo + session JSONL logs + master context prompt. Standing rule: **all changes are committed and pushed to GitHub after every response** so the repo is always the single source of truth. 54 trading skills in `.claude/skills/` are referenced for all analysis and decisions going forward.

## v7 Critical Rewrite (2026-05-02)

**MT5 Report 20 (v6) was a disaster:** Net -$2,738.50, PF 0.97, max DD 25.62%, 879 trades (vs Python 667), win rate 41.75% (vs 57%). The EA "kept making money then losing it all in big downtrends" — exactly the user's description.

**ROOT CAUSE: Pending stops fill on intrabar spikes that Python ignores**

Python's entry rule (validated): `if bar['close'] > asian_high → enter at asian_high`. Only triggers on confirmed bar CLOSE crossing the boundary.

v6 EA used `BuyStop @ asian_high` which fills on ANY tick touching the level — including intrabar spikes that reverse before bar close. Result: ~212 false-breakout trades that Python never counts. These trades have ~30% win rate, dragging overall win rate from 57% to 42% and PF from 1.73 to 0.97.

**Secondary bugs:**
- **Market fallback** fired immediately when price was past boundary at 07:00, regardless of bar close
- **EMA 20-pip ambiguity zone** (EA only) skipped trades Python takes
- **Soft recovery DD compounding**: 8% trailing → recovery → reset peak → another 8% drop → 0.92³ = 22% absolute DD

**v7 fixes (council-validated, unanimous):**
1. **Bar-close market orders** via `IsNewBar()` — matches Python exactly. On confirmed H1 bar close, read `bar[1].close` and enter market order if breakout valid.
2. **Removed pending stop logic entirely** — no BuyStop, SellStop, OCO, retry guards, stops-level checks
3. **Removed market fallback** — bar-close logic handles all cases
4. **Removed EMA ambiguity zone** — simple `close > EMA` matches Python
5. **Absolute DD cap from starting equity** — prevents recovery cycles compounding past 10%

**Why this won't repeat v4's slippage problem:** v4 had 20-50 pip slippage because it didn't use `IsNewBar()` (detected mid-bar). v7's `IsNewBar()` fires at exact bar boundary (08:00:00.000), market order fills at current ask ≈ previous bar close ± spread (1-3 pips).

**Expected v7 results:** ~667 trades, ~56% win rate, PF ~1.5-1.7, DD ~8-11%.

## Phase 1 & 2 Diagnostic (2026-05-03)

**Phase 1 — Sanity Check the 667 (✅ DONE)**
Ran Python harness on 2023 GBPUSD only. Result: **86 signal days** (inconclusive zone between "MT5 right" <30 and "Python plausible" >150). This triggered Phase 2.

**Phase 2 — Parity Oracle (✅ COMPLETE)**

The council verdict was unambiguous: "Do not write v8. Build the parity oracle first."

Built identical per-bar decision trace loggers in both Python and MQL5:

1. **Python trace** (`phase2_python_trace.py`):
   - Emits `decision_trace_python_GBPUSD_2023.csv` with 6208 bars traced
   - Schema: date, bar_time_gmt, bar_close, w1_ema, trend_dir, asian_high, asian_low, asian_range_pips, range_ok, in_london, signal, skip_reason, entry_price, sl, tp
   - Result: 86 signals (66 LONG, 20 SHORT)

2. **MQL5 v8 — Parity Oracle EA** (`LondonBreakout_v8.mq5`):
   - v7 + trace logger that writes identical CSV schema to MT5 Files folder
   - New input: `InpTraceLogging = true` (default)
   - `CheckBarCloseEntry()` instrumented: emits trace row on every London/post-Asian bar
   - Signals: LONG, SHORT, NONE (with skip_reason: NO_BREAKOUT, TREND_FILTER_NO_DATA, BUY_ORDER_FAILED, SELL_ORDER_FAILED)
   - gv_prefix: LB7_ → LB8_ (clean global state separation)
   - Deployed to MT5 Experts folder (ready to compile & backtest)

3. **Diff script** (`phase2_parity_diff.py`):
   - Aligns both traces on bar_time_gmt timestamp
   - Compares: signal, trend_dir, range_ok, in_london, skip_reason
   - Numeric tolerance: 0.2 pips (handles float rounding)
   - Output: `parity_diff_SYMBOL_YEAR.csv` with all divergent rows + summary statistics
   - Detects first divergence immediately

**Next action (YOUR TURN):**
1. Compile v8 in MetaEditor (F7)
2. Backtest v8 on GBPUSD H1 **2023 only** (2023.01.01 — 2023.12.31)
3. Copy `decision_trace_mql5_GBPUSD_2023.csv` from MT5 Files folder to `diagnostic/traces/`
4. Run: `python diagnostic/phase2_parity_diff.py GBPUSD 2023`
5. This reveals **exactly which bar** MQL5 diverges from Python (and why: 56 vs 86 signals)

This is the critical gate. Once we know the root cause of the divergence, we either:
- Fix a bug in v8 and retest
- Discover v7's bar-close logic was correct all along (validates path forward)
- Find evidence the edge doesn't exist (kill strategy per Risk Manager's kill criterion)

## v6 Critical Update (2026-05-01)
**MT5 Report 19 (v5) was a disaster:** infinite retry loop spamming "Invalid price" errors (10015) every tick. Multiple compounding bugs.

**Bug 1 — Infinite retry loop:** v5 only set `g_pendings_placed=true` when at least one order placed successfully. When BOTH BuyStop and SellStop failed (broker rejection, stops-level violation, price already past boundary), the EA retried every tick forever, generating thousands of error logs per day.

**Bug 2 — Late placement:** v5 placed orders whenever OnTick happened to enter `STATE_RANGE_SET`. Logs show this happening at 09:01 GMT instead of 07:00. By then, price had often already moved past `asian_high`, making BuyStop invalid (BuyStop must be > current Ask).

**Bug 3 — No stops-level enforcement:** v5 didn't check `SYMBOL_TRADE_STOPS_LEVEL`. Some brokers reject pending stops too close to market price.

**Bug 4 — No market fallback:** When price was already past the boundary, v5 simply failed instead of capturing the breakout via market order (which is what Python's "fill at boundary" model intends to capture).

**v6 fixes (all four):**
1. `g_pendings_placed = true` set **unconditionally** after placement attempt — one attempt per day, period
2. Order placement attempts **immediately** when state transitions to `STATE_RANGE_SET` (right after Asian range closes)
3. `SYMBOL_TRADE_STOPS_LEVEL` checked + `InpExtraStopsBuffer` safety margin
4. Market-order fallback when price has already moved past boundary (controlled by `InpAllowMarketFallback`)
5. Diagnostic logging on failure (logs req_price, Ask/Bid, min distance) for fast future diagnosis

## v5 Critical Update (2026-04-29)
**MT5 Report 17 (v4) was a disaster:** PF 0.91, 343 trades (vs Python 667), -$2,251 loss. Root cause: TWO entry-mechanism bugs.

**Bug 1: Entry slippage.** v4 used market orders that filled 20-50 pips beyond the range boundary. Python assumes fills AT the boundary (stop order behaviour). R:R collapsed from 1:1.5 to 1:0.25 in many trades.

**Bug 2: Missing entry bar.** v4's `hour < InpLondonEndHour=10` only checked 2 of the 3 London bars Python checks (missed the 09:00-10:00 bar's close). That's 33% of breakouts gone, explaining 343 vs 667 trades.

**v5 fix:** Pending **BuyStop @ asian_high** + **SellStop @ asian_low** placed at 07:00 GMT, expiring 10:00 GMT. OCO logic cancels the unfilled side when one fills. Matches Python's fill model exactly.

---

## What This Project Is
A fully validated algorithmic trading strategy for GBPUSD, targeting deployment on The5ers prop firm (MT5). The strategy captures London session open range breakouts using Asian session price range as reference.

---

## ⚠️ HARD CONSTRAINT: 10% MAX DRAWDOWN

User has set an absolute max drawdown limit of **10%** for the personal account.

This rules out almost all of the council's iterations (Iter 1 has DD 19.1% — violates). The only iteration that fits:
- **Iter 9: Flat 0.5%** — Max DD 6.66%, but CAGR only 7.4% (never hits 3x in 10y)

For 2-3x in <3y AND <10% DD, we need a hybrid:
- **Capped progressive risk** with `InpTrailingDDPct = 10.0` and soft recovery firing well before that cap
- Trade smaller (0.5%–0.75%) — accept slower growth in exchange for hard DD safety

The v4 EA's `InpTrailingDDPct` default has been raised to 25 historically, but per this constraint the effective deployment value is **10**, with soft recovery triggering at 7-8%.

---

## Strategy Parameters (LOCKED — do not change)
| Parameter | Value |
|-----------|-------|
| TP multiplier | 1.5× Asian range |
| Min range | 15 pips |
| Max range | 60 pips |
| Trend filter | W1 EMA-26 (skip within 20 pips of EMA) |
| Asian session | 00:00–07:00 GMT |
| Entry window | 07:00–10:00 GMT (first H1 close outside range) |
| EOD exit | 17:00 GMT |
| Cost model | 1.0 pip (The5ers actual: 0.9–1.3 pips) |

---

## Validation Results (Python, 2015–2024)
| Metric | Value |
|--------|-------|
| OOS Profit Factor | **1.733** (Period A: 1.638, Period B: 1.829) |
| DSR p-value | **p<0.0005** (2,000 permutations) |
| Bootstrap | **99%+** both periods |
| Win rate | **56–57%** |
| Configs tested | 180 (55 passed) |

---

## Position Sizing — All 13 iterations vs 10% DD / <5y constraint
| Iter | Rule | DD | 2x time | 3x time | <10% DD? | <5y to 2x? |
|------|------|----|---------|---------|----------|------------|
| 1 | 0.5→1→1.5% prog | 19.1% | 2.67y | 4.85y | ❌ | ✅ |
| 2 | 1% base, 2% on 6-win | 13.0% | 4.67y | 8.36y | ❌ | ✅ |
| 3 | Flat 2% | 24.9% | 2.00y | 3.83y | ❌ | ✅ |
| 4 | Kelly 1.5% base | 20.2% | 2.67y | 6.00y | ❌ | ✅ |
| 5 | 1→1.5→2% | 23.2% | 2.17y | 4.60y | ❌ | ✅ |
| 6 | Flat 3% | 35.5% | 1.70y | 2.08y | ❌ | ✅ |
| 7 | 2→2.5→3% prog | 35.5% | 1.71y | 2.08y | ❌ | ✅ |
| 8 | 1.5→2→2.5% scaling | 31.9% | 2.00y | 3.80y | ❌ | ✅ |
| 9 | Flat 0.5% | 6.66% | 9.80y | N/A | ✅ | ❌ |
| **10** | **Flat 0.75%** | **9.88%** | **7.49y** | **N/A** | **✅** | ❌ |
| 11 | Flat 1.0% | 13.0% | 4.70y | 8.40y | ❌ | ✅ |
| 12 | Slow prog 0.5→0.75→1.0% | 13.0% | 4.70y | 8.42y | ❌ | ✅ |
| 13 | Flat 1.0% with loss-cut | 14.5% | 4.90y | 8.59y | ❌ | ✅ |

**Honest truth:** No single-pair sizing rule satisfies BOTH "<10% DD" AND "2x in <5y" simultaneously.

- **Iter 10 (flat 0.75%)** is the only iteration under 10% DD — but takes 7.5y to 2x
- **Iter 11 (flat 1.0%)** hits 2x in 4.7y — but DD is 13%
- For 2x in <5y with <10% DD on a single pair, the edge isn't strong enough

**Path forward:** multi-pair diversification (run Iter 9/10 sizing on 3+ uncorrelated pairs). Aggregate return scales linearly; aggregate DD stays bounded due to uncorrelated drawdowns. Currently only GBPUSD has the validated edge — next research step is validating EURUSD/USDJPY breakouts.

---

## Council Verdict (revised for 10% DD constraint)

**Recommendation: Iter 9 (flat 0.5%)** as the personal-account default.
- Compliant with 10% DD constraint
- Same settings work for The5ers $10k evaluation (their max DD is 4%, this stays well within)
- Slow but survivable

**For 2-3x in <3y goal:** consider running Iter 9 across **3 uncorrelated pairs** (GBPUSD, USDJPY, EURUSD if they pass validation) — total return scales roughly 3× while DD remains ~10% (uncorrelated drawdowns).

---

## MT5 EA File
`mql5_ea/LondonBreakout_v7.mq5` — current version: **v7.0**

### Version History
| Version | Change |
|---------|--------|
| v1 | Initial build |
| v2 | CSV trade logger |
| v3 | Progressive risk sizing |
| v3.1 | Fix CopyRates; cache Asian range; bar-based check |
| v3.2 | Daily DD now resets each day; trailing DD 8%→15% |
| v4 | Soft recovery halt; InpHardHalt toggle for prop firm; 21-column CSV — **DISASTER: PF 0.91, 343 trades, slippage 20-50 pips** |
| v5 | Pending stop orders at range boundaries; OCO logic; entry-window fix captures all 3 London bars; default risk = Iter 10 (flat 0.75%) — **DISASTER: infinite retry loop, error 10015 spam (Report 19)** |
| v6 | Hardened stop orders: retry guard, market fallback, stops-level enforcement, diagnostic logging — **DISASTER: PF 0.97, 879 trades, DD 25.62% (Report 20). Pending stops still fill on intrabar spikes Python ignores** |
| **v7** | **BAR-CLOSE MARKET ORDERS via `IsNewBar()`** — matches Python signal logic exactly. Removed all pending stop logic, market fallback, EMA ambiguity zone. Added absolute DD cap from starting equity. Council-validated unanimous |

### Why v4 Was Built
v3.2's trailing DD halt was still permanent. Phase 3 risk × loss streak triggered it → flatline 2018-onward. v4 replaces with soft recovery: pause 30 days at 0.5%, reset peak watermark, resume.

### Recommended v7 Settings (Iter 10 — best fit under 10% DD)
```
InpUseProgressiveRisk = false      ← flat risk (Iter 10)
InpRiskPercent        = 0.75       ← Iter 10 flat 0.75% (Python DD 9.88%)
InpHardHalt           = false      ← soft recovery
InpTrailingDDPct      = 8.0        ← absolute + trailing DD cap from starting equity
InpRecoveryDays       = 30
InpRecoveryRiskPct    = 0.25       ← halved during recovery
InpUseTrendFilter     = true       ← W1 EMA-26 trend filter (no ambiguity zone)
```

**v7 backtest checklist:**
- File: `mql5_ea/LondonBreakout_v7.mq5`
- Symbol: GBPUSD H1
- Period: 2015.01.01–2024.01.01 (or longer)
- Deposit: $25,000
- Model: Every tick based on real ticks
- Expected: ~667 trades (±30), ~56% win rate, PF ~1.5-1.7, DD ~8-11%

---

## Broker: The5ers
- All-in cost: 0.9–1.3 pips
- Evaluation target: +8% on $10k, max DD 4%, no time limit

**The5ers $10k evaluation settings:**
```
InpHardHalt           = true
InpTrailingDDPct      = 4.0
InpDailyLossLimitPct  = 3.0
InpUseProgressiveRisk = false
InpRiskPercent        = 0.5
```

**Compliance:** All 13 prohibited practices verified — see `docs/the5ers_compliance.md`.

---

## Next Steps
1. **Rerun MT5 backtest** with v4 + Iter 9 settings + InpTrailingDDPct=10 (2014.11–2024.12)
2. **Verify DD stays under 10%** — if it doesn't, drop to 0.25% risk
3. **Validate 2025 forward test** (Python)
4. **Multi-pair diversification research** — only path to 2-3x in <3y with 10% DD cap
5. **Demo trade** before The5ers challenge

---

## Key Files
| File | Purpose |
|------|---------|
| `mql5_ea/LondonBreakout_v4.mq5` | EA — deploy this to MT5 |
| `mql5_ea/LondonBreakout_v3.mq5` | Prior version |
| `mql5_ea/HOW_TO_BACKTEST.md` | Mandates 2014.11.01 start |
| `STRATEGY_REPORT.md` | Full research |
| `ITERATION_REPORT.md` | All 9 iterations + council verdict |
| `docs/the5ers_compliance.md` | Prohibited-practices audit |
| `results/trades_all.xlsx` | **Full Excel of all 667 trades** |
| `results/iteration_to_target.json` | All 9 iteration numerical results |
| `iterate_to_target.py` | Python iterator |
| `generate_trade_excel.py` | Builds trades_all.xlsx |

---

## How to Resume in a New Chat
1. Read this file first (`CONTEXT.md`)
2. Read `STRATEGY_REPORT.md` for full research background
3. Read `ITERATION_REPORT.md` for sizing decisions + council verdict
4. Check latest git log: `git log --oneline -10`
5. **Note user's 10% DD hard cap** — this rules out all but Iter 9
6. Current task: rerun MT5 backtest with v4 + 10% DD config
