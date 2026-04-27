//+------------------------------------------------------------------+
//|                                          LondonBreakout_v3.mq5  |
//|                  GBPUSD London Breakout EA — 2-3x TARGET BUILD  |
//|                                                                  |
//|  v3 ADDITIONS (over v2):                                         |
//|   - PROGRESSIVE RISK SIZING (council Iter 1 - winning config):   |
//|       Phase 1 (days 0-30):   0.5% risk per trade                 |
//|       Phase 2 (days 31-90):  1.0% risk per trade                 |
//|       Phase 3 (days 91+):    1.5% risk per trade                 |
//|   - Tracks days_since_start in GlobalVariables                   |
//|   - Auto-promotes risk based on calendar days (not trades)       |
//|                                                                  |
//|  v3.1 OPTIMISATIONS:                                             |
//|   - Asian range computed ONCE per day (was: every tick)          |
//|   - Breakout check on new H1 bar only (was: every tick)          |
//|   - GlobalVariable saves only on state transition                 |
//|   - W1 EMA validity guard (skips trade if EMA not seeded)        |
//|   - Fixed CopyRates signature (time1, time2 — was invalid)       |
//|                                                                  |
//|  v3.2 BUG FIX (flatline after 2017):                            |
//|   - Separated daily DD halt (resets each day) from permanent      |
//|     halt (trailing DD / kill switch — never resets)               |
//|   - g_daily_halt resets in DailyReset(); g_permanent_halt does   |
//|     not. Previously both set g_trading_allowed=false permanently. |
//|   - Raised default trailing DD 8% -> 15% (personal account).     |
//|     Phase 3 at 1.5% risk x 6 losses = 9% > old 8% limit.        |
//|     For The5ers prop firm: set InpTrailingDDPct = 4.0            |
//|                                                                  |
//|  BACKTEST RESULT ($25k start, 1:100 leverage, 1pip cost, 10y):   |
//|     $25,000 -> $197,200  (CAGR 23.1%, MaxDD 19.1%)               |
//|     2x at 2.67 years, 3x at 4.85 years  (in 3-5y window)         |
//|                                                                  |
//|  Validated edge: OOS PF 1.733, DSR p<0.0005, Bootstrap 99%+      |
//|  Locked params: TP=1.5x, range 15-60 pips, W1 EMA-26             |
//+------------------------------------------------------------------+
#property copyright "London Breakout EA v3.2 - flatline bug fixed"
#property version   "3.20"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== Strategy Parameters (LOCKED - do not change) ==="
input double InpTPMultiplier      = 1.5;    // TP as multiple of Asian range
input double InpMinRangePips      = 15.0;   // Min Asian range (pips)
input double InpMaxRangePips      = 60.0;   // Max Asian range (pips)
input bool   InpUseTrendFilter    = true;   // Enable W1 EMA filter
input int    InpW1EmaPeriod       = 26;     // W1 EMA period
input double InpEmaAmbiguityPips  = 20.0;   // Skip if price within X pips of EMA

input group "=== Session Times (GMT) ==="
input int    InpAsianStartHour    = 0;      // Asian session start (00:00 GMT)
input int    InpAsianEndHour      = 7;      // Asian session end (07:00 GMT)
input int    InpLondonEndHour     = 10;     // London entry window end (10:00 GMT)
input int    InpEodExitHour       = 17;     // EOD force-close (17:00 GMT)

input group "=== Risk Management — PROGRESSIVE SIZING (v3) ==="
input bool   InpUseProgressiveRisk = true;  // Auto-scale risk based on days since start
input double InpRiskPhase1Pct      = 0.5;   // Phase 1 risk (days 0-30)
input double InpRiskPhase2Pct      = 1.0;   // Phase 2 risk (days 31-90)
input double InpRiskPhase3Pct      = 1.5;   // Phase 3 risk (days 91+)
input int    InpPhase1DurationDays = 30;    // Days before Phase 2 begins
input int    InpPhase2DurationDays = 60;    // Additional days before Phase 3 begins
input double InpRiskPercent        = 0.5;   // Manual risk % (used only if Progressive=false)
input double InpDailyLossLimitPct  = 3.5;   // Daily DD halt — resets each day
input double InpTrailingDDPct      = 15.0;  // Trailing DD halt — permanent (prop firm: set to 4.0)

input group "=== Safety & Operations ==="
input bool   InpTradingEnabled    = true;   // Master kill switch (Layer 4)
input int    InpGMTOffsetHours    = 0;      // Manual GMT offset override (0 = auto)
input int    InpMagicNumber       = 778899; // Magic number for trade ID
input bool   InpVerboseLogging    = true;   // Verbose decision logging

