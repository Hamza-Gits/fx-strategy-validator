# How to Backtest LondonBreakout_v4.mq5 on MT5

## Step 1: Install the EA

1. Open MetaTrader 5
2. Click **File → Open Data Folder**
3. Navigate to `MQL5/Experts/`
4. Copy `LondonBreakout_v4.mq5` into this folder
5. In MT5, open **Navigator** panel (Ctrl+N) → Experts
6. Right-click `LondonBreakout_v4` → **Compile**
7. Should compile with 0 errors, 0 warnings

## Step 2: Run Strategy Tester Backtest

1. **View → Strategy Tester** (Ctrl+R)
2. Configure:
   - **Expert:** LondonBreakout_v4
   - **Symbol:** GBPUSD
   - **Timeframe:** H1
   - **Date Range:** **2014.11.01 - 2024.12.31** (CRITICAL — see below)
   - **Modeling:** Every tick based on real ticks (most accurate) OR M1 OHLC (faster)
   - **Initial Deposit:** 25000 (matches Python backtest)
   - **Leverage:** 1:100 (or your prop firm leverage)

> **CRITICAL — start date 2014.11.01 not 2015.01.01**
> The W1 EMA-26 needs 26 weeks of prior data to be valid. If you start at 2015.01.01, the first ~6 months will skip every trade because `UpdateW1EMA()` returns false (insufficient history). Starting 2 months earlier seeds the EMA before the first trade window. This single change recovers ~30 missed trades from early 2015.

3. Click **Inputs** tab — verify defaults:
   - InpTPMultiplier = 1.5
   - InpMinRangePips = 15
   - InpMaxRangePips = 60
   - InpUseTrendFilter = true
   - InpW1EmaPeriod = 26
   - InpUseProgressiveRisk = true
   - InpRiskPhase1Pct = 0.5 (Phase 1: 0-30 days)
   - InpRiskPhase2Pct = 1.0 (Phase 2: 31-90 days)
   - InpRiskPhase3Pct = 1.5 (Phase 3: 91+ days)
   - InpDailyLossLimitPct = 3.5 (Layer 1, resets daily)
   - InpTrailingDDPct = 25.0 (Layer 2 threshold — soft recovery on personal account)
   - InpHardHalt = false (personal account: soft recovery, NOT permanent halt)
   - InpRecoveryDays = 30 (days at 0.5% before watermark resets)
4. Click **Start**

## Step 3: Expected Results

### Personal account (default settings, $25k deposit, Iter 1 risk):
- **Trade count:** ~600-700 over 10 years (~67/year)
- **Final equity:** ~$197,000 (CAGR 23.1%, 2x at 2.67y)
- **Profit Factor:** ~1.5 (Python OOS PF was 1.733; some MT5 degradation expected)
- **Win Rate:** ~52-57%
- **Max DD:** ~19-25%
- **Equity curve:** Continuous growth, may have small dip+pause+recover sequences (soft recovery firing) but NEVER multi-year flat sections

### If trades stop and equity flatlines (the v3.2 bug):
This SHOULDN'T happen on v4. If it does:
1. Check the journal for `RECOVERY MODE` messages (this is normal — pause for 30 days then resume)
2. Check for `PERMANENT halt` messages — should only fire if InpHardHalt=true
3. If still flatlining, raise `InpTrailingDDPct` to 30 and re-run

### The5ers prop firm settings ($10k account, 4% max DD):
Change these from defaults:
- InpHardHalt = **true** (permanent halt if DD breached — evaluation fails anyway)
- InpTrailingDDPct = **4.0** (their max DD rule)
- InpDailyLossLimitPct = **3.0** (conservative)
- InpUseProgressiveRisk = **false** (flat 0.5% during evaluation)
- InpRiskPercent = **0.5** ($50 per trade on $10k)

Expected: +8% target reached in ~6-8 months. The5ers has no time limit.

## Step 4: Verify Logic in Journal

