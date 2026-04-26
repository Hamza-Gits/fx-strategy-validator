# How to Backtest LondonBreakout_v1.mq5 on MT5

## Step 1: Install the EA

1. Open MetaTrader 5
2. Click **File → Open Data Folder**
3. Navigate to `MQL5/Experts/`
4. Copy `LondonBreakout_v1.mq5` into this folder
5. In MT5, open **Navigator** panel (Ctrl+N) → Experts
6. Right-click `LondonBreakout_v1` → **Compile**
7. Should compile with 0 errors, 0 warnings

## Step 2: Run Strategy Tester Backtest

1. **View → Strategy Tester** (Ctrl+R)
2. Configure:
   - **Expert:** LondonBreakout_v1
   - **Symbol:** GBPUSD
   - **Timeframe:** H1
   - **Date Range:** 2015.01.01 - 2024.12.31 (or 2025 forward test)
   - **Modeling:** Every tick based on real ticks (most accurate) OR M1 OHLC (faster)
   - **Initial Deposit:** 10000
   - **Leverage:** 1:30 (or your prop firm leverage)
3. Click **Inputs** tab — verify all params match defaults:
   - InpTPMultiplier = 1.5
   - InpMinRangePips = 15
   - InpMaxRangePips = 60
   - InpUseTrendFilter = true
   - InpW1EmaPeriod = 26
   - InpRiskPercent = 0.5
4. Click **Start**

## Step 3: Expected Results

Based on Python verification of the same logic:

**Full period 2015-2024 (locked params):**
- Trade count: ~600-700
- Profit Factor: 1.4-1.6 (with 2-pip cost model)
- Win Rate: ~52-53%
- Trade frequency: 60-70 per year (~5/month)

**Note:** Backtest results may vary slightly from Python due to:
- Tick-level precision in MT5 vs hourly bars in Python
- Spread modeling on real-tick playback
- Broker-specific spread/commission settings

## Step 4: Verify Logic Correctness

In the Strategy Tester, check the **Journal** tab. You should see:

```
[YYYY.MM.DD HH:MM:SS GMT] EA Initialized | Symbol=GBPUSD | PipSize=0.00010 ...
[YYYY.MM.DD HH:MM:SS GMT] Asian range computed: H=1.23456 L=1.23000 Range=45.6 pips (bars=7)
[YYYY.MM.DD HH:MM:SS GMT] STATE: WAITING -> RANGE_SET | trigger: Asian range valid: 45.6 pips
[YYYY.MM.DD HH:MM:SS GMT] LONG breakout: close=1.23500 > AH=1.23456, EMA=1.21000
[YYYY.MM.DD HH:MM:SS GMT] ENTERED LONG | lots=0.05 | entry=1.23456 | SL=1.23000 | TP=1.24140
[YYYY.MM.DD HH:MM:SS GMT] EOD exit triggered at hour 17 GMT
```

Verify:
- ✅ Asian range computed at 07:00 GMT
- ✅ Entry price = Asian high/low (NOT bar close)
- ✅ SL = opposite range boundary
- ✅ TP = entry + 1.5 × range
- ✅ EOD exit fires at 17:00 GMT

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

**No trades in backtest:**
- Check trend filter — try setting `InpUseTrendFilter = false` to see if EMA filter is too restrictive
- Check date range — needs at least 2 weeks for W1 EMA to seed

**Trade count much lower than Python (e.g., < 100 over 10 years):**
- Likely `iMA` PERIOD_W1 isn't returning weekly EMA correctly
- Check journal for "W1 EMA-26 refreshed: X.XXXXX" messages

**EOD exit not firing:**
- Verify GMT offset detection in journal
- Check if broker has 17:00 GMT bar (should always exist for forex)

## Support Files

- `LondonBreakout_v1.mq5` — Main EA source code
- `ARCHITECTURE.md` — Council-approved design specification
- `../STRATEGY_REPORT.md` — Full strategy validation report
- `../verify_ea_logic.py` — Python verification script

---

**Status:** EA ready for MT5 backtest  
**Last Verified:** Python logic match confirmed (PF 1.4+ with 2-pip cost model)