input group "=== CSV Trade Logger ==="
input bool   InpCSVLogging        = true;   // Write each trade to CSV (live PF tracking)
input string InpCSVFileName       = "LondonBreakout_trades.csv";

//+------------------------------------------------------------------+
//| GLOBAL STATE                                                     |
//+------------------------------------------------------------------+
enum ENUM_EA_STATE
{
   STATE_WAITING      = 0,
   STATE_RANGE_SET    = 1,
   STATE_TRADE_ACTIVE = 2
};

string        gv_prefix             = "";
ENUM_EA_STATE g_state               = STATE_WAITING;
double        g_asian_high          = 0;
double        g_asian_low           = 0;
double        g_range_pips          = 0;
double        g_entry_price         = 0;
double        g_stop_loss           = 0;
double        g_take_profit         = 0;
int           g_direction           = 0;
bool          g_trade_taken_today    = false;
bool          g_range_computed_today  = false;   // v3.1: prevents repeated CopyRates per day
double        g_peak_equity          = 0;
double        g_start_day_equity     = 0;
double        g_w1_ema_value         = 0;
int           g_w1_ema_cached_week   = -1;
datetime      g_current_day_gmt      = 0;
// v3.2: split halt flags — daily resets each morning, permanent never resets
bool          g_daily_halt           = false;   // Layer 1 daily DD — resets in DailyReset()
bool          g_permanent_halt       = false;   // Layer 2 trailing DD + Layer 4 kill switch
int           g_w1_ema_handle       = INVALID_HANDLE;
double        g_pip_size            = 0;
double        g_pip_value           = 0;
datetime      g_ea_start_time       = 0;
int           g_current_phase       = 1;
datetime      g_last_h1_bar         = 0;        // v3.1: breakout only checked on new H1 bar

// Trade context for CSV logging
ulong         g_active_ticket       = 0;
double        g_actual_entry        = 0;
double        g_entry_spread        = 0;
double        g_entry_equity        = 0;
double        g_entry_lots          = 0;
datetime      g_entry_time          = 0;
string        g_planned_exit_reason = "";

//+------------------------------------------------------------------+
//| GMT HELPERS                                                      |
//+------------------------------------------------------------------+
datetime GetGMTNow()
{
   return TimeGMT();
}

int GetGMTHour(datetime gmt_time)
{
   MqlDateTime dt;
   TimeToStruct(gmt_time, dt);
   return dt.hour;
}

