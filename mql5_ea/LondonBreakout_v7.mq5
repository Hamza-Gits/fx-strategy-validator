//+------------------------------------------------------------------+
//|                                          LondonBreakout_v7.mq5  |
//|     GBPUSD London Breakout EA — BAR-CLOSE MARKET ORDERS (v7)     |
//|                                                                  |
//|  v7 CRITICAL REWRITE (over v6 / Report 20 disaster):             |
//|                                                                  |
//|   ROOT CAUSE OF v6 FAILURE:                                      |
//|   v6 used BuyStop/SellStop pending orders at Asian boundaries.   |
//|   These fill on ANY tick touching the level — even intrabar      |
//|   spikes that reverse before bar close. Python only enters when  |
//|   the confirmed H1 bar CLOSE crosses the boundary. This created  |
//|   ~212 phantom false-breakout trades, dragging win rate from     |
//|   57% to 42% and PF from 1.73 to 0.97. Report 20: net -$2,738. |
//|                                                                  |
//|   v7 FIXES:                                                      |
//|                                                                  |
//|   1. BAR-CLOSE MARKET ORDERS (replaces pending stops)            |
//|      Uses IsNewBar() to detect confirmed H1 bar close. Reads    |
//|      previous bar close price, enters market order only if       |
//|      close > asian_high (long) or close < asian_low (short).    |
//|      Matches Python's bar-close signal logic exactly.            |
//|                                                                  |
//|   2. REMOVED MARKET FALLBACK                                     |
//|      No longer needed — bar-close logic handles all cases.       |
//|                                                                  |
//|   3. REMOVED EMA AMBIGUITY ZONE                                  |
//|      Python has no 20-pip buffer — just w1_close > w1_ema.       |
//|      v7 matches: simple > / < comparison, no ambiguity.          |
//|                                                                  |
//|   4. ABSOLUTE DD CAP FROM STARTING EQUITY                        |
//|      v6's trailing DD (8%) reset peak watermark after recovery,  |
//|      allowing compounding: 3 cycles = 22% total DD. v7 tracks   |
//|      g_starting_equity and enforces absolute DD cap.             |
//|                                                                  |
//|   5. REMOVED ALL PENDING STOP CODE                               |
//|      No BuyStop, SellStop, OCO, stops-level checks, or retry     |
//|      guards. Clean, simple entry via market orders on bar close. |
//|                                                                  |
//|  Strategy parameters (LOCKED, identical to Python):              |
//|     TP=1.5x range, range filter 15-60 pips, W1 EMA-26 trend     |
//|     filter (NO ambiguity zone), Asian 00:00-07:00 GMT,           |
//|     entry window 07:00-10:00 GMT, EOD 17:00 GMT                  |
//|                                                                  |
//|  Risk (LOCKED, Iter 10):                                         |
//|     Flat 0.75% — only sizing under user's 10% absolute DD cap.  |
//|     Python validation: DD=9.88%, 2x in 7.49y.                   |
//|                                                                  |
//|  Validated edge: OOS PF 1.733, DSR p<0.0005, Bootstrap 99%+      |
//+------------------------------------------------------------------+
#property copyright "London Breakout EA v7 - bar-close market orders"
#property version   "7.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== Strategy Parameters (LOCKED) ==="
input double InpTPMultiplier      = 1.5;
input double InpMinRangePips      = 15.0;
input double InpMaxRangePips      = 60.0;
input bool   InpUseTrendFilter    = true;
input int    InpW1EmaPeriod       = 26;

input group "=== Session Times (GMT) ==="
input int    InpAsianStartHour    = 0;
input int    InpAsianEndHour      = 7;
input int    InpLondonEndHour     = 10;
input int    InpEodExitHour       = 17;

input group "=== Risk — Iter 10 (10% DD cap) ==="
input bool   InpUseProgressiveRisk = false;
input double InpRiskPhase1Pct      = 0.5;
input double InpRiskPhase2Pct      = 1.0;
input double InpRiskPhase3Pct      = 1.5;
input int    InpPhase1DurationDays = 30;
input int    InpPhase2DurationDays = 60;
input double InpRiskPercent        = 0.75;
input double InpDailyLossLimitPct  = 3.0;
input double InpTrailingDDPct      = 8.0;