In the Strategy Tester **Journal** tab:
```
[2014.11.10 00:00:00] EA v4.0 Init | GBPUSD | PipSize=0.00010 | PipValue=1.00 | State=WAITING
[2014.11.10 00:00:00] Strategy: TP=1.50x | Range 15-60 | EMA-26 filter=ON
[2014.11.10 00:00:00] Risk: phase1=0.5% phase2=1.0% phase3=1.5% | DailyDD=3.5% | TrailingDD=25.0% | HardHalt=no (soft recovery)
[2015.01.05 07:30:00] Asian range computed: H=1.51234 L=1.50890 R=34.4 pips (7 bars)
[2015.01.05 07:30:00] STATE: WAITING -> RANGE_SET | trigger: Asian range valid: 34.4 pips
[2015.01.05 08:00:00] LONG breakout: close=1.51300 > AH=1.51234
[2015.01.05 08:00:00] Sizing: phase=1 risk=0.50% equity=$25000.00 risk_$=$125.00 stop=34.4 lots=0.01
[2015.01.05 08:00:00] ENTERED LONG | lots=0.01 | expected=1.51234 | actual=1.51235 | spread=1.60 | SL=1.50890 | TP=1.52366
[2015.01.05 17:00:00] EOD exit at hour 17 GMT
[2015.01.06 00:00:00] === DAILY RESET (00:00 GMT) ===
[2015.02.04 00:00:00] *** CRITICAL: PHASE TRANSITION: 1 -> 2 | Risk 1.00% | Day 30 ***
```

Verify:
- ✅ EMA seeds before first trade (no `EMA not yet seeded` after Dec 2014)
- ✅ Phase 1 → 2 transition around day 30
- ✅ Phase 2 → 3 transition around day 90
- ✅ Recovery mode (if any) shows `RECOVERY MODE` and 30 days later `Recovery period complete`

## Step 5: CSV Trade Log

The EA writes every trade to `MQL5/Files/LondonBreakout_v4_trades.csv`:
- 21 columns including direction, entry/exit, slippage, P&L, phase, recovery flag
- Use this to generate the full Excel spreadsheet via `python/generate_trade_excel.py`

## Step 6: Demo Trading (After Backtest Passes)

1. Open The5ers MT5 demo account
2. Attach EA to GBPUSD H1 chart
3. Set The5ers-specific inputs (see Step 3)
4. Enable **Algo Trading** button (top toolbar)
5. Watch journal for first 24 hours

## Step 7: Live Deployment Checklist

Before going live on The5ers $10k challenge:

- [ ] Backtest 2014.11.01–2024.12.31 shows PF > 1.3, no flatline
- [ ] Demo trades show fills within 1.5 pips of expected price
- [ ] GMT offset detected correctly (journal first line)
- [ ] Layer 1 daily DD halt tested (force equity drop, verify resumes next day)
- [ ] Layer 2 soft recovery tested (force watermark drop, verify pause + resume)
- [ ] Layer 4 manual kill switch tested (set InpTradingEnabled=false)
- [ ] InpHardHalt = true confirmed for prop firm mode
- [ ] InpTrailingDDPct = 4.0 confirmed for The5ers
- [ ] Position size sanity-check: 0.5% × $10k = $50 risk per trade

## Troubleshooting

### Equity flatlines mid-backtest
**Symptom:** Trades stop after some date, equity stays flat.
**v4 should NOT do this.** Check the journal:
- If `PERMANENT halt` appears: InpHardHalt is true. Set false for personal account.
- If `RECOVERY MODE` appears but never resolves: backtest may have ended during recovery (30-day pause). Extend the date range.
- If neither appears: report — could be a different issue.

### Trade count much lower than Python (< 500 over 10 years)
**Likely cause:** W1 EMA not seeded.
**Fix:** Start backtest 2014.11.01 (NOT 2015.01.01). The EA needs 26 weeks of prior data.

### Profit Factor doesn't match Python (~1.2 vs 1.73)
**Likely causes:**
1. Cost modeling — backtest models 1.0 pip, real spreads may be 1-2 pips higher.
2. W1 EMA filter may be slightly different in MT5 vs Python.
3. Phase progression — short backtests miss phase transitions.

---

## Support Files

- `LondonBreakout_v4.mq5` — Main EA (council-locked Iter 1, soft recovery, The5ers mode)
- `LondonBreakout_v3.mq5` — Prior version (kept for reference)
- `ARCHITECTURE.md` — Council-approved design specification
- `../STRATEGY_REPORT.md` — Full strategy validation report (DSR p<0.0005)
- `../ITERATION_REPORT.md` — All 9 sizing iterations + council verdict
- `../iterate_to_target.py` — Python backtest simulator (reproduces $25k → $197k)

---

**Status:** EA v4 council-locked, ready for MT5 backtest
**Last Updated:** 2026-04-28
**Verified:** Python logic validated (OOS PF 1.733, DSR p<0.0005)