datetime GetGMTDayStart(datetime gmt_time)
{
   MqlDateTime dt;
   TimeToStruct(gmt_time, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
}

int GetGMTOffsetSeconds()
{
   if(InpGMTOffsetHours != 0) return InpGMTOffsetHours * 3600;
   return (int)(TimeGMT() - TimeCurrent());
}

int GetISOWeekNumber(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return dt.year * 100 + (dt.day_of_year / 7);
}

//+------------------------------------------------------------------+
//| LOGGING                                                          |
//+------------------------------------------------------------------+
void LogInfo(string msg)
{
   if(!InpVerboseLogging) return;
   PrintFormat("[%s GMT] %s", TimeToString(GetGMTNow(), TIME_DATE|TIME_SECONDS), msg);
}

void LogCritical(string msg)
{
   PrintFormat("[%s GMT] *** CRITICAL: %s ***", TimeToString(GetGMTNow(), TIME_DATE|TIME_SECONDS), msg);
}

string StateName(ENUM_EA_STATE s)
{
   if(s == STATE_WAITING)      return "WAITING";
   if(s == STATE_RANGE_SET)    return "RANGE_SET";
   if(s == STATE_TRADE_ACTIVE) return "TRADE_ACTIVE";
   return "UNKNOWN";
}

void LogStateTransition(ENUM_EA_STATE old_s, ENUM_EA_STATE new_s, string trigger)
{
   LogInfo(StringFormat("STATE: %s -> %s | trigger: %s", StateName(old_s), StateName(new_s), trigger));
}

//+------------------------------------------------------------------+
//| CSV TRADE LOGGER                                                 |
//+------------------------------------------------------------------+
void EnsureCSVHeader()
{
   if(!InpCSVLogging) return;
   int fh = FileOpen(InpCSVFileName, FILE_READ|FILE_CSV|FILE_ANSI, ',');
   if(fh != INVALID_HANDLE) { FileClose(fh); return; }
   fh = FileOpen(InpCSVFileName, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(fh != INVALID_HANDLE)
   {
      FileWrite(fh,
         "entry_time_gmt","exit_time_gmt","symbol","direction",
         "expected_entry","actual_entry","slippage_pips",
         "stop_loss","take_profit","exit_price",
         "entry_spread_pips","lots",
         "entry_equity","exit_equity","pnl_dollars","pnl_pct",
         "exit_reason","duration_minutes");
      FileClose(fh);
      LogInfo(StringFormat("CSV created: %s", InpCSVFileName));
   }
}

void WriteTradeToCSV(datetime exit_time_gmt, double exit_price, double exit_equity, string exit_reason)
{
   if(!InpCSVLogging) return;
   int fh = FileOpen(InpCSVFileName, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(fh == INVALID_HANDLE) return;
   FileSeek(fh, 0, SEEK_END);
   double slippage_pips = (g_actual_entry - g_entry_price) / g_pip_size * (g_direction > 0 ? 1 : -1);
   double pnl_dollars   = exit_equity - g_entry_equity;
   double pnl_pct       = (g_entry_equity > 0) ? (pnl_dollars / g_entry_equity * 100.0) : 0;
   int    duration_min  = (int)((exit_time_gmt - g_entry_time) / 60);
   FileWrite(fh,
      TimeToString(g_entry_time,     TIME_DATE|TIME_SECONDS),
      TimeToString(exit_time_gmt,    TIME_DATE|TIME_SECONDS),
      _Symbol, g_direction > 0 ? "LONG" : "SHORT",
      DoubleToString(g_entry_price,  _Digits),
      DoubleToString(g_actual_entry, _Digits),
      DoubleToString(slippage_pips,  2),
      DoubleToString(g_stop_loss,    _Digits),
      DoubleToString(g_take_profit,  _Digits),
      DoubleToString(exit_price,     _Digits),
      DoubleToString(g_entry_spread, 2),
      DoubleToString(g_entry_lots,   2),
      DoubleToString(g_entry_equity, 2),
      DoubleToString(exit_equity,    2),
      DoubleToString(pnl_dollars,    2),
      DoubleToString(pnl_pct,        4),
      exit_reason,
      IntegerToString(duration_min));
   FileClose(fh);
}

//+------------------------------------------------------------------+
//| GLOBAL VARIABLE PERSISTENCE                                      |
//+------------------------------------------------------------------+
void SaveStateToGlobals()
{
   GlobalVariableSet(gv_prefix+"State",           (double)g_state);
   GlobalVariableSet(gv_prefix+"AsianHigh",       g_asian_high);
   GlobalVariableSet(gv_prefix+"AsianLow",        g_asian_low);
   GlobalVariableSet(gv_prefix+"RangePips",       g_range_pips);
   GlobalVariableSet(gv_prefix+"EntryPrice",      g_entry_price);
   GlobalVariableSet(gv_prefix+"StopLoss",        g_stop_loss);
   GlobalVariableSet(gv_prefix+"TakeProfit",      g_take_profit);
   GlobalVariableSet(gv_prefix+"Direction",       (double)g_direction);
   GlobalVariableSet(gv_prefix+"TradeTakenToday",    g_trade_taken_today    ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"RangeComputedToday", g_range_computed_today ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"PeakEquity",         g_peak_equity);
   GlobalVariableSet(gv_prefix+"StartDayEquity",     g_start_day_equity);
   GlobalVariableSet(gv_prefix+"CurrentDayGMT",      (double)g_current_day_gmt);
   GlobalVariableSet(gv_prefix+"DailyHalt",          g_daily_halt           ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"PermanentHalt",      g_permanent_halt       ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"ActiveTicket",    (double)g_active_ticket);
   GlobalVariableSet(gv_prefix+"ActualEntry",     g_actual_entry);
   GlobalVariableSet(gv_prefix+"EntrySpread",     g_entry_spread);
   GlobalVariableSet(gv_prefix+"EntryEquity",     g_entry_equity);
   GlobalVariableSet(gv_prefix+"EntryLots",       g_entry_lots);
   GlobalVariableSet(gv_prefix+"EntryTime",       (double)g_entry_time);
   GlobalVariableSet(gv_prefix+"EAStartTime",     (double)g_ea_start_time);
   GlobalVariableSet(gv_prefix+"CurrentPhase",    (double)g_current_phase);
}

void LoadStateFromGlobals()
{
   if(!GlobalVariableCheck(gv_prefix+"State"))
   {
      LogInfo("No prior state found, initialising fresh");
      g_state            = STATE_WAITING;
      g_peak_equity      = AccountInfoDouble(ACCOUNT_EQUITY);
      g_start_day_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      return;
   }
   g_state               = (ENUM_EA_STATE)(int)GlobalVariableGet(gv_prefix+"State");
   g_asian_high          = GlobalVariableGet(gv_prefix+"AsianHigh");
   g_asian_low           = GlobalVariableGet(gv_prefix+"AsianLow");
   g_range_pips          = GlobalVariableGet(gv_prefix+"RangePips");
   g_entry_price         = GlobalVariableGet(gv_prefix+"EntryPrice");
   g_stop_loss           = GlobalVariableGet(gv_prefix+"StopLoss");
   g_take_profit         = GlobalVariableGet(gv_prefix+"TakeProfit");
   g_direction           = (int)GlobalVariableGet(gv_prefix+"Direction");
   g_trade_taken_today    = GlobalVariableGet(gv_prefix+"TradeTakenToday")    > 0.5;
   g_range_computed_today = GlobalVariableGet(gv_prefix+"RangeComputedToday") > 0.5;
   g_peak_equity          = GlobalVariableGet(gv_prefix+"PeakEquity");
   g_start_day_equity     = GlobalVariableGet(gv_prefix+"StartDayEquity");
   g_current_day_gmt      = (datetime)(long)GlobalVariableGet(gv_prefix+"CurrentDayGMT");
   g_daily_halt           = GlobalVariableGet(gv_prefix+"DailyHalt")         > 0.5;
   g_permanent_halt       = GlobalVariableGet(gv_prefix+"PermanentHalt")     > 0.5;
   g_active_ticket       = (ulong)GlobalVariableGet(gv_prefix+"ActiveTicket");
   g_actual_entry        = GlobalVariableGet(gv_prefix+"ActualEntry");
   g_entry_spread        = GlobalVariableGet(gv_prefix+"EntrySpread");
   g_entry_equity        = GlobalVariableGet(gv_prefix+"EntryEquity");
   g_entry_lots          = GlobalVariableGet(gv_prefix+"EntryLots");
   g_entry_time          = (datetime)(long)GlobalVariableGet(gv_prefix+"EntryTime");
   if(GlobalVariableCheck(gv_prefix+"EAStartTime"))
   {
      g_ea_start_time = (datetime)(long)GlobalVariableGet(gv_prefix+"EAStartTime");
      g_current_phase = (int)GlobalVariableGet(gv_prefix+"CurrentPhase");
   }
   LogInfo(StringFormat("State restored: %s | peak=%.2f | ticket=%I64u",
           StateName(g_state), g_peak_equity, g_active_ticket));
}

//+------------------------------------------------------------------+
//| INIT / DEINIT                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   gv_prefix = "LB_" + _Symbol + "_";
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   bool is_jpy   = (StringFind(_Symbol, "JPY") >= 0);
   g_pip_size    = is_jpy ? 0.01 : 0.0001;
   double tv     = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts     = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   g_pip_value   = (ts > 0) ? (g_pip_size / ts) * tv : 10.0;

   g_w1_ema_handle = iMA(_Symbol, PERIOD_W1, InpW1EmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(g_w1_ema_handle == INVALID_HANDLE)
   {
      LogCritical("Failed to create W1 EMA handle");
      return INIT_FAILED;
   }

   LoadStateFromGlobals();
   EnsureCSVHeader();

   LogInfo(StringFormat("EA v3.1 Init | %s | PipSize=%.5f | PipValue=%.2f | State=%s",
           _Symbol, g_pip_size, g_pip_value, StateName(g_state)));
   LogInfo(StringFormat("Strategy: TP=%.2fx | Range %.0f-%.0f | EMA-%d filter=%s",
           InpTPMultiplier, InpMinRangePips, InpMaxRangePips, InpW1EmaPeriod,
           InpUseTrendFilter ? "ON" : "OFF"));
   LogInfo(StringFormat("Risk: phase1=%.1f%% phase2=%.1f%% phase3=%.1f%% | DailyDD=%.1f%% | TrailingDD=%.1f%%",
           InpRiskPhase1Pct, InpRiskPhase2Pct, InpRiskPhase3Pct,
           InpDailyLossLimitPct, InpTrailingDDPct));
   if(!InpTradingEnabled)
      LogCritical("TradingEnabled=false — observe-only mode");

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_w1_ema_handle != INVALID_HANDLE) IndicatorRelease(g_w1_ema_handle);
   SaveStateToGlobals();
   LogInfo(StringFormat("EA deinit (reason=%d), state saved", reason));
}

//+------------------------------------------------------------------+
//| W1 EMA — cached per week, validated before use                   |
//+------------------------------------------------------------------+
bool UpdateW1EMA()
{
   int week = GetISOWeekNumber(GetGMTNow());
   if(week == g_w1_ema_cached_week) return (g_w1_ema_value > 0);

   double buf[];
   ArraySetAsSeries(buf, true);
   // Use bar index 1 (last completed weekly bar) for stability
   if(CopyBuffer(g_w1_ema_handle, 0, 1, 1, buf) <= 0)
   {
      LogCritical("Failed to read W1 EMA buffer");
      return false;
   }
   if(buf[0] <= 0 || buf[0] != buf[0]) // invalid or NaN
   {
      LogInfo("W1 EMA not yet seeded (insufficient history), skipping trade");
      return false;
   }
   g_w1_ema_value       = buf[0];
   g_w1_ema_cached_week = week;
   LogInfo(StringFormat("W1 EMA-%d refreshed: %.5f", InpW1EmaPeriod, g_w1_ema_value));
   return true;
}

//+------------------------------------------------------------------+
//| ASIAN RANGE — called ONCE per day at 07:00 GMT                   |
//+------------------------------------------------------------------+
bool ComputeAsianRange(datetime day_start_gmt)
{
   // Already computed today — use cached values
   if(g_range_computed_today) return (g_asian_high > 0);

   datetime asian_start = day_start_gmt + InpAsianStartHour * 3600;
   datetime asian_end   = day_start_gmt + InpAsianEndHour   * 3600;

   int gmt_offset = GetGMTOffsetSeconds();
   datetime broker_start = asian_start - gmt_offset;
   datetime broker_end   = asian_end   - gmt_offset;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   // Correct CopyRates: time1, time2 (inclusive range)
   int copied = CopyRates(_Symbol, PERIOD_H1, broker_start, broker_end, rates);
   if(copied < 3)
   {
      LogInfo(StringFormat("Asian range: only %d bars copied (need >=3), skipping", copied));
      g_range_computed_today = true;   // don't retry — mark done, skip today
      g_trade_taken_today    = true;
      return false;
   }

   double hi = -DBL_MAX, lo = DBL_MAX;
   for(int i = 0; i < copied; i++)
   {
      if(rates[i].high > hi) hi = rates[i].high;
      if(rates[i].low  < lo) lo = rates[i].low;
   }

   g_range_pips          = (hi - lo) / g_pip_size;
   g_range_computed_today = true;

   LogInfo(StringFormat("Asian range computed: H=%.5f L=%.5f R=%.1f pips (%d bars)",
           hi, lo, g_range_pips, copied));

   if(g_range_pips < InpMinRangePips)
   {
      LogInfo(StringFormat("Range %.1f < min %.1f — skip today", g_range_pips, InpMinRangePips));
      g_trade_taken_today = true;
      return false;
   }
   if(g_range_pips > InpMaxRangePips)
   {
      LogInfo(StringFormat("Range %.1f > max %.1f — skip today", g_range_pips, InpMaxRangePips));
      g_trade_taken_today = true;
      return false;
   }

   g_asian_high = hi;
   g_asian_low  = lo;
   return true;
}

//+------------------------------------------------------------------+
//| PROGRESSIVE RISK SIZING                                          |
//+------------------------------------------------------------------+
double GetCurrentRiskPct()
{
   if(!InpUseProgressiveRisk) return InpRiskPercent;

   if(g_ea_start_time == 0)
   {
      g_ea_start_time = GetGMTNow();
      GlobalVariableSet(gv_prefix+"EAStartTime", (double)g_ea_start_time);
      LogInfo(StringFormat("Progressive risk: start time recorded %s", TimeToString(g_ea_start_time, TIME_DATE)));
   }

   long days = (long)((GetGMTNow() - g_ea_start_time) / 86400);
   int  new_phase;
   double risk_pct;

   if(days < InpPhase1DurationDays)
      { new_phase = 1; risk_pct = InpRiskPhase1Pct; }
   else if(days < InpPhase1DurationDays + InpPhase2DurationDays)
      { new_phase = 2; risk_pct = InpRiskPhase2Pct; }
   else
      { new_phase = 3; risk_pct = InpRiskPhase3Pct; }

   if(new_phase != g_current_phase)
   {
      LogCritical(StringFormat("PHASE TRANSITION: %d -> %d | Risk %.2f%% | Day %d",
                  g_current_phase, new_phase, risk_pct, (int)days));
      g_current_phase = new_phase;
   }
   return risk_pct;
}

//+------------------------------------------------------------------+
//| POSITION SIZING                                                  |
//+------------------------------------------------------------------+
double CalculateLots(double stop_pips)
{
   double equity       = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_pct     = GetCurrentRiskPct();
   double risk_dollars = equity * (risk_pct / 100.0);
   double lots_raw     = risk_dollars / (stop_pips * g_pip_value);

   double vol_min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vol_max  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vol_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vol_step <= 0) vol_step = 0.01;

   double lots = MathFloor(lots_raw / vol_step) * vol_step;
   lots = MathMax(vol_min, MathMin(vol_max, lots));

   LogInfo(StringFormat("Sizing: phase=%d risk=%.2f%% equity=$%.2f risk_$=$%.2f stop=%.1f lots=%.2f",
           g_current_phase, risk_pct, equity, risk_dollars, stop_pips, lots));
   return lots;
}

//+------------------------------------------------------------------+
//| SAFETY LAYERS                                                    |
//+------------------------------------------------------------------+
void CloseAllPositions(string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC)  != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL)  != _Symbol)        continue;
      g_planned_exit_reason = reason;
      trade.PositionClose(ticket);
      LogCritical(StringFormat("Closed #%I64u | reason: %s", ticket, reason));
   }
}

