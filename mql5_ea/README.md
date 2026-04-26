# MQL5 EA — GBPUSD London Breakout (In Development)

## Status
🔄 **Planning Phase Complete** — Council-approved architecture ready for build  
📋 **Next Step:** Build MQL5 implementation (2–4 hours estimated)  
🎯 **Target:** The5ers prop firm ($10k challenge, MT5 platform)

---

## Strategy Overview

**London Open Range Breakout** — A validated institutional FX edge:
- Captures Asian session range (00:00–07:00 GMT)
- Enters on first London open breakout (07:00–10:00 GMT)
- Exits EOD (17:00 GMT) or on SL/TP
- Weekly EMA-26 trend filter (optional but optimal)

**Winning Configuration (Locked):**
- TP Multiplier: 1.5x Asian range
- Min Range: 15 pips
- Max Range: 60 pips
- Trend Filter: Weekly EMA-26 ON

---

## Validation Results

| Metric | Period A (2015-19) | Period B (2020-24) | Average |
|--------|-------------------|-------------------|---------|
| **OOS Profit Factor** | 1.638 | 1.829 | **1.733** ✓ |
| **Bootstrap %ile** | 99.0% | 99.6% | **99%+** ✓ |
| **DSR p-value** | 0.0 | 0.0 | **< 0.0005** ✓ |
| **OOS Trade Count** | 102 | 82 | 184 total |
| **Win Rate** | 56% | 56.7% | **56-57%** |
| **Max Drawdown** | 0.73% | 0.69% | **0.73%** |

**Statistical Verdict:** ✓ PASS all gates (DSR, Bootstrap, IS/OOS degradation)

Full research: [STRATEGY_REPORT.md](../STRATEGY_REPORT.md)

---

## MQL5 Architecture (Council-Approved)

### 1. GMT Timing (Critical)
- Use `TimeGMT()` exclusively, never `TimeCurrent()`
- Daily offset verification with halt if drift > 15 min
- Prevents silent strategy failure from DST/broker clock shifts

### 2. State Machine (3 States, GlobalVariable Persistence)
```
WAITING (0)       → 00:00 GMT reset, scanning Asian bars
RANGE_SET (1)     → 07:00 GMT, levels calculated, watching London
TRADE_ACTIVE (2)  → Position open, monitoring SL/TP/EOD/safety
```
Survives EA restarts via GlobalVariables.

### 3. Position Sizing
- **0.5% equity risk per trade** (not 1% — buffer for 5% daily limit)
- Formula: `riskLots = (equity × 0.005) / (stopPips × pipValue)`
- Range filter: skip if Asian range < 15 or > 80 pips

### 4. Entry Logic
- Entry price = **range boundary** (NOT bar close)
  - Long: entry = Asian High, SL = Asian Low, TP = High + 1.5×range
  - Short: entry = Asian Low, SL = Asian High, TP = Low - 1.5×range
- One trade per day maximum
- W1 EMA filter (cached weekly, periods=26)

### 5. Safety Layers (4 Independent)
1. **Daily Loss Halt:** Close all if daily loss > 3.5%
2. **Trailing Equity DD:** Close all if peak-to-current DD > 4%
3. **Internal SL Enforcement:** Market-close if price passes SL (handles spread spikes)
4. **Manual Kill Switch:** `TradingEnabled` input to disable without restart

### 6. Exit Logic
- SL/TP placed as pending orders at entry
- EOD exit: 17:00 GMT bar open closes all positions
- Layer 3 enforces SL if broker execution fails

---

## Build Plan

**Phase 1: GMT Verification Module** (complete)
- `TimeGMT()` delta check on init and daily
- Halt if offset exceeds 15 minutes

**Phase 2: State Machine Scaffold** (pending)
- GlobalVariable persistence
- State transitions with logging

**Phase 3: Asian Range Detection** (pending)
- 00:00–07:00 GMT bar scan
- High/low/pip calculation with range filter

**Phase 4: W1 EMA Filter** (pending)
- Weekly cached `iMA()` call
- Trend direction logic

**Phase 5: London Entry Logic** (pending)
- 07:00–10:00 GMT breakout detection
- Range boundary entry price (not bar close!)

**Phase 6: Position Sizing** (pending)
- 0.5% equity risk formula

**Phase 7: SL/TP/EOD Management** (pending)
- Pending order placement
- 17:00 GMT market close

**Phase 8: 4-Layer Safety** (pending)
- Daily DD, trailing watermark, internal SL, manual kill

**Phase 9: Verbose Logging** (pending)
- Every decision point logged for audit

---

## Files & References

| File | Purpose |
|------|---------|
| `LondonBreakout_v1.mq5` | Main EA (in development) |
| `../STRATEGY_REPORT.md` | Full research & validation (✓ complete) |
| `../results/gbpusd_top.json` | Winning config parameters (locked) |
| `../validation_harness/strategy_london_breakout.py` | Python reference implementation |

---

## Pre-Deployment Checklist

- [ ] Read The5ers current challenge rules (daily DD, max DD, EA restrictions)
- [ ] Verify GMT offset against broker time on deployment
- [ ] Backtest on MT5 Strategy Tester (expect ~180 trades, PF ≈ 1.7)
- [ ] Verify entry prices are range boundaries (not bar closes)
- [ ] Demo trade 3–5 live trades, verify fills within 1.5 pips
- [ ] Stage 5: Deploy to The5ers $10k challenge

---

## Contact & Questions

Strategy validation: Claude (with LLM Council input)  
Development stage: Architecture planning complete, MQL5 build in progress

---

**Last Updated:** 2026-04-26  
**Status:** Ready for MQL5 build phase