input group "=== Halt Mode ==="
input bool   InpHardHalt           = false;
input int    InpRecoveryDays       = 30;
input double InpRecoveryRiskPct    = 0.25;

input group "=== Safety & Operations ==="
input bool   InpTradingEnabled    = true;
input int    InpGMTOffsetHours    = 0;
input int    InpMagicNumber       = 778899;
input bool   InpVerboseLogging    = true;

input group "=== CSV Trade Logger ==="
input bool   InpCSVLogging        = true;
input string InpCSVFileName       = "LondonBreakout_v7_trades.csv";

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
bool          g_range_computed_today  = false;
double        g_peak_equity          = 0;
double        g_starting_equity      = 0;   // v7: absolute DD tracking from start
double        g_start_day_equity     = 0;
double        g_w1_ema_value         = 0;
int           g_w1_ema_cached_week   = -1;
datetime      g_current_day_gmt      = 0;
bool          g_daily_halt           = false;
bool          g_permanent_halt       = false;
datetime      g_recovery_start       = 0;
int           g_w1_ema_handle       = INVALID_HANDLE;
double        g_pip_size            = 0;
double        g_pip_value           = 0;
datetime      g_ea_start_time       = 0;
int           g_current_phase       = 1;

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
datetime GetGMTNow() { return TimeGMT(); }

int GetGMTHour(datetime gmt_time)
{
   MqlDateTime dt; TimeToStruct(gmt_time, dt);
   return dt.hour;
}

datetime GetGMTDayStart(datetime gmt_time)
{
   MqlDateTime dt; TimeToStruct(gmt_time, dt);
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
   MqlDateTime dt; TimeToStruct(t, dt);
   return dt.year * 100 + (dt.day_of_year / 7);
}

//+------------------------------------------------------------------+
//| v7: Convert broker bar timestamp to GMT hour                     |
//+------------------------------------------------------------------+
int GetBarGMTHour(datetime broker_bar_time)
{
   int gmt_offset = GetGMTOffsetSeconds();
   datetime gmt_time = broker_bar_time + gmt_offset;
   MqlDateTime dt; TimeToStruct(gmt_time, dt);
   return dt.hour;
}

//+------------------------------------------------------------------+
//| v7: New Bar Detection — only process signals on confirmed H1     |
//| bar close. From trading-bot-analyst skill (mql-snippets.md).     |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_H1, 0);
   if(currentBarTime != lastBarTime)
   {
      lastBarTime = currentBarTime;
      return true;
   }
   return false;
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
         "exit_reason","duration_minutes","phase","asian_range_pips","in_recovery");
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
   bool   in_recovery   = (g_recovery_start != 0);
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
      IntegerToString(duration_min),
      IntegerToString(g_current_phase),
      DoubleToString(g_range_pips, 1),
      in_recovery ? "YES" : "NO");
   FileClose(fh);
}

//+------------------------------------------------------------------+
//| GLOBAL VARIABLE PERSISTENCE                                      |
//+------------------------------------------------------------------+
void SaveStateToGlobals()
{
   GlobalVariableSet(gv_prefix+"State",              (double)g_state);
   GlobalVariableSet(gv_prefix+"AsianHigh",          g_asian_high);
   GlobalVariableSet(gv_prefix+"AsianLow",           g_asian_low);
   GlobalVariableSet(gv_prefix+"RangePips",          g_range_pips);
   GlobalVariableSet(gv_prefix+"EntryPrice",         g_entry_price);
   GlobalVariableSet(gv_prefix+"StopLoss",           g_stop_loss);
   GlobalVariableSet(gv_prefix+"TakeProfit",         g_take_profit);
   GlobalVariableSet(gv_prefix+"Direction",          (double)g_direction);
   GlobalVariableSet(gv_prefix+"TradeTakenToday",    g_trade_taken_today    ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"RangeComputedToday", g_range_computed_today ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"PeakEquity",         g_peak_equity);
   GlobalVariableSet(gv_prefix+"StartingEquity",     g_starting_equity);
   GlobalVariableSet(gv_prefix+"StartDayEquity",     g_start_day_equity);
   GlobalVariableSet(gv_prefix+"CurrentDayGMT",      (double)g_current_day_gmt);
   GlobalVariableSet(gv_prefix+"DailyHalt",          g_daily_halt           ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"PermanentHalt",      g_permanent_halt       ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"RecoveryStart",      (double)g_recovery_start);
   GlobalVariableSet(gv_prefix+"ActiveTicket",       (double)g_active_ticket);
   GlobalVariableSet(gv_prefix+"ActualEntry",        g_actual_entry);
   GlobalVariableSet(gv_prefix+"EntrySpread",        g_entry_spread);
   GlobalVariableSet(gv_prefix+"EntryEquity",        g_entry_equity);
   GlobalVariableSet(gv_prefix+"EntryLots",          g_entry_lots);
   GlobalVariableSet(gv_prefix+"EntryTime",          (double)g_entry_time);
   GlobalVariableSet(gv_prefix+"EAStartTime",        (double)g_ea_start_time);
   GlobalVariableSet(gv_prefix+"CurrentPhase",       (double)g_current_phase);
}

