# MQL5 EA Architecture — Council-Approved Design

## Convened Council: 5 Advisors on EA Design

The architecture below is the result of an LLM Council convened to determine the safest, most robust design for deploying a validated trading strategy on a real $10k prop firm evaluation.

**Advisors:**
- The Executor (implementation focus)
- The Contrarian (failure mode focus)
- The First Principles Thinker (architectural focus)
- The Expansionist (scaling & opportunity)
- The Outsider (fresh eyes, bug detection)

**Verdict:** Consensus on critical safety-first architecture. See section "Council Consensus" below.

---

## Council Consensus (What All 5 Advisors Agreed On)

### 1. GMT Timing is Non-Negotiable
**Unanimous Finding:** Wrong session times kill silently — no errors, no alerts, just slow account bleed while you assume it's working.

**Implementation:**
- Use `TimeGMT()` exclusively — never `TimeCurrent()`
- On EA init and at 00:00 GMT daily: compute delta = `TimeGMT() - TimeCurrent()`
- If `|delta| > 900 seconds (15 min)`, set `TradingAllowed = false`, log alert, **halt**
- Reason: Brokers shift server clocks for DST. Your Asian range 00:00–07:00 GMT becomes 01:00–08:00 silently, invalidating the entire validated strategy

### 2. State Machine Must Survive Restarts
**Unanimous Finding:** EA memory loss = missed trades or invalid trades.

**Implementation:**
- Store state in **GlobalVariables**, not local variables
- GlobalVariables survive EA restart, recompilation, and VPS reboot
- On init: read state from GlobalVariables first — if `TRADE_ACTIVE`, resume monitoring immediately
- Every state transition logs timestamp, old state, new state, and trigger values

### 3. Verbose Logging is Essential
**Unanimous Finding:** 18–20 trades per year means you cannot distinguish a broken EA from a cold streak without complete audit trail.

**Implementation:**
- Log every decision: range formed Y/N, EMA filter pass/fail, trade taken/skipped + reason
- Log every session window: "Asian session start", "London window open", "EOD exit"
- Log every trade: entry price, entry spread, exit reason (TP/SL/EOD/safety)
- Goal: reconstruct every trade from MT5 journal alone

### 4. The Expansionist is Wrong
**Unanimous Finding:** Multi-pair expansion during prop evaluation is dangerous scope creep.

**Implementation:**
- Build GBPUSD only for this EA
- Do not add pairs until GBPUSD passes The5ers challenge
- Reason: Pairs correlate during news events — multi-pair simultaneously entering on news blows the daily limit faster. Unproven live system + scope creep = higher failure risk.

---

## Where the Council Clashed (Resolved)

### Contrarian vs. Executor on Sample Size
**Contrarian:** "18–20 trades/year is statistically insufficient. The backtest p-value doesn't translate to live. You can't know if the EA works."

**Executor:** "Build anyway. This is the only way to find out. Lock parameters, log everything, and don't optimize until 60+ trades."

**Verdict:** Both are right. Accept the statistical limitation consciously. This is why logging and safety layers are critical — you can't rely on the edge being real until you have volume, so over-protect against edge failure.

---

## Blind Spots Caught (Missed by All 5 Advisors, Caught by Peer Review)

### 1. The5ers Trailing Drawdown (CRITICAL)
**Blind Spot:** All advisors assumed drawdown is calculated from starting balance. It's not.

**Reality:** The5ers calculates trailing drawdown from **equity peak**. A winning streak followed by a losing cluster can breach the drawdown limit even with positive overall P&L.

**Fix:** Layer 2 Safety monitors running peak equity. On every tick:
```
if peak_equity = 0:
    peak_equity = account_equity
else:
    peak_equity = max(peak_equity, account_equity)

if (peak_equity - account_equity) / peak_equity > 0.04:
    close all, halt permanently
```

### 2. Single-Trade Max Loss Can Breach Daily Limit
**Blind Spot:** All advisors focused on position sizing but missed the range cap interaction.

**Reality:** If the Asian range on a given day is 150 pips instead of typical 30–50, a 0.5% risk trade with 1.5x TP means a massive position. One bad trade can breach the daily loss limit before EOD exit.

**Fix:** Range filter in entry logic: skip trade if `range_pips < 15 OR range_pips > 80`. This is a **trade qualification gate**, not a sizing gate.

### 3. The5ers Rules Are the True Specification
**Blind Spot:** All advisors gave generic prop firm safeguard advice without reading The5ers actual rulebook.

**Reality:** The5ers has specific rules on news trading, weekend holding, EA restrictions by challenge tier. A technically correct EA that violates a platform rule fails the evaluation regardless of P&L.

**Fix:** Before building, read current The5ers challenge rules:
- Exact daily drawdown limit and calculation basis
- Maximum total drawdown and trailing vs static
- News trading restrictions
- Weekend holding prohibition
- EA usage policies

### 4. Spread Spikes at London Open
**Blind Spot:** Advisors mentioned spread spikes but didn't go far enough.

**Reality:** At 07:00 GMT London open, spreads spike to 3–5 pips for 2–3 minutes. A 15-pip minimum range filter might be entirely eaten by a spread spike before the trade entry fills. Plus, a missed SL during a spread spike + no internal equity check = instant account termination.

**Fix:** Layer 3 Safety (internal SL enforcement) — on every tick while `TRADE_ACTIVE`, compute whether live equity drop implies price has passed the SL level. If so, market-close immediately.

---

## Final Architecture (Council Verdict)

### Core Components

#### 1. GMT Verification Module
```
OnInit():
    compute delta = TimeGMT() - TimeCurrent()
    if |delta| > 900 sec:
        TradingAllowed = false
        log "GMT offset error: delta = {delta}"
        halt

OnTick() (daily):
    at 00:00 GMT:
        recompute delta
        if |delta| > 900 sec:
            close all, TradingAllowed = false
```

