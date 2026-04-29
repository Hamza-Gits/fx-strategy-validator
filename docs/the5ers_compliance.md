# The5ers Prohibited Practices — Compliance Audit

**EA:** LondonBreakout_v4.mq5
**Audit date:** 2026-04-28
**Auditor:** Strategy + EA author (source code reviewed line-by-line)

This document maps each of The5ers' 13 prohibited trading practices to specific behaviour in the EA, with code-level evidence.

---

## Compliance Matrix

| # | Prohibited Practice | EA Status | Evidence |
|---|---|---|---|
| 1 | Arbitrage trading | ✅ COMPLIANT | EA operates on a single broker, single account. No cross-exchange logic. |
| 2 | High-frequency trading (sub-second trades) | ✅ COMPLIANT | Trades open at H1 bar close after 07:00 GMT, exit at TP/SL or 17:00 GMT. Hold time hours, never seconds. |
| 3 | Bulk trading (multiple simultaneous trades) | ✅ COMPLIANT | State machine `STATE_WAITING → STATE_RANGE_SET → STATE_TRADE_ACTIVE` enforces exactly one open trade. `g_trade_taken_today = true` blocks new entries until 00:00 GMT next day. |
| 4 | Bracketing news (pending buy + sell stops near price) | ✅ COMPLIANT | Only ONE direction is taken per day. Direction is determined by W1 EMA filter + actual H1 close vs. range. No pending orders — entry is `trade.Buy()` / `trade.Sell()` market order at H1 bar close. |
| 5 | Exploiting price glitches | ✅ COMPLIANT | EA reads standard market quotes via `SymbolInfoDouble(SYMBOL_BID/ASK)` and uses standard MT5 order routing. No quote validation or arbitrage logic. |
| 6 | Coordination / copy trading | ✅ COMPLIANT | Custom EA, source code owned by trader. Magic number 778899 unique to this account. No external signal feeds. |
| 7 | One-sided bets (always one direction) | ✅ COMPLIANT | `CheckLondonBreakout()` evaluates BOTH long and short setups per day: `allow_long = last_close > w1_ema; allow_short = last_close < w1_ema`. Direction depends on market state, not hardcoded. |
| 8 | Rollover-night scalping | ✅ COMPLIANT | Asian session (00:00–07:00 GMT) is used **only** to compute the price range. NO trades placed during this window. London entry window is 07:00–10:00 GMT. |
| 9 | Third-party EA without source code | ✅ COMPLIANT | Trader owns and can modify all source code (LondonBreakout_v4.mq5 is in this repository). |
| 10 | Tick scalping | ✅ COMPLIANT | Breakout entry checked once per H1 bar via `iTime(_Symbol, PERIOD_H1, 0) != g_last_h1_bar`. Tick handler `OnTick()` runs every tick but only acts on bar boundaries. |
| 11 | Hedge arbitrage | ✅ COMPLIANT | One directional position only. No simultaneous long+short on same/correlated pairs. |
| 12 | Account sharing / reselling | ✅ COMPLIANT | Personal use, single account holder. |
| 13 | "Pass your challenge" services | ✅ COMPLIANT | Trader runs the EA on their own account, not as a paid service for others. |

---

## Code-level evidence (selected)

### Rule 3 — Single position enforcement (LondonBreakout_v4.mq5 line ~720)
```mql5
if(g_state == STATE_RANGE_SET && !g_trade_taken_today && hour < InpLondonEndHour)
{
   datetime current_bar = iTime(_Symbol, PERIOD_H1, 0);
   if(current_bar != g_last_h1_bar)
   {
      g_last_h1_bar = current_bar;
      EnterTrade();
   }
}
```
After `EnterTrade()` succeeds, `g_trade_taken_today = true` blocks all further entries until daily reset at 00:00 GMT.

### Rule 7 — Bidirectional trading (line ~600)
```mql5
allow_long  = (last_close > g_w1_ema_value);
allow_short = (last_close < g_w1_ema_value);

if(allow_long && last_close > g_asian_high)
   { /* enter LONG */ }
if(allow_short && last_close < g_asian_low)
   { /* enter SHORT */ }
```
The EA takes whatever direction the market provides. In a strong uptrend it will trade mostly long; in a downtrend, mostly short. This is **market-condition-dependent**, not a fixed bias.

### Rule 8 — No Asian-session trading (line ~700)
```mql5
if(g_state == STATE_WAITING && !g_trade_taken_today &&
   hour >= InpAsianEndHour && hour < InpLondonEndHour)
{
   if(!g_range_computed_today)
   {
      ComputeAsianRange(day_start);
   }
}
```
The state never advances past `STATE_WAITING` until `hour >= InpAsianEndHour` (07:00 GMT). No trade can be placed during the Asian session.

### Rule 10 — Bar-close entry (line ~620)
```mql5
if(CopyRates(_Symbol, PERIOD_H1, 1, 1, bars) <= 0) return false;
double last_close = bars[0].close;
```
`PERIOD_H1, 1, 1` reads bar index 1 — the **last completed** H1 bar. Entry is never based on the live forming bar.

---

## The5ers-specific EA configuration

For The5ers $10k evaluation, set the following inputs:

```
InpHardHalt           = true    // Permanent halt if 4% DD breached
InpTrailingDDPct      = 4.0     // The5ers max total DD rule
InpDailyLossLimitPct  = 3.0     // Conservative (resets each day)
InpUseProgressiveRisk = false   // Flat risk during evaluation
InpRiskPercent        = 0.5     // $50 risk per trade on $10k
```

With `InpHardHalt = true`, if equity ever drops 4% from peak the EA closes any open position and stops trading permanently. This matches The5ers' rule that breaching max DD ends the evaluation.

For personal accounts, leave `InpHardHalt = false` — the EA uses **soft recovery** instead (30-day pause at 0.5%, then resume with reset peak watermark). This is the v4 fix for the v3.2 flatline bug.

---

## Disclosure

This audit reflects the EA logic as of v4.0 (2026-04-28). Any change to the source code may invalidate compliance — re-audit after modifications.

If The5ers updates their prohibited practices list, the trader is responsible for re-checking compliance against the new rules.