void LoadStateFromGlobals()
{
   if(!GlobalVariableCheck(gv_prefix+"State"))
   {
      LogInfo("No prior state found, initialising fresh");
      g_state            = STATE_WAITING;
      g_peak_equity      = AccountInfoDouble(ACCOUNT_EQUITY);
      g_starting_equity  = AccountInfoDouble(ACCOUNT_EQUITY);
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
   if(GlobalVariableCheck(gv_prefix+"StartingEquity"))
      g_starting_equity   = GlobalVariableGet(gv_prefix+"StartingEquity");
   else
      g_starting_equity   = g_peak_equity;   // fallback for first run
   g_start_day_equity     = GlobalVariableGet(gv_prefix+"StartDayEquity");
   g_current_day_gmt      = (datetime)(long)GlobalVariableGet(gv_prefix+"CurrentDayGMT");
   g_daily_halt           = GlobalVariableGet(gv_prefix+"DailyHalt")         > 0.5;
   g_permanent_halt       = GlobalVariableGet(gv_prefix+"PermanentHalt")     > 0.5;
   if(GlobalVariableCheck(gv_prefix+"RecoveryStart"))
      g_recovery_start    = (datetime)(long)GlobalVariableGet(gv_prefix+"RecoveryStart");
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
   LogInfo(StringFormat("State restored: %s | peak=%.2f | start=%.2f | ticket=%I64u | recovery=%s",
           StateName(g_state), g_peak_equity, g_starting_equity, g_active_ticket,
           g_recovery_start != 0 ? "ACTIVE" : "no"));
}

//+------------------------------------------------------------------+
//| INIT / DEINIT                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   gv_prefix = "LB7_" + _Symbol + "_";   // v7 prefix — prevents stale v6 state
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(20);

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

   LogInfo(StringFormat("EA v7.0 Init | %s | PipSize=%.5f | PipValue=%.2f | State=%s",
           _Symbol, g_pip_size, g_pip_value, StateName(g_state)));
   LogInfo(StringFormat("Strategy: TP=%.2fx | Range %.0f-%.0f | EMA-%d filter=%s (NO ambiguity zone)",
           InpTPMultiplier, InpMinRangePips, InpMaxRangePips, InpW1EmaPeriod,
           InpUseTrendFilter ? "ON" : "OFF"));
   LogInfo(StringFormat("Risk: Iter10 flat=%.2f%% | DailyDD=%.1f%% | TrailingDD=%.1f%% | HardHalt=%s",
           InpRiskPercent, InpDailyLossLimitPct, InpTrailingDDPct,
           InpHardHalt ? "YES (prop firm)" : "no (soft recovery)"));
   LogInfo("v7 Entry: BAR-CLOSE market orders (IsNewBar H1) - no pending stops");
   if(!InpTradingEnabled) LogCritical("TradingEnabled=false — observe-only mode");

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_w1_ema_handle != INVALID_HANDLE) IndicatorRelease(g_w1_ema_handle);
   SaveStateToGlobals();
   LogInfo(StringFormat("EA deinit (reason=%d), state saved", reason));
}