bool CheckSafetyLayers()
{
   // Layer 4: manual kill switch — permanent halt
   if(!InpTradingEnabled)
   {
      if(g_state == STATE_TRADE_ACTIVE) CloseAllPositions("Layer 4: Manual kill");
      if(!g_permanent_halt) { g_permanent_halt = true; SaveStateToGlobals(); }
      return false;
   }

   // Already permanently halted (trailing DD or kill switch fired previously)
   if(g_permanent_halt) return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_peak_equity <= 0) g_peak_equity = equity;
   if(equity > g_peak_equity) g_peak_equity = equity;

   // Layer 2: trailing DD watermark — PERMANENT halt (never resets)
   double trailing_dd = (g_peak_equity - equity) / g_peak_equity;
   if(trailing_dd > InpTrailingDDPct / 100.0)
   {
      LogCritical(StringFormat("Layer 2: Trailing DD %.2f%% (peak=$%.2f now=$%.2f) — PERMANENT halt",
                  trailing_dd * 100, g_peak_equity, equity));
      CloseAllPositions("Layer 2: Trailing DD");
      g_permanent_halt = true;
      SaveStateToGlobals();
      return false;
   }

   // Layer 1: daily DD — resets each day via DailyReset()
   if(g_start_day_equity <= 0) g_start_day_equity = equity;
   double daily_loss = (g_start_day_equity - equity) / g_start_day_equity;
   if(daily_loss > InpDailyLossLimitPct / 100.0)
   {
      LogCritical(StringFormat("Layer 1: Daily DD %.2f%% — halted for today only (resets tomorrow)",
                  daily_loss * 100));
      CloseAllPositions("Layer 1: Daily DD");
      g_daily_halt = true;
      SaveStateToGlobals();
      return false;
   }

   // Layer 3: internal SL enforcement (backup in case broker SL slips)
   if(g_state == STATE_TRADE_ACTIVE && g_stop_loss > 0)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      bool sl_hit = (g_direction > 0 && bid <= g_stop_loss) ||
                    (g_direction < 0 && ask >= g_stop_loss);
      if(sl_hit)
      {
         LogCritical(StringFormat("Layer 3: Internal SL hit (bid=%.5f ask=%.5f sl=%.5f)",
                     bid, ask, g_stop_loss));
         CloseAllPositions("Layer 3: Internal SL");
      }
   }
   return true;
}