#### 2. State Machine (3 States)
```
State 0: WAITING
    - 00:00 GMT daily reset
    - Scan bars from 00:00 to 07:00 GMT
    - Calculate Asian high, low, range
    - If range passes filter (15–80 pips) → RANGE_SET

State 1: RANGE_SET
    - 07:00 GMT, ready to trigger
    - Watch 07:00–10:00 GMT London window
    - On first bar close outside Asian range AND EMA filter pass → enter, TRADE_ACTIVE

State 2: TRADE_ACTIVE
    - Position open, monitor SL/TP/EOD/safety
    - On SL hit OR TP hit → close, WAITING
    - On 17:00 GMT → market close (EOD), WAITING
    - On safety trigger → close, halt

All state + level data in GlobalVariables.
```

#### 3. Position Sizing
```
stop_pips = abs(entry_price - stop_loss_price)
risk_lots = (AccountEquity() * 0.005) / (stop_pips * PipValue)
trade_volume = risk_lots
```

#### 4. Entry Logic
```
Long Entry:
    entry_price = asian_high (NOT bar close!)
    stop_loss = asian_low
    take_profit = asian_high + (1.5 * asian_range)
    
Short Entry:
    entry_price = asian_low
    stop_loss = asian_high
    take_profit = asian_low - (1.5 * asian_range)

Conditions:
    - EMA filter (long: close > W1 EMA, short: close < W1 EMA)
    - TradeTakenToday = false (one trade per day max)
    - Not within 20 pips of W1 EMA (ambiguity zone)
```

#### 5. W1 EMA Filter
```
OnInit():
    w1_ema_period = 26
    w1_ema_cached_value = 0
    w1_ema_cached_week = 0

OnTick():
    if current_week != w1_ema_cached_week:
        w1_ema_cached_value = iMA(_Symbol, PERIOD_W1, 26, 0, MODE_EMA, PRICE_CLOSE)
        w1_ema_cached_week = current_week
```

#### 6. Exit Logic
```
If SL price reached → close at stop loss
Else if TP price reached → close at take profit
Else if bar.hour >= 17 (17:00 GMT) → market close at bar.open

Layer 3 Safety: if equity drop implies SL passed → market close immediately
```

#### 7. Safety Layers (4 Independent)

**Layer 1 — Daily Loss:**
```
start_of_day_equity = recorded at 00:00 GMT
if (start_of_day_equity - AccountEquity()) / start_of_day_equity > 0.035:
    close all
    TradingAllowed = false
    log "Daily loss limit breached"
```

**Layer 2 — Trailing Drawdown (Critical):**
```
peak_equity = max(peak_equity, AccountEquity()) [every tick]
if (peak_equity - AccountEquity()) / peak_equity > 0.04:
    close all
    TradingAllowed = false (permanent until manual reset)
    log "Trailing DD limit breached: peak={peak}, current={equity}"
```

**Layer 3 — Internal SL:**
```
While TRADE_ACTIVE:
    expected_sl_price = calculated at entry
    if direction = LONG:
        if Bid < expected_sl_price:
            close at market
            log "Internal SL triggered"
    else:
        if Ask > expected_sl_price:
            close at market
            log "Internal SL triggered"
```

**Layer 4 — Manual Kill:**
```
input bool TradingEnabled = true;
at OnTick start:
    if !TradingEnabled:
        if TRADE_ACTIVE:
            close all
        skip all trading logic
```

---

## Verification Plan

### Stage 1: Strategy Tester
- Backtest 2015–2024 GBPUSD H1
- Expected: ~180 trades, PF ≈ 1.7, WR ≈ 56%
- Verify trade count matches Python results from `gbpusd_top.json`
- Verify entry prices are range boundaries (not closes)

### Stage 2: GMT Audit
- Run on demo, verify "Asian session start" logs at 00:00 GMT
- Verify "London window open" logs at 07:00 GMT
- Verify "EOD exit" logs at 17:00 GMT
- Cross-check with broker time shown in MT5 status

### Stage 3: Safety Layer Demo Test
- Force equity drop > 3.5%: confirm Layer 1 closes all
- Force peak-to-current DD > 4%: confirm Layer 2 halts
- Set `TradingEnabled = false`: confirm no orders

### Stage 4: Live Demo (3–5 real trades)
- Run 1–4 weeks on The5ers demo
- Log actual entry spread, slippage, fills
- Compare live execution vs backtest
- Pass: fills within 1.5 pips expected price

### Stage 5: The5ers $10k Challenge
- Only after Stage 4 passes
- Monitor trailing equity peak daily via Layer 2 logs
- Do not adjust parameters during evaluation

---

## Known Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| GMT offset wrong after DST | Daily recalc + startup halt |
| EA restart loses state | GlobalVariable persistence |
| Spread spike blows daily limit | Layer 1 + Layer 3 internal SL |
| Peak equity trailing DD breach | Layer 2 watermark monitor |
| The5ers rule violation | Read rules doc before building |
| Can't debug in 18-20 trades/year | Verbose decision logging |
| Single trade too large | Max range cap (80 pips) |
| SL missed during spread spike | Layer 3 internal enforcement |

---

## Implementation Notes

- All times in GMT, no timezone conversions
- Pip value lookup: 0.01 if JPY, else 0.0001
- Test thoroughly on demo before The5ers challenge
- Lock parameters — do not optimize on live results until 60+ trades
- Log audit trail for every decision — this is how you debug a low-trade-frequency strategy

---

**Architecture Version:** 1.0  
**Council Approval:** Complete  
**Build Status:** Ready for MQL5 implementation
