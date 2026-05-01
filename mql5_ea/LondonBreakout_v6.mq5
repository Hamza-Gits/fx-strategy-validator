//+------------------------------------------------------------------+
//|                                          LondonBreakout_v6.mq5  |
//|     GBPUSD London Breakout EA — STOP ORDERS HARDENED (v6)       |
//|                                                                  |
//|  v6 CRITICAL FIXES (over v5 / Report 19 disaster):               |
//|                                                                  |
//|   1. INFINITE RETRY LOOP FIX                                     |
//|      v5 only set g_pendings_placed=true when placed_any=true.    |
//|      If both BuyStop AND SellStop failed (e.g. broker rejection, |
//|      stops-level violation), the EA retried EVERY TICK forever  |
//|      (Report 19: thousands of "Invalid price" errors per day).  |
//|      v6: g_pendings_placed=true is set UNCONDITIONALLY after    |
//|      the placement attempt. One attempt per day, period.        |
//|                                                                  |
//|   2. LATE PLACEMENT FIX                                          |
//|      v5 placed pendings whenever OnTick happened to enter the   |
//|      RANGE_SET state, which could be 09:01 GMT instead of 07:00.|
//|      By 09:01, price had often already moved past asian_high,  |
//|      making BuyStop INVALID (must be > current Ask).             |
//|      v6: Tries to place at 07:00 GMT precisely (first tick      |
//|      after Asian session closes). If we're late and price has   |
//|      already moved past one boundary, that side falls back to   |
//|      a MARKET order (matching Python "fill at boundary" intent).|
//|                                                                  |
//|   3. STOPS LEVEL VALIDATION                                      |
//|      Some brokers require pending stop orders to be at least    |
//|      SYMBOL_TRADE_STOPS_LEVEL points away from current market.  |
//|      v6 reads this and enforces minimum distance. If our        |
//|      desired price is too close, we fall back to market order.  |
//|                                                                  |
//|   4. PRICE-PAST-LEVEL FALLBACK                                   |
//|      If at placement time (regardless of hour) Ask > asian_high,|
//|      the long side immediately enters via market order at the   |
//|      current Ask (not BuyStop). Same for short side if          |
//|      Bid < asian_low. This captures breakouts that Python's    |
//|      bar-close logic would have caught.                          |
//|                                                                  |
//|   5. DIAGNOSTIC LOGGING                                          |
//|      On any order failure, log: requested price, current Ask,   |
//|      current Bid, stops-level minimum distance. Makes future    |
//|      bugs trivial to diagnose from journal alone.                |
//|                                                                  |
//|  Strategy parameters (LOCKED, identical to v5):                  |
//|     TP=1.5x range, range filter 15-60 pips, W1 EMA-26 trend     |
//|     filter (skip within 20 pips), Asian 00:00-07:00 GMT,        |
//|     entry window 07:00-10:00 GMT, EOD 17:00 GMT                  |
//|                                                                  |
//|  Risk (LOCKED, Iter 10):                                         |
//|     Flat 0.75% — only sizing under user's 10% absolute DD cap.  |
//|     Python validation: DD=9.88%, 2x in 7.49y.                   |
//|                                                                  |
//|  Validated edge: OOS PF 1.733, DSR p<0.0005, Bootstrap 99%+      |
//+------------------------------------------------------------------+
#property copyright "London Breakout EA v6 - hardened stop orders"
#property version   "6.00"
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
input double InpEmaAmbiguityPips  = 20.0;

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

input group "=== v6 Execution Hardening ==="
input bool   InpAllowMarketFallback = true;  // If price already past boundary, enter via market order (matches Python intent)
input int    InpExtraStopsBuffer    = 2;     // Extra points beyond broker stops-level (safety margin)

input group "=== CSV Trade Logger ==="
input bool   InpCSVLogging        = true;
input string InpCSVFileName       = "LondonBreakout_v6_trades.csv";

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