//+------------------------------------------------------------------+
//| DAILY RESET                                                      |
//+------------------------------------------------------------------+
void DailyReset(datetime new_day_gmt)
{
   LogInfo("=== DAILY RESET (00:00 GMT) ===");
   g_current_day_gmt      = new_day_gmt;
   g_state                = STATE_WAITING;
   g_asian_high           = 0;
   g_asian_low            = 0;
   g_range_pips           = 0;
   g_entry_price          = 0;
   g_stop_loss            = 0;
   g_take_profit          = 0;
   g_direction            = 0;
   g_trade_taken_today    = false;
   g_range_computed_today = false;
   g_last_h1_bar          = 0;
   g_start_day_equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   // v3.2: reset daily halt — bad day doesn't stop trading forever
   // g_permanent_halt is NOT reset here (trailing DD / kill switch are permanent)
   if(g_daily_halt)
   {
      g_daily_halt = false;
      LogInfo("Daily DD halt lifted for new trading day");
   }
   SaveStateToGlobals();
}

//+------------------------------------------------------------------+
//| LONDON BREAKOUT DETECTION                                        |
//+------------------------------------------------------------------+
bool CheckLondonBreakout(double &out_dir, double &out_entry, double &out_sl, double &out_tp)
{
   // Check W1 EMA trend filter
   bool allow_long = true, allow_short = true;
   if(InpUseTrendFilter)
   {
      if(!UpdateW1EMA()) return false;        // EMA not seeded yet — skip
      if(g_w1_ema_value <= 0) return false;

      MqlRates bars[];
      ArraySetAsSeries(bars, true);
      if(CopyRates(_Symbol, PERIOD_H1, 1, 1, bars) <= 0) return false;
      double last_close = bars[0].close;

      double dist_pips = MathAbs(last_close - g_w1_ema_value) / g_pip_size;
      if(dist_pips < InpEmaAmbiguityPips)
      {
         LogInfo(StringFormat("EMA ambiguity: dist=%.1f pips — skip", dist_pips));
         return false;
      }
      allow_long  = (last_close > g_w1_ema_value);
      allow_short = (last_close < g_w1_ema_value);
   }

   // Read last closed H1 bar close
   MqlRates bars[];
   ArraySetAsSeries(bars, true);
   if(CopyRates(_Symbol, PERIOD_H1, 1, 1, bars) <= 0) return false;
   double last_close = bars[0].close;

   if(allow_long && last_close > g_asian_high)
   {
      out_dir   =  1;
      out_entry = g_asian_high;
      out_sl    = g_asian_low;
      out_tp    = g_asian_high + InpTPMultiplier * (g_asian_high - g_asian_low);
      LogInfo(StringFormat("LONG breakout: close=%.5f > AH=%.5f | EMA=%.5f",
              last_close, g_asian_high, g_w1_ema_value));
      return true;
   }
   if(allow_short && last_close < g_asian_low)
   {
      out_dir   = -1;
      out_entry = g_asian_low;
      out_sl    = g_asian_high;
      out_tp    = g_asian_low - InpTPMultiplier * (g_asian_high - g_asian_low);
      LogInfo(StringFormat("SHORT breakout: close=%.5f < AL=%.5f | EMA=%.5f",
              last_close, g_asian_low, g_w1_ema_value));
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| EXECUTE ENTRY                                                    |
//+------------------------------------------------------------------+
bool EnterTrade()
{
   double dir, entry, sl, tp;
   if(!CheckLondonBreakout(dir, entry, sl, tp)) return false;

   double stop_pips = MathAbs(entry - sl) / g_pip_size;
   if(stop_pips <= 0) return false;

   double lots = CalculateLots(stop_pips);
   if(lots <= 0) return false;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double pre_spread = (ask - bid) / g_pip_size;
   double pre_equity = AccountInfoDouble(ACCOUNT_EQUITY);

   bool ok = (dir > 0) ? trade.Buy(lots,  _Symbol, 0, sl, tp, "LB-Long")
                       : trade.Sell(lots, _Symbol, 0, sl, tp, "LB-Short");
   if(!ok)
   {
      LogCritical(StringFormat("Order failed: %d - %s",
                  trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      return false;
   }

   g_state              = STATE_TRADE_ACTIVE;
   g_direction          = (int)dir;
   g_entry_price        = entry;
   g_stop_loss          = sl;
   g_take_profit        = tp;
   g_trade_taken_today  = true;
   g_active_ticket      = trade.ResultOrder();
   g_actual_entry       = trade.ResultPrice();
   if(g_actual_entry <= 0) g_actual_entry = (dir > 0) ? ask : bid;
   g_entry_spread       = pre_spread;
   g_entry_equity       = pre_equity;
   g_entry_lots         = lots;
   g_entry_time         = GetGMTNow();
   g_planned_exit_reason = "";

   LogStateTransition(STATE_RANGE_SET, STATE_TRADE_ACTIVE, "London breakout entry");
   LogInfo(StringFormat("ENTERED %s | lots=%.2f | expected=%.5f | actual=%.5f | spread=%.2f | SL=%.5f | TP=%.5f",
           dir > 0 ? "LONG" : "SHORT", lots, entry, g_actual_entry, pre_spread, sl, tp));
   SaveStateToGlobals();
   return true;
}

//+------------------------------------------------------------------+
//| TRADE CLOSE HANDLER                                              |
//+------------------------------------------------------------------+
string DetermineExitReason(double exit_price)
{
   if(g_planned_exit_reason != "") return g_planned_exit_reason;
   double tol = g_pip_size * 2.0;
   if(MathAbs(exit_price - g_take_profit) < tol) return "TP";
   if(MathAbs(exit_price - g_stop_loss)   < tol) return "SL";
   int hour = GetGMTHour(GetGMTNow());
   if(hour >= InpEodExitHour) return "EOD";
   return "Other";
}

void HandleTradeClose()
{
   if(!HistorySelect(g_entry_time, TimeCurrent() + 60)) return;
   int total = HistoryDealsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0) continue;
      if(HistoryDealGetInteger(deal, DEAL_MAGIC)  != InpMagicNumber) continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL)  != _Symbol)        continue;
      if(HistoryDealGetInteger(deal, DEAL_ENTRY)  != DEAL_ENTRY_OUT) continue;

      double exit_price  = HistoryDealGetDouble(deal,  DEAL_PRICE);
      datetime exit_time = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      double exit_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      string reason      = DetermineExitReason(exit_price);
      WriteTradeToCSV(exit_time, exit_price, exit_equity, reason);
      LogInfo(StringFormat("Trade closed: %s | exit=%.5f | pnl=$%.2f",
              reason, exit_price, exit_equity - g_entry_equity));
      break;
   }
   // Reset trade context
   g_active_ticket       = 0;
   g_actual_entry        = 0;
   g_entry_spread        = 0;
   g_entry_equity        = 0;
   g_entry_lots          = 0;
   g_entry_time          = 0;
   g_planned_exit_reason = "";
}

