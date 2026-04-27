# How to Backtest LondonBreakout_v3.mq5 on MT5

## Step 1: Install the EA

1. Open MetaTrader 5
2. Click **File → Open Data Folder**
3. Navigate to `MQL5/Experts/`
4. Copy `LondonBreakout_v3.mq5` into this folder
5. In MT5, open **Navigator** panel (Ctrl+N) → Experts
6. Right-click `LondonBreakout_v3` → **Compile**
7. Should compile with 0 errors, 0 warnings

## Step 2: Run Strategy Tester Backtest

1. **View → Strategy Tester** (Ctrl+R)
2. Configure:
   - **Expert:** LondonBreakout_v3
   - **Symbol:** GBPUSD
   - **Timeframe:** H1
   - **Date Range:** 2015.01.01 - 2024.12.31 (or 2025 forward test)
   - **Modeling:** Every tick based on real ticks (most accurate) OR M1 OHLC (faster)
   - **Initial Deposit:** 25000 (matches Python backtest)
   - **Leverage:** 1:100 (or your prop firm leverage)
3. Click **Inputs** tab — verify all params match defaults:
   - InpTPMultiplier = 1.5
   - InpMinRangePips = 15
   - InpMaxRangePips = 60
   - InpUseTrendFilter = true
   - InpW1EmaPeriod = 26
   - InpUseProgressiveRisk = true (v3 new)
   - InpRiskPhase1Pct = 0.5 (Phase 1: 0-30 days)
   - InpRiskPhase2Pct = 1.0 (Phase 2: 31-90 days)
   - InpRiskPhase3Pct = 1.5 (Phase 3: 91+ days)
   - InpDailyLossLimitPct = 3.5 (Safety Layer 1)
   - InpTrailingDDPct = 8.0 (Safety Layer 2)
4. Click **Start**

## Step 3: Expected Results

Based on Python backtest with 1.0-pip cost model ($25k account):

**Full period 2015-2024 (locked params, $25k, 1:100, progressive risk):**
- Trade count: ~667
- Final equity: ~$197,200 (CAGR 23.1%, Max DD 19.1%)
- Profit Factor: 1.733 (OOS combined, validated via DSR p<0.0005)
- Win Rate: ~56-57%
- Trade frequency: ~66 trades/year (~5-6/month)

**If using fixed 0.5% risk (no progressive scaling):**
- Final equity will be lower (~$110k-120k at year 10)

**Note:** Backtest results may vary slightly from Python due to:
- Tick-level precision in MT5 vs hourly bars in Python
- Spread modeling on real-tick playback
- Broker-specific spread/commission settings

## Step 4: Verify Logic Correctness

In the Strategy Tester, check the **Journal** tab. You should see:

```
[2015.01.05 00:00:00] GMT offset: 7200 sec (2.00 hours)
[2015.01.05 00:00:00] EA v2.0 Init | GBPUSD | PipSize=0.00010 | PipValue=1.00
[2015.01.05 00:00:00] Strategy: TP=1.50x | Range 15-60 | EMA-26 filter=ON
[2015.01.05 00:00:00] Risk=0.50% | DailyDD=3.50% | TrailingDD=8.00% | CSV=ON
[2015.01.05 07:30:00] Asian range: H=1.51234 L=1.50890 R=34.4 pips (7 bars)
[2015.01.05 07:30:00] STATE: WAITING -> RANGE_SET | trigger: Asian range valid: 34.4 pips
[2015.01.05 08:00:00] LONG breakout: close=1.51300 > AH=1.51234
[2015.01.05 08:00:00] Sizing: phase=1 risk=0.50% equity=$25000.00 risk_$=$125.00 stop=34.4 raw=0.36 final=0.01
[2015.01.05 08:00:00] ENTERED LONG | lots=0.01 | expected=1.51234 | actual=1.51235 | spread=1.60 | SL=1.50890 | TP=1.52366
[2015.01.05 17:00:00] EOD exit at hour 17 GMT
[2015.01.05 17:00:00] Closed #2147483648 | reason: EOD exit (17:00 GMT)
[2015.01.06 00:00:00] === DAILY RESET (00:00 GMT) ===
```

Verify:
- ✅ GMT offset detected correctly (check journal first line)
- ✅ Asian range computed during 07:00 GMT window
- ✅ Entry triggered on first H1 close outside range
- ✅ SL = opposite range boundary
- ✅ TP = entry + 1.5 × range
- ✅ EOD exit fires at 17:00 GMT
- ✅ Phase progression message appears after 30/90 days