//+------------------------------------------------------------------+
//| W1 EMA — cached per week                                         |
//+------------------------------------------------------------------+
bool UpdateW1EMA()
{
   int week = GetISOWeekNumber(GetGMTNow());
   if(week == g_w1_ema_cached_week) return (g_w1_ema_value > 0);

   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(g_w1_ema_handle, 0, 1, 1, buf) <= 0)
   {
      LogCritical("Failed to read W1 EMA buffer");
      return false;
   }
   if(buf[0] <= 0 || buf[0] != buf[0])
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
//| ASIAN RANGE                                                      |
//+------------------------------------------------------------------+
bool ComputeAsianRange(datetime day_start_gmt)
{
   if(g_range_computed_today) return (g_asian_high > 0);

   datetime asian_start = day_start_gmt + InpAsianStartHour * 3600;
   datetime asian_end   = day_start_gmt + InpAsianEndHour   * 3600;

   int gmt_offset = GetGMTOffsetSeconds();
   datetime broker_start = asian_start - gmt_offset;
   datetime broker_end   = asian_end   - gmt_offset;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, PERIOD_H1, broker_start, broker_end, rates);
   if(copied < 3)
   {
      LogInfo(StringFormat("Asian range: only %d bars copied (need >=3), skipping", copied));
      g_range_computed_today = true;
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
//| RISK SIZING                                                      |
//+------------------------------------------------------------------+
double GetCurrentRiskPct()
{
   if(g_recovery_start != 0) return InpRecoveryRiskPct;
   if(!InpUseProgressiveRisk) return InpRiskPercent;

   if(g_ea_start_time == 0)
   {
      g_ea_start_time = GetGMTNow();
      GlobalVariableSet(gv_prefix+"EAStartTime", (double)g_ea_start_time);
   }

   long days = (long)((GetGMTNow() - g_ea_start_time) / 86400);
   int  new_phase;
   double risk_pct;
   if(days < InpPhase1DurationDays) { new_phase = 1; risk_pct = InpRiskPhase1Pct; }
   else if(days < InpPhase1DurationDays + InpPhase2DurationDays) { new_phase = 2; risk_pct = InpRiskPhase2Pct; }
   else { new_phase = 3; risk_pct = InpRiskPhase3Pct; }
   if(new_phase != g_current_phase)
   {
      LogCritical(StringFormat("PHASE TRANSITION: %d -> %d | Risk %.2f%%", g_current_phase, new_phase, risk_pct));
      g_current_phase = new_phase;
   }
   return risk_pct;
}

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
   if(lots < vol_min) lots = vol_min;
   if(lots > vol_max) lots = vol_max;

   LogInfo(StringFormat("Lots: equity=%.2f risk=%.2f%% ($%.2f) stop=%.1f pips → %.2f lots",
           equity, risk_pct, risk_dollars, stop_pips, lots));
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
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)        continue;
      g_planned_exit_reason = reason;
      trade.PositionClose(ticket);
      LogCritical(StringFormat("Closed #%I64u | reason: %s", ticket, reason));
   }
}