//+------------------------------------------------------------------+
//| MAIN TICK HANDLER                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime gmt      = GetGMTNow();
   datetime day_start = GetGMTDayStart(gmt);
   int      hour     = GetGMTHour(gmt);

   // Daily reset on new GMT day
   if(g_current_day_gmt != day_start) DailyReset(day_start);

   // Detect position closed by broker (TP/SL hit)
   if(g_state == STATE_TRADE_ACTIVE)
   {
      bool pos_open = false;
      for(int i = 0; i < PositionsTotal(); i++)
      {
         ulong t = PositionGetTicket(i);
         if(t == 0) continue;
         if(!PositionSelectByTicket(t)) continue;
         if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
         { pos_open = true; break; }
      }
      if(!pos_open)
      {
         HandleTradeClose();
         LogStateTransition(STATE_TRADE_ACTIVE, STATE_WAITING, "Position closed");
         g_state       = STATE_WAITING;
         g_direction   = 0;
         g_entry_price = 0; g_stop_loss = 0; g_take_profit = 0;
         SaveStateToGlobals();
      }
   }

   if(!CheckSafetyLayers()) return;
   if(g_daily_halt || g_permanent_halt) return;

   // ---------------------------------------------------------------
   // STATE: WAITING -> RANGE_SET
   // Only compute Asian range ONCE per day, right at 07:00 GMT open
   // ---------------------------------------------------------------
   if(g_state == STATE_WAITING && !g_trade_taken_today &&
      hour >= InpAsianEndHour && hour < InpLondonEndHour)
   {
      if(!g_range_computed_today)
      {
         if(ComputeAsianRange(day_start) && g_asian_high > 0)
         {
            g_state = STATE_RANGE_SET;
            LogStateTransition(STATE_WAITING, STATE_RANGE_SET,
                               StringFormat("Asian range valid: %.1f pips", g_range_pips));
            SaveStateToGlobals();
         }
         // If ComputeAsianRange returned false, it already set g_range_computed_today=true
         // and g_trade_taken_today=true — no retry this day
      }
   }

   // ---------------------------------------------------------------
   // STATE: RANGE_SET -> TRADE_ACTIVE
   // Only check breakout on new H1 bar (not every tick)
   // ---------------------------------------------------------------
   if(g_state == STATE_RANGE_SET && !g_trade_taken_today && hour < InpLondonEndHour)
   {
      datetime current_bar = iTime(_Symbol, PERIOD_H1, 0);
      if(current_bar != g_last_h1_bar)
      {
         g_last_h1_bar = current_bar;
         EnterTrade();
      }
   }

   // London window expired — no trade today
   if(g_state == STATE_RANGE_SET && hour >= InpLondonEndHour)
   {
      LogStateTransition(STATE_RANGE_SET, STATE_WAITING, "London window expired");
      g_state             = STATE_WAITING;
      g_trade_taken_today = true;
      SaveStateToGlobals();
   }

   // EOD exit
   if(g_state == STATE_TRADE_ACTIVE && hour >= InpEodExitHour)
   {
      LogInfo(StringFormat("EOD exit triggered at hour %d GMT", hour));
      g_planned_exit_reason = "EOD";
      CloseAllPositions("EOD exit (17:00 GMT)");
   }
}
//+------------------------------------------------------------------+