ulong         g_buystop_ticket      = 0;
ulong         g_sellstop_ticket     = 0;
bool          g_pendings_placed     = false;   // SET ONCE PER DAY, success OR fail (v6 retry guard)

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
   GlobalVariableSet(gv_prefix+"PendingsPlaced",     g_pendings_placed       ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"PeakEquity",         g_peak_equity);
   GlobalVariableSet(gv_prefix+"StartDayEquity",     g_start_day_equity);
   GlobalVariableSet(gv_prefix+"CurrentDayGMT",      (double)g_current_day_gmt);
   GlobalVariableSet(gv_prefix+"DailyHalt",          g_daily_halt           ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"PermanentHalt",      g_permanent_halt       ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"RecoveryStart",      (double)g_recovery_start);
   GlobalVariableSet(gv_prefix+"ActiveTicket",    (double)g_active_ticket);
   GlobalVariableSet(gv_prefix+"BuyStopTicket",   (double)g_buystop_ticket);
   GlobalVariableSet(gv_prefix+"SellStopTicket",  (double)g_sellstop_ticket);
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
   if(GlobalVariableCheck(gv_prefix+"PendingsPlaced"))
      g_pendings_placed   = GlobalVariableGet(gv_prefix+"PendingsPlaced")     > 0.5;
   g_peak_equity          = GlobalVariableGet(gv_prefix+"PeakEquity");
   g_start_day_equity     = GlobalVariableGet(gv_prefix+"StartDayEquity");
   g_current_day_gmt      = (datetime)(long)GlobalVariableGet(gv_prefix+"CurrentDayGMT");
   g_daily_halt           = GlobalVariableGet(gv_prefix+"DailyHalt")         > 0.5;
   g_permanent_halt       = GlobalVariableGet(gv_prefix+"PermanentHalt")     > 0.5;
   if(GlobalVariableCheck(gv_prefix+"RecoveryStart"))
      g_recovery_start    = (datetime)(long)GlobalVariableGet(gv_prefix+"RecoveryStart");
   g_active_ticket       = (ulong)GlobalVariableGet(gv_prefix+"ActiveTicket");
   if(GlobalVariableCheck(gv_prefix+"BuyStopTicket"))
      g_buystop_ticket   = (ulong)GlobalVariableGet(gv_prefix+"BuyStopTicket");
   if(GlobalVariableCheck(gv_prefix+"SellStopTicket"))
      g_sellstop_ticket  = (ulong)GlobalVariableGet(gv_prefix+"SellStopTicket");
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
   LogInfo(StringFormat("State restored: %s | peak=%.2f | ticket=%I64u | recovery=%s",
           StateName(g_state), g_peak_equity, g_active_ticket,
           g_recovery_start != 0 ? "ACTIVE" : "no"));
}