bool CheckSafetyLayers()
{
   if(!InpTradingEnabled)
   {
      if(g_state == STATE_TRADE_ACTIVE) CloseAllPositions("Layer 4: Manual kill");
      if(!g_permanent_halt) { g_permanent_halt = true; SaveStateToGlobals(); }
      return false;
   }
   if(g_permanent_halt) return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_peak_equity <= 0) g_peak_equity = equity;
   if(equity > g_peak_equity) g_peak_equity = equity;

   // v7 FIX: Absolute DD from starting equity (prevents recovery compounding)
   if(g_starting_equity > 0)
   {
      double absolute_dd = (g_starting_equity - equity) / g_starting_equity;
      if(absolute_dd > InpTrailingDDPct / 100.0)
      {
         if(InpHardHalt)
         {
            LogCritical(StringFormat("Layer 2A: ABSOLUTE DD %.2f%% from start — PERMANENT halt", absolute_dd * 100));
            CloseAllPositions("Layer 2A: Absolute DD hard halt");
            g_permanent_halt = true;
            SaveStateToGlobals();
            return false;
         }
         else
         {
            if(g_recovery_start == 0)
            {
               g_recovery_start = GetGMTNow();
               CloseAllPositions("Layer 2A: Absolute DD recovery start");
               LogCritical(StringFormat("RECOVERY MODE (absolute): DD %.2f%% from start=$%.2f — pausing %d days at %.1f%%",
                           absolute_dd * 100, g_starting_equity, InpRecoveryDays, InpRecoveryRiskPct));
               SaveStateToGlobals();
            }
            long days_in_recovery = (long)((GetGMTNow() - g_recovery_start) / 86400);
            if(days_in_recovery < InpRecoveryDays) return false;
            g_peak_equity    = equity;
            g_recovery_start = 0;
            LogCritical("Recovery complete — peak watermark reset, resuming");
            SaveStateToGlobals();
         }
      }
   }

   // Trailing DD from peak (original logic, secondary to absolute)
   double trailing_dd = (g_peak_equity - equity) / g_peak_equity;
   if(trailing_dd > InpTrailingDDPct / 100.0)
   {
      if(InpHardHalt)
      {
         LogCritical(StringFormat("Layer 2B: TRAILING DD %.2f%% from peak — PERMANENT halt", trailing_dd * 100));
         CloseAllPositions("Layer 2B: Trailing DD hard halt");
         g_permanent_halt = true;
         SaveStateToGlobals();
         return false;
      }
      else
      {
         if(g_recovery_start == 0)
         {
            g_recovery_start = GetGMTNow();
            CloseAllPositions("Layer 2B: Trailing DD recovery start");
            LogCritical(StringFormat("RECOVERY MODE (trailing): DD %.2f%% from peak=$%.2f — pausing %d days at %.1f%%",
                        trailing_dd * 100, g_peak_equity, InpRecoveryDays, InpRecoveryRiskPct));
            SaveStateToGlobals();
         }
         long days_in_recovery = (long)((GetGMTNow() - g_recovery_start) / 86400);
         if(days_in_recovery < InpRecoveryDays) return false;
         g_peak_equity    = equity;
         g_recovery_start = 0;
         LogCritical("Recovery complete — peak watermark reset, resuming");
         SaveStateToGlobals();
      }
   }

   if(g_start_day_equity <= 0) g_start_day_equity = equity;
   double daily_loss = (g_start_day_equity - equity) / g_start_day_equity;
   if(daily_loss > InpDailyLossLimitPct / 100.0)
   {
      LogCritical(StringFormat("Layer 1: Daily DD %.2f%% — halted today", daily_loss * 100));
      CloseAllPositions("Layer 1: Daily DD");
      g_daily_halt = true;
      SaveStateToGlobals();
      return false;
   }

   if(g_state == STATE_TRADE_ACTIVE && g_stop_loss > 0)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      bool sl_hit = (g_direction > 0 && bid <= g_stop_loss) ||
                    (g_direction < 0 && ask >= g_stop_loss);
      if(sl_hit)
      {
         LogCritical(StringFormat("Layer 3: Internal SL hit (bid=%.5f ask=%.5f sl=%.5f)", bid, ask, g_stop_loss));
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
   g_start_day_equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_daily_halt)
   {
      g_daily_halt = false;
      LogInfo("Daily DD halt lifted for new trading day");
   }
   SaveStateToGlobals();
}

//+------------------------------------------------------------------+
//| W1 TREND FILTER — v7: NO ambiguity zone (matches Python exactly) |
//+------------------------------------------------------------------+
int GetTrendDirection()
{
   if(!InpUseTrendFilter) return 2;   // 2 = both directions allowed
   if(!UpdateW1EMA()) return 0;
   if(g_w1_ema_value <= 0) return 0;

   MqlRates bars[];
   ArraySetAsSeries(bars, true);
   if(CopyRates(_Symbol, PERIOD_H1, 1, 1, bars) <= 0) return 0;
   double last_close = bars[0].close;

   // v7: Simple comparison — NO ambiguity zone
   // Matches Python exactly: allow_long = w1_close > w1_ema_v
   if(last_close > g_w1_ema_value) return  1;   // bullish: longs only
   if(last_close < g_w1_ema_value) return -1;   // bearish: shorts only
   return 0;   // exactly equal: skip
}