## Step 5: GMT Offset Verification

**CRITICAL:** Most brokers run on GMT+2 or GMT+3 (Sunday) server time. The EA uses `TimeGMT()` which auto-corrects to actual GMT.

Watch the journal for the line:
```
GMT offset: 7200 seconds (2.00 hours)
```

This should match your broker's stated server-to-GMT offset. If it shows 0 or extreme values, manually set `InpGMTOffsetHours` to the correct value.

## Step 6: Demo Trading (After Backtest Passes)

1. Open The5ers MT5 demo account
2. Attach EA to GBPUSD H1 chart
3. Verify all input parameters
4. Enable **Algo Trading** button (top toolbar)
5. Watch journal for first 24 hours
6. Verify:
   - GMT offset detected correctly
   - Asian range computed at 07:00 GMT
   - All safety layers active
   - No order errors

## Step 7: Live Deployment Checklist

Before going live on The5ers $10k challenge:

- [ ] Backtest shows PF > 1.3 over 10-year period
- [ ] Demo trades show fills within 1.5 pips of expected price
- [ ] GMT offset verified correct
- [ ] Layer 1 daily DD halt tested (force equity drop)
- [ ] Layer 2 trailing DD halt tested (force peak drop)
- [ ] Layer 4 manual kill switch tested (set TradingEnabled=false)
- [ ] Read The5ers current rules (daily DD, max DD, news restrictions)
- [ ] Position size verified: 0.5% × $10k = $50 risk per trade

## Troubleshooting

### No trades in backtest
**Symptom:** Backtest runs but 0 trades executed

**Checks:**
1. Verify GMT offset is detected (check journal first line)
   - Should show `GMT offset: 7200 sec (2.00 hours)` or similar
   - If shows 0, set `InpGMTOffsetHours = 2` manually
2. Verify GBPUSD data exists for your date range
   - Download H1 history: right-click chart → Reload
3. Try disabling trend filter temporarily:
   - Set `InpUseTrendFilter = false` 
   - If trades appear, W1 EMA is too restrictive for this data

### Trade count much lower than Python (< 100 over 10 years)
**Likely cause:** W1 EMA not initialized properly in backtest

**Fix:**
1. Start backtest date 1-2 months EARLIER than 2015.01.01 to seed EMA
   - Try 2014.11.01 → 2024.12.31
2. Watch journal for message: `W1 EMA-26 refreshed: X.XXXXX`
   - If absent, check `g_w1_ema_handle` creation in OnInit

### "Expert compiled with errors" or crashes
**Symptom:** Backtest won't start

**Common fixes:**
1. Check MQL5 version in MT5 (Tools → Options → Expert Advisors → check "Allow live trading")
2. Rebuild: File → Recent Projects → right-click → Rebuild
3. Check Trade.mqh library exists (should auto-include from standard)

### Profit Factor doesn't match Python (~1.2 vs 1.73)
**Likely causes:**
1. Cost modeling — backtest models 1.0 pip, real spreads may be 1-2 pips higher
2. W1 EMA filter too strict — too many entries filtered out
3. Phase progression — if backtest is short (< 30 days), all trades at 0.5% risk
   - Full 10-year backtest should show phase transitions in journal

## Step 5: CSV Trade Log (v3 NEW)

During backtest, every trade is automatically logged to:
```
MQL5/Files/LondonBreakout_trades.csv
```

Download this file after backtest to analyze:
- Entry/exit times (GMT)
- Slippage (expected vs actual)
- PnL per trade
- Exit reasons (TP, SL, EOD)
- Duration in minutes

This is your live performance audit trail.

---

## Support Files

- `LondonBreakout_v3.mq5` — Main EA source code (progressive risk, CSV logging, 4-layer safeguards)
- `ARCHITECTURE.md` — Council-approved design specification (state machine, GMT timing, safety layers)
- `../STRATEGY_REPORT.md` — Full strategy validation report (DSR p<0.0005, 55/180 configs pass)
- `../ITERATION_REPORT.md` — Position sizing iteration results (Iter 1 council pick)
- `../iterate_to_target.py` — Python backtest simulator (reproduces $25k → $197k)

---

**Status:** EA v3 ready for MT5 backtest  
**Last Updated:** 2026-04-27  
**Verified:** Python logic validated (OOS PF 1.733, DSR p<0.0005)