//+------------------------------------------------------------------+
//| INIT / DEINIT                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   gv_prefix = "LB6_" + _Symbol + "_";
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

   LogInfo(StringFormat("EA v6.0 Init | %s | PipSize=%.5f | PipValue=%.2f | State=%s",
           _Symbol, g_pip_size, g_pip_value, StateName(g_state)));
   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   LogInfo(StringFormat("Broker StopsLevel=%d points (~%.1f pips), MarketFallback=%s",
           stops_level, stops_level / 10.0, InpAllowMarketFallback ? "ON" : "OFF"));
   LogInfo(StringFormat("Strategy: TP=%.2fx | Range %.0f-%.0f | EMA-%d filter=%s",
           InpTPMultiplier, InpMinRangePips, InpMaxRangePips, InpW1EmaPeriod,
           InpUseTrendFilter ? "ON" : "OFF"));
   LogInfo(StringFormat("Risk: Iter10 flat=%.2f%% | DailyDD=%.1f%% | TrailingDD=%.1f%% | HardHalt=%s",
           InpRiskPercent, InpDailyLossLimitPct, InpTrailingDDPct,
           InpHardHalt ? "YES (prop firm)" : "no (soft recovery)"));
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
   lots = MathMax(vol_min, MathMin(vol_max, lots));

   LogInfo(StringFormat("Sizing: phase=%d risk=%.2f%% equity=$%.2f risk_$=$%.2f stop=%.1f lots=%.2f%s",
           g_current_phase, risk_pct, equity, risk_dollars, stop_pips, lots,
           g_recovery_start != 0 ? " [RECOVERY]" : ""));
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

   double trailing_dd = (g_peak_equity - equity) / g_peak_equity;
   if(trailing_dd > InpTrailingDDPct / 100.0)
   {
      if(InpHardHalt)
      {
         LogCritical(StringFormat("Layer 2: HARD trailing DD %.2f%% — PERMANENT halt", trailing_dd * 100));
         CloseAllPositions("Layer 2: Hard halt");
         g_permanent_halt = true;
         SaveStateToGlobals();
         return false;
      }
      else
      {
         if(g_recovery_start == 0)
         {
            g_recovery_start = GetGMTNow();
            CloseAllPositions("Layer 2: Recovery mode start");
            LogCritical(StringFormat("RECOVERY MODE: trailing DD %.2f%% — pausing %d days at %.1f%%",
                        trailing_dd * 100, InpRecoveryDays, InpRecoveryRiskPct));
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
   if(g_pendings_placed) CancelAllPendings("Daily reset cleanup");
   g_buystop_ticket       = 0;
   g_sellstop_ticket      = 0;
   g_pendings_placed      = false;
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
//| W1 TREND FILTER                                                  |
//+------------------------------------------------------------------+
int GetTrendDirection()
{
   if(!InpUseTrendFilter) return 2;
   if(!UpdateW1EMA()) return 0;
   if(g_w1_ema_value <= 0) return 0;

   MqlRates bars[];
   ArraySetAsSeries(bars, true);
   if(CopyRates(_Symbol, PERIOD_H1, 1, 1, bars) <= 0) return 0;
   double last_close = bars[0].close;

   double dist_pips = MathAbs(last_close - g_w1_ema_value) / g_pip_size;
   if(dist_pips < InpEmaAmbiguityPips)
   {
      LogInfo(StringFormat("EMA ambiguity: dist=%.1f pips — skip today", dist_pips));
      return 0;
   }
   if(last_close > g_w1_ema_value) return  1;
   if(last_close < g_w1_ema_value) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| v6 ORDER PLACEMENT — hardened with retry guard, market fallback, |
//| stops-level enforcement, and detailed failure logging.           |
//+------------------------------------------------------------------+
bool TryPlaceBuyStopOrMarket(double price, double sl, double tp, double lots, datetime expiration, long stops_level_points)
{
   double ask        = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double point      = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double min_dist   = (stops_level_points + InpExtraStopsBuffer) * point;
   double price_norm = NormalizeDouble(price, _Digits);

   bool need_market = false;
   string fallback_reason = "";

   if(ask >= price_norm)
   {
      need_market = true;
      fallback_reason = StringFormat("Ask %.5f >= boundary %.5f (price already past)", ask, price_norm);
   }
   else if((price_norm - ask) < min_dist)
   {
      need_market = true;
      fallback_reason = StringFormat("Boundary %.5f within %.1f pts of Ask %.5f (stops-level %d)",
                                     price_norm, (price_norm - ask) / point, ask, stops_level_points);
   }

   if(need_market && !InpAllowMarketFallback)
   {
      LogCritical(StringFormat("BuyStop SKIPPED — market fallback OFF. %s", fallback_reason));
      return false;
   }

   if(need_market)
   {
      LogInfo(StringFormat("BuyStop -> MARKET fallback: %s", fallback_reason));
      bool ok = trade.Buy(lots, _Symbol, 0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), "LB-MarketBuy");
      if(!ok)
         LogCritical(StringFormat("Market Buy failed: retcode=%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      return ok;
   }

   MqlTradeRequest req = {}; MqlTradeResult res = {};
   req.action       = TRADE_ACTION_PENDING;
   req.symbol       = _Symbol;
   req.volume       = lots;
   req.type         = ORDER_TYPE_BUY_STOP;
   req.price        = price_norm;
   req.sl           = NormalizeDouble(sl, _Digits);
   req.tp           = NormalizeDouble(tp, _Digits);
   req.deviation    = 20;
   req.magic        = InpMagicNumber;
   req.type_filling = ORDER_FILLING_RETURN;
   req.type_time    = ORDER_TIME_SPECIFIED;
   req.expiration   = expiration;
   req.comment      = "LB-BuyStop";
   if(OrderSend(req, res) && res.retcode == TRADE_RETCODE_DONE)
   {
      g_buystop_ticket = res.order;
      LogInfo(StringFormat("Placed BuyStop @%.5f SL=%.5f TP=%.5f lots=%.2f exp=%s",
              req.price, req.sl, req.tp, lots, TimeToString(expiration, TIME_DATE|TIME_MINUTES)));
      return true;
   }
   LogCritical(StringFormat("BuyStop failed: retcode=%d %s | req_price=%.5f Ask=%.5f minDist=%.1fpts",
               res.retcode, res.comment, req.price, ask, min_dist / point));
   return false;
}

bool TryPlaceSellStopOrMarket(double price, double sl, double tp, double lots, datetime expiration, long stops_level_points)
{
   double bid        = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double point      = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double min_dist   = (stops_level_points + InpExtraStopsBuffer) * point;
   double price_norm = NormalizeDouble(price, _Digits);

   bool need_market = false;
   string fallback_reason = "";

   if(bid <= price_norm)
   {
      need_market = true;
      fallback_reason = StringFormat("Bid %.5f <= boundary %.5f (price already past)", bid, price_norm);
   }
   else if((bid - price_norm) < min_dist)
   {
      need_market = true;
      fallback_reason = StringFormat("Boundary %.5f within %.1f pts of Bid %.5f (stops-level %d)",
                                     price_norm, (bid - price_norm) / point, bid, stops_level_points);
   }

   if(need_market && !InpAllowMarketFallback)
   {
      LogCritical(StringFormat("SellStop SKIPPED — market fallback OFF. %s", fallback_reason));
      return false;
   }

   if(need_market)
   {
      LogInfo(StringFormat("SellStop -> MARKET fallback: %s", fallback_reason));
      bool ok = trade.Sell(lots, _Symbol, 0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), "LB-MarketSell");
      if(!ok)
         LogCritical(StringFormat("Market Sell failed: retcode=%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      return ok;
   }

   MqlTradeRequest req = {}; MqlTradeResult res = {};
   req.action       = TRADE_ACTION_PENDING;
   req.symbol       = _Symbol;
   req.volume       = lots;
   req.type         = ORDER_TYPE_SELL_STOP;
   req.price        = price_norm;
   req.sl           = NormalizeDouble(sl, _Digits);
   req.tp           = NormalizeDouble(tp, _Digits);
   req.deviation    = 20;
   req.magic        = InpMagicNumber;
   req.type_filling = ORDER_FILLING_RETURN;
   req.type_time    = ORDER_TIME_SPECIFIED;
   req.expiration   = expiration;
   req.comment      = "LB-SellStop";
   if(OrderSend(req, res) && res.retcode == TRADE_RETCODE_DONE)
   {
      g_sellstop_ticket = res.order;
      LogInfo(StringFormat("Placed SellStop @%.5f SL=%.5f TP=%.5f lots=%.2f exp=%s",
              req.price, req.sl, req.tp, lots, TimeToString(expiration, TIME_DATE|TIME_MINUTES)));
      return true;
   }
   LogCritical(StringFormat("SellStop failed: retcode=%d %s | req_price=%.5f Bid=%.5f minDist=%.1fpts",
               res.retcode, res.comment, req.price, bid, min_dist / point));
   return false;
}

//+------------------------------------------------------------------+
//| Place pendings — called ONCE per day at first valid OnTick after |
//| 07:00 GMT. Sets g_pendings_placed=true unconditionally to prevent |
//| infinite retry on broker rejection.                              |
//+------------------------------------------------------------------+
void PlacePendingOrders()
{
   if(g_pendings_placed) return;
   if(g_asian_high <= 0 || g_asian_low <= 0)
   {
      g_pendings_placed = true; // guard: don't retry without valid range
      return;
   }

   int trend = GetTrendDirection();
   if(trend == 0)
   {
      LogInfo("Trend filter: skip today");
      g_pendings_placed   = true;
      g_trade_taken_today = true;
      SaveStateToGlobals();
      return;
   }

   double range = g_asian_high - g_asian_low;
   double tp_long  = g_asian_high + InpTPMultiplier * range;
   double tp_short = g_asian_low  - InpTPMultiplier * range;
   double stop_pips = range / g_pip_size;
   if(stop_pips <= 0)
   {
      g_pendings_placed = true;
      return;
   }

   double lots = CalculateLots(stop_pips);
   if(lots <= 0)
   {
      g_pendings_placed = true;
      return;
   }

   double pre_equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   datetime expiration    = GetGMTDayStart(GetGMTNow()) + InpLondonEndHour * 3600 - GetGMTOffsetSeconds();
   long stops_level_points = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);

   bool placed_any = false;
   if(trend == 1 || trend == 2)
      placed_any |= TryPlaceBuyStopOrMarket(g_asian_high, g_asian_low, tp_long, lots, expiration, stops_level_points);
   if(trend == -1 || trend == 2)
      placed_any |= TryPlaceSellStopOrMarket(g_asian_low, g_asian_high, tp_short, lots, expiration, stops_level_points);

   // CRITICAL v6 FIX: set guard regardless of success — one attempt per day, full stop.
   g_pendings_placed = true;

   if(placed_any)
   {
      g_entry_equity = pre_equity;
      g_entry_lots   = lots;
   }
   else
   {
      LogCritical("All order placements failed — no trade today (retry blocked, will reset 00:00 GMT)");
      g_trade_taken_today = true;
   }
   SaveStateToGlobals();
}

//+------------------------------------------------------------------+
//| Cancel single pending                                            |
//+------------------------------------------------------------------+
void CancelPendingOrder(ulong ticket)
{
   if(ticket == 0) return;
   if(!OrderSelect(ticket)) return;
   MqlTradeRequest req = {}; MqlTradeResult res = {};
   req.action = TRADE_ACTION_REMOVE;
   req.order  = ticket;
   if(OrderSend(req, res))
      LogInfo(StringFormat("Cancelled pending #%I64u retcode=%d", ticket, res.retcode));
}

void CancelAllPendings(string reason)
{
   if(g_buystop_ticket != 0)  { CancelPendingOrder(g_buystop_ticket);  g_buystop_ticket  = 0; }
   if(g_sellstop_ticket != 0) { CancelPendingOrder(g_sellstop_ticket); g_sellstop_ticket = 0; }
   LogInfo(StringFormat("Cancelled all pending orders: %s", reason));
}

//+------------------------------------------------------------------+
//| OCO + fill detection                                             |
//+------------------------------------------------------------------+
void HandlePendingFills()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)        continue;

      if(g_state != STATE_TRADE_ACTIVE)
      {
         long pos_type = PositionGetInteger(POSITION_TYPE);
         double pos_open = PositionGetDouble(POSITION_PRICE_OPEN);
         double pos_sl   = PositionGetDouble(POSITION_SL);
         double pos_tp   = PositionGetDouble(POSITION_TP);

         g_state              = STATE_TRADE_ACTIVE;
         g_direction          = (pos_type == POSITION_TYPE_BUY) ? 1 : -1;
         g_entry_price        = (g_direction > 0) ? g_asian_high : g_asian_low;
         g_actual_entry       = pos_open;
         g_stop_loss          = pos_sl;
         g_take_profit        = pos_tp;
         g_active_ticket      = t;
         g_entry_time         = (datetime)PositionGetInteger(POSITION_TIME);
         g_trade_taken_today  = true;
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         g_entry_spread       = (ask - bid) / g_pip_size;
         g_planned_exit_reason = "";

         LogStateTransition(STATE_RANGE_SET, STATE_TRADE_ACTIVE,
                            StringFormat("Filled @%.5f (%s)", pos_open, g_direction > 0 ? "LONG" : "SHORT"));
         SaveStateToGlobals();

         if(g_direction > 0 && g_sellstop_ticket != 0) { CancelPendingOrder(g_sellstop_ticket); g_sellstop_ticket = 0; }
         if(g_direction < 0 && g_buystop_ticket  != 0) { CancelPendingOrder(g_buystop_ticket);  g_buystop_ticket  = 0; }
      }
      return;
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
//| MAIN TICK HANDLER                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime gmt       = GetGMTNow();
   datetime day_start = GetGMTDayStart(gmt);
   int      hour      = GetGMTHour(gmt);

   if(g_current_day_gmt != day_start) DailyReset(day_start);

   // Detect fills regardless of state
   if(g_pendings_placed && g_state == STATE_RANGE_SET) HandlePendingFills();

   // Detect closure
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
   if(g_daily_halt || g_permanent_halt)
   {
      if(g_pendings_placed && (g_buystop_ticket != 0 || g_sellstop_ticket != 0))
         CancelAllPendings("Safety halt");
      return;
   }

   // STATE_WAITING -> STATE_RANGE_SET
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
      }
   }

   // STATE_RANGE_SET — place pendings ONCE
   if(g_state == STATE_RANGE_SET && !g_pendings_placed && !g_trade_taken_today &&
      hour >= InpAsianEndHour && hour < InpLondonEndHour)
   {
      PlacePendingOrders();   // sets g_pendings_placed=true unconditionally
   }

   // London window expired
   if(hour >= InpLondonEndHour && g_state == STATE_RANGE_SET)
   {
      if(g_buystop_ticket != 0 || g_sellstop_ticket != 0)
         CancelAllPendings("London window expired (10:00 GMT)");
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