//+------------------------------------------------------------------+
//| v7 BAR-CLOSE ENTRY — replaces all pending stop logic             |
//|                                                                  |
//| Called ONLY when IsNewBar() fires. Reads previous bar close and  |
//| enters market order if breakout confirmed. Matches Python:       |
//|   if allow_long and bar['close'] > asian_high:                   |
//|       entry_price = asian_high                                   |
//+------------------------------------------------------------------+
void CheckBarCloseEntry()
{
   if(g_state != STATE_RANGE_SET || g_trade_taken_today) return;

   MqlRates bars[];
   ArraySetAsSeries(bars, true);
   if(CopyRates(_Symbol, PERIOD_H1, 1, 1, bars) < 1) return;

   // Convert broker bar time to GMT hour
   int bar_gmt_hour = GetBarGMTHour(bars[0].time);

   // Only check bars within London window: 07:00, 08:00, 09:00 GMT
   if(bar_gmt_hour < InpAsianEndHour || bar_gmt_hour >= InpLondonEndHour) return;

   int trend = GetTrendDirection();
   if(trend == 0)
   {
      LogInfo("Trend filter: skip today (ambiguity/no data)");
      g_trade_taken_today = true;
      SaveStateToGlobals();
      return;
   }

   double prevClose = bars[0].close;
   double range     = g_asian_high - g_asian_low;
   double stop_pips = range / g_pip_size;

   if(stop_pips <= 0) return;

   double lots = CalculateLots(stop_pips);
   if(lots <= 0) return;

   double pre_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   bool entered = false;

   // LONG: bar close > asian_high AND trend is bullish
   if((trend == 1 || trend == 2) && prevClose > g_asian_high)
   {
      double sl = NormalizeDouble(g_asian_low, _Digits);
      double tp = NormalizeDouble(g_asian_high + InpTPMultiplier * range, _Digits);

      LogInfo(StringFormat("BAR-CLOSE LONG: prevClose=%.5f > asian_high=%.5f | SL=%.5f TP=%.5f lots=%.2f",
              prevClose, g_asian_high, sl, tp, lots));

      if(trade.Buy(lots, _Symbol, 0, sl, tp, "LB-Long"))
      {
         g_state              = STATE_TRADE_ACTIVE;
         g_direction          = 1;
         g_entry_price        = g_asian_high;   // boundary price (Python model)
         g_actual_entry       = trade.ResultPrice();
         g_stop_loss          = sl;
         g_take_profit        = tp;
         g_active_ticket      = trade.ResultOrder();
         g_entry_time         = GetGMTNow();
         g_trade_taken_today  = true;
         g_entry_equity       = pre_equity;
         g_entry_lots         = lots;
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         g_entry_spread       = (ask - bid) / g_pip_size;
         g_planned_exit_reason = "";

         double slippage = (g_actual_entry - g_entry_price) / g_pip_size;
         LogStateTransition(STATE_RANGE_SET, STATE_TRADE_ACTIVE,
                            StringFormat("LONG @%.5f (boundary=%.5f, slip=%.1f pips)", g_actual_entry, g_entry_price, slippage));
         SaveStateToGlobals();
         entered = true;
      }
      else
      {
         LogCritical(StringFormat("Market Buy failed: retcode=%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      }
   }

   // SHORT: bar close < asian_low AND trend is bearish
   if(!entered && (trend == -1 || trend == 2) && prevClose < g_asian_low)
   {
      double sl = NormalizeDouble(g_asian_high, _Digits);
      double tp = NormalizeDouble(g_asian_low - InpTPMultiplier * range, _Digits);

      LogInfo(StringFormat("BAR-CLOSE SHORT: prevClose=%.5f < asian_low=%.5f | SL=%.5f TP=%.5f lots=%.2f",
              prevClose, g_asian_low, sl, tp, lots));

      if(trade.Sell(lots, _Symbol, 0, sl, tp, "LB-Short"))
      {
         g_state              = STATE_TRADE_ACTIVE;
         g_direction          = -1;
         g_entry_price        = g_asian_low;   // boundary price (Python model)
         g_actual_entry       = trade.ResultPrice();
         g_stop_loss          = sl;
         g_take_profit        = tp;
         g_active_ticket      = trade.ResultOrder();
         g_entry_time         = GetGMTNow();
         g_trade_taken_today  = true;
         g_entry_equity       = pre_equity;
         g_entry_lots         = lots;
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         g_entry_spread       = (ask - bid) / g_pip_size;
         g_planned_exit_reason = "";

         double slippage = (g_entry_price - g_actual_entry) / g_pip_size;
         LogStateTransition(STATE_RANGE_SET, STATE_TRADE_ACTIVE,
                            StringFormat("SHORT @%.5f (boundary=%.5f, slip=%.1f pips)", g_actual_entry, g_entry_price, slippage));
         SaveStateToGlobals();
         entered = true;
      }
      else
      {
         LogCritical(StringFormat("Market Sell failed: retcode=%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      }
   }
}

//+------------------------------------------------------------------+
//| TRADE CLOSE                                                      |
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
   g_active_ticket       = 0;
   g_actual_entry        = 0;
   g_entry_spread        = 0;
   g_entry_equity        = 0;
   g_entry_lots          = 0;
   g_entry_time          = 0;
   g_planned_exit_reason = "";
}

//+------------------------------------------------------------------+
//| MAIN TICK HANDLER — v7 simplified state machine                  |
//|                                                                  |
//| Entry logic ONLY runs on IsNewBar() — confirmed H1 bar closes.   |
//| Trade management (closure detection, EOD exit) runs every tick.  |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime gmt       = GetGMTNow();
   datetime day_start = GetGMTDayStart(gmt);
   int      hour      = GetGMTHour(gmt);

   if(g_current_day_gmt != day_start) DailyReset(day_start);

   // Detect trade closure — runs every tick
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

   // Safety layers — runs every tick
   if(!CheckSafetyLayers()) return;
   if(g_daily_halt || g_permanent_halt) return;

   // ====================================================================
   // v7: ALL ENTRY LOGIC BELOW ONLY RUNS ON CONFIRMED BAR CLOSE
   // ====================================================================
   if(IsNewBar())
   {
      // STATE_WAITING -> STATE_RANGE_SET (compute Asian range)
      if(g_state == STATE_WAITING && !g_trade_taken_today)
      {
         // Check if we're past Asian session
         // Use current GMT hour (not bar hour) for state transition timing
         if(hour >= InpAsianEndHour)
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
            }
         }
      }

      // STATE_RANGE_SET: Check bar close for breakout entry
      // IMPORTANT: CheckBarCloseEntry runs BEFORE window expiry check
      // so the 09:00 bar (last London bar) gets checked
      if(g_state == STATE_RANGE_SET && !g_trade_taken_today)
      {
         CheckBarCloseEntry();
      }

      // London window expired — after checking the last bar
      if(g_state == STATE_RANGE_SET)
      {
         // Read current bar's GMT hour to see if London window has passed
         MqlRates cur_bar[];
         ArraySetAsSeries(cur_bar, true);
         if(CopyRates(_Symbol, PERIOD_H1, 0, 1, cur_bar) >= 1)
         {
            int cur_bar_gmt_hour = GetBarGMTHour(cur_bar[0].time);
            if(cur_bar_gmt_hour >= InpLondonEndHour)
            {
               LogStateTransition(STATE_RANGE_SET, STATE_WAITING, "London window expired (10:00 GMT)");
               g_state             = STATE_WAITING;
               g_trade_taken_today = true;
               SaveStateToGlobals();
            }
         }
      }
   }

   // EOD exit — runs every tick (not just new bar), catches trades that need closing at 17:00
   if(g_state == STATE_TRADE_ACTIVE && hour >= InpEodExitHour)
   {
      LogInfo(StringFormat("EOD exit triggered at hour %d GMT", hour));
      g_planned_exit_reason = "EOD";
      CloseAllPositions("EOD exit (17:00 GMT)");
   }
}
//+------------------------------------------------------------------+
