# GBPUSD London Breakout — Master Context File
**Last updated:** 2026-04-27  
**Status:** EA v3.2 built, MT5 backtest in progress (flatline bug fixed)

---

## What This Project Is
A fully validated algorithmic trading strategy for GBPUSD, targeting deployment on The5ers prop firm (MT5). The strategy captures London session open range breakouts using Asian session price range as reference.

---

## Strategy Parameters (LOCKED — do not change)
| Parameter | Value |
|-----------|-------|
| TP multiplier | 1.5× Asian range |
| Min range | 15 pips |
| Max range | 60 pips |
| Trend filter | W1 EMA-26 (bullish above, bearish below, skip within 20 pips) |
| Asian session | 00:00–07:00 GMT |
| Entry window | 07:00–10:00 GMT (first H1 close outside range) |
| EOD exit | 17:00 GMT |
| Cost model | 1.0 pip (The5ers actual: 0.9–1.3 pips all-in) |

---

## Validation Results (Python, 2015–2024)
| Metric | Value |
|--------|-------|
| OOS Profit Factor | **1.733** (Period A: 1.638, Period B: 1.829) |
| DSR p-value | **p<0.0005** (2,000 permutations, all underperformed) |
| Bootstrap | **99%+** both periods |
| Win rate | **56–57%** |
| Max DD (Python) | **0.73%** (fixed 0.5% risk) |
| Configs tested | 180 (55 passed both periods) |

---

## Position Sizing — Council Iter 1 (Progressive)
| Phase | Days | Risk |
|-------|------|------|
| Phase 1 | 0–30 | 0.5% |
| Phase 2 | 31–90 | 1.0% |
| Phase 3 | 91+ | 1.5% |

Python backtest result: **$25k → $197,200** (CAGR 23.1%, Max DD 19.1%, 10 years)

---

## MT5 EA File
`mql5_ea/LondonBreakout_v3.mq5` — current version: **v3.2**

### Version History
| Version | Change |
|---------|--------|
| v1 | Initial build |
| v2 | CSV trade logger, trade context capture |
| v3 | Progressive risk sizing (Phase 1/2/3) |
| v3.1 | Fix CopyRates signature; cache Asian range once/day; bar-based breakout check |
| v3.2 | **Fix permanent halt bug** — daily DD now resets each day; trailing DD raised 8%→15% |

### v3.2 Bug Fixed (critical)
**Symptom:** Backtest flatlines from 2017 — only 94 trades over 9 years instead of ~600.  
**Root cause:** `g_trading_allowed = false` was set by BOTH daily DD (Layer 1) and trailing DD (Layer 2), but was never reset. One bad day permanently stopped all trading.  
**Fix:** Split into `g_daily_halt` (resets each morning in DailyReset) and `g_permanent_halt` (trailing DD + kill switch, never resets). Raised trailing DD default from 8% to 15%.

---

## MT5 Backtest Results So Far
| Report | Period | Trades | PF | Net Profit | Notes |
|--------|--------|--------|----|------------|-------|
| Report 16 | 2015–2024 | 94 | 1.44 | $7,937 | Flatlined from 2017 — v3.1 bug |
| v3.2 | TBD | ~500–600 expected | ~1.4+ | TBD | Fixed — needs retest |

---

## Broker: The5ers
- Spreads: 0.2–0.9 pips typical on GBPUSD
- Commission: $4/lot round-trip
- All-in cost: **0.9–1.3 pips** (backtest uses 1.0 pip — conservative)
- No time limit on evaluation
- Evaluation target: +8% on $10k account, max DD 4%
- Platform: MT5

**For The5ers live:** Set `InpTrailingDDPct = 4.0` (their max DD rule)  
**For personal account / backtest:** Leave at 15.0

---

## Next Steps (in order)
1. **Rerun MT5 backtest 2015–2024 with v3.2** — should show 500+ trades, no flatline
2. **Run 2025 forward test (Python)** — test champion config on truly unseen data
3. **Demo trade Phase 1** — 30 days at 0.5% risk on The5ers demo
4. **The5ers challenge** — $10k evaluation, EA running live

---

## Key Files
| File | Purpose |
|------|---------|
| `mql5_ea/LondonBreakout_v3.mq5` | EA — deploy this to MT5 |
| `mql5_ea/HOW_TO_BACKTEST.md` | MT5 backtest step-by-step guide |
| `STRATEGY_REPORT.md` | Full 13-section research report |
| `ITERATION_REPORT.md` | Sizing iteration results (6 variants, council picked Iter 1) |
| `results/gbpusd_top.json` | Top 5 validated configs |
| `validate_gbpusd.py` / `iterate_to_target.py` | Python backtests |

---

## How to Resume in a New Chat
1. Read this file first (`CONTEXT.md`)
2. Read `STRATEGY_REPORT.md` for full research background
3. Read `ITERATION_REPORT.md` for sizing decisions
4. Check latest git log: `git log --oneline -10`
5. Current task: rerun MT5 backtest with v3.2 EA, then run 2025 Python forward test
