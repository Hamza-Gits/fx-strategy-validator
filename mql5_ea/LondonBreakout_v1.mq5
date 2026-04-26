//+------------------------------------------------------------------+
//|                                          LondonBreakout_v1.mq5  |
//|                          GBPUSD London Open Range Breakout EA   |
//|                                                                  |
//|  Validated edge: OOS PF 1.733, DSR p<0.0005, Bootstrap 99%+      |
//|  10 years backtest (2015-2024), 184 OOS trades, 56-57% win rate  |
//|                                                                  |
//|  Locked parameters (do NOT optimize on live results):            |
//|    TP=1.5x range, range 15-60 pips, W1 EMA-26 trend filter       |
//|                                                                  |
//|  Target: The5ers $10k challenge ($4/lot commission, 0.2-0.9 pip) |
//+------------------------------------------------------------------+
#property copyright "London Breakout EA - Council-approved architecture"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== Strategy Parameters (LOCKED - do not change) ==="
input double InpTPMultiplier      = 1.5;    // TP as multiple of Asian range
input double InpMinRangePips      = 15.0;   // Min Asian range (pips)
input double InpMaxRangePips      = 60.0;   // Max Asian range (pips) - was 80 in arch, 60 = backtest winner
input bool   InpUseTrendFilter    = true;   // Enable W1 EMA filter
input int    InpW1EmaPeriod       = 26;     // W1 EMA period
input double InpEmaAmbiguityPips  = 20.0;   // Skip if price within X pips of EMA

input group "=== Session Times (GMT) ==="
input int    InpAsianStartHour    = 0;      // Asian session start (00:00 GMT)
input int    InpAsianEndHour      = 7;      // Asian session end (07:00 GMT)
input int    InpLondonEndHour     = 10;     // London entry window end (10:00 GMT)
input int    InpEodExitHour       = 17;     // EOD force-close (17:00 GMT)

input group "=== Risk Management ==="
input double InpRiskPercent       = 0.5;    // Risk % of equity per trade
input double InpDailyLossLimitPct = 3.5;    // Daily DD halt threshold
input double InpTrailingDDPct     = 4.0;    // Trailing DD halt threshold

input group "=== Safety & Operations ==="
input bool   InpTradingEnabled    = true;   // Master kill switch (Layer 4)
input int    InpGMTOffsetHours    = 0;      // Manual GMT offset override (0 = auto)
input int    InpMagicNumber       = 778899; // Magic number for trade ID
input bool   InpVerboseLogging    = true;   // Verbose decision logging

//+------------------------------------------------------------------+
//| GLOBAL STATE                                                     |
//+------------------------------------------------------------------+
enum ENUM_EA_STATE
{
   STATE_WAITING      = 0,  // 00:00-07:00 GMT, scanning Asian bars
   STATE_RANGE_SET    = 1,  // 07:00-10:00 GMT, watching breakout
   STATE_TRADE_ACTIVE = 2   // Position open, monitoring
};

// GlobalVariable name prefix (survives restarts)
string gv_prefix = "";

// In-memory cache of state (synced with GlobalVariables)
ENUM_EA_STATE g_state            = STATE_WAITING;
double        g_asian_high       = 0;
double        g_asian_low        = 0;
double        g_range_pips       = 0;
double        g_entry_price      = 0;
double        g_stop_loss        = 0;
double        g_take_profit      = 0;
int           g_direction        = 0;
bool          g_trade_taken_today = false;
double        g_peak_equity      = 0;
double        g_start_day_equity = 0;
double        g_w1_ema_value     = 0;
int           g_w1_ema_cached_week = -1;
datetime      g_current_day_gmt  = 0;
bool          g_trading_allowed  = true;
int           g_w1_ema_handle    = INVALID_HANDLE;
double        g_pip_size         = 0;
double        g_pip_value        = 0;

//+------------------------------------------------------------------+
//| GMT HELPERS                                                      |
//+------------------------------------------------------------------+
int GetGMTOffsetSeconds()
{
   if(InpGMTOffsetHours != 0)
      return InpGMTOffsetHours * 3600;
   return (int)(TimeGMT() - TimeCurrent());
}

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
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;
   return StructToTime(dt);
}

int GetISOWeekNumber(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   // Approximate week number (good enough for cache invalidation)
   return dt.year * 100 + (dt.day_of_year / 7);
}

//+------------------------------------------------------------------+
//| LOGGING                                                          |
//+------------------------------------------------------------------+
void LogInfo(string msg)
{
   if(!InpVerboseLogging) return;
   datetime gmt = GetGMTNow();
   PrintFormat("[%s GMT] %s", TimeToString(gmt, TIME_DATE|TIME_SECONDS), msg);
}

void LogCritical(string msg)
{
   datetime gmt = GetGMTNow();
   PrintFormat("[%s GMT] *** CRITICAL: %s ***", TimeToString(gmt, TIME_DATE|TIME_SECONDS), msg);
}

void LogStateTransition(ENUM_EA_STATE old_s, ENUM_EA_STATE new_s, string trigger)
{
   string old_name = StateName(old_s);
   string new_name = StateName(new_s);
   LogInfo(StringFormat("STATE: %s -> %s | trigger: %s", old_name, new_name, trigger));
}

string StateName(ENUM_EA_STATE s)
{
   if(s == STATE_WAITING) return "WAITING";
   if(s == STATE_RANGE_SET) return "RANGE_SET";
   if(s == STATE_TRADE_ACTIVE) return "TRADE_ACTIVE";
   return "UNKNOWN";
}

//+------------------------------------------------------------------+
//| GLOBAL VARIABLE PERSISTENCE                                      |
//+------------------------------------------------------------------+
void SaveStateToGlobals()
{
   GlobalVariableSet(gv_prefix + "State", (double)g_state);
   GlobalVariableSet(gv_prefix + "AsianHigh", g_asian_high);
   GlobalVariableSet(gv_prefix + "AsianLow", g_asian_low);
   GlobalVariableSet(gv_prefix + "RangePips", g_range_pips);
   GlobalVariableSet(gv_prefix + "EntryPrice", g_entry_price);
   GlobalVariableSet(gv_prefix + "StopLoss", g_stop_loss);
   GlobalVariableSet(gv_prefix + "TakeProfit", g_take_profit);
   GlobalVariableSet(gv_prefix + "Direction", (double)g_direction);
   GlobalVariableSet(gv_prefix + "TradeTakenToday", g_trade_taken_today ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix + "PeakEquity", g_peak_equity);
   GlobalVariableSet(gv_prefix + "StartDayEquity", g_start_day_equity);
   GlobalVariableSet(gv_prefix + "CurrentDayGMT", (double)g_current_day_gmt);
   GlobalVariableSet(gv_prefix + "TradingAllowed", g_trading_allowed ? 1.0 : 0.0);
}

void LoadStateFromGlobals()
{
   if(GlobalVariableCheck(gv_prefix + "State"))
   {
      g_state             = (ENUM_EA_STATE)(int)GlobalVariableGet(gv_prefix + "State");
      g_asian_high        = GlobalVariableGet(gv_prefix + "AsianHigh");
      g_asian_low         = GlobalVariableGet(gv_prefix + "AsianLow");
      g_range_pips        = GlobalVariableGet(gv_prefix + "RangePips");
      g_entry_price       = GlobalVariableGet(gv_prefix + "EntryPrice");
      g_stop_loss         = GlobalVariableGet(gv_prefix + "StopLoss");
      g_take_profit       = GlobalVariableGet(gv_prefix + "TakeProfit");
      g_direction         = (int)GlobalVariableGet(gv_prefix + "Direction");
      g_trade_taken_today = GlobalVariableGet(gv_prefix + "TradeTakenToday") > 0.5;
      g_peak_equity       = GlobalVariableGet(gv_prefix + "PeakEquity");
      g_start_day_equity  = GlobalVariableGet(gv_prefix + "StartDayEquity");
      g_current_day_gmt   = (datetime)(long)GlobalVariableGet(gv_prefix + "CurrentDayGMT");
      g_trading_allowed   = GlobalVariableGet(gv_prefix + "TradingAllowed") > 0.5;
      LogInfo(StringFormat("State restored from globals: %s, equity_peak=%.2f", StateName(g_state), g_peak_equity));
   }
   else
   {
      LogInfo("No prior state found, initializing fresh");
      g_state = STATE_WAITING;
      g_peak_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      g_start_day_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   }
}

//+------------------------------------------------------------------+
//| INIT / DEINIT                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   gv_prefix = "LB_" + _Symbol + "_";
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   // Pip calculation (forex: 0.0001, JPY: 0.01)
   string sym = _Symbol;
   bool is_jpy = (StringFind(sym, "JPY") >= 0);
   g_pip_size = is_jpy ? 0.01 : 0.0001;

   // Pip value per lot in account currency
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size > 0)
      g_pip_value = (g_pip_size / tick_size) * tick_value;
   else
      g_pip_value = 10.0; // fallback for standard lot

   // GMT offset verification (Layer 0 safety)
   int offset = GetGMTOffsetSeconds();
   LogInfo(StringFormat("GMT offset: %d seconds (%.2f hours)", offset, offset/3600.0));

   // W1 EMA indicator handle
   g_w1_ema_handle = iMA(_Symbol, PERIOD_W1, InpW1EmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(g_w1_ema_handle == INVALID_HANDLE)
   {
      LogCritical("Failed to create W1 EMA indicator handle");
      return INIT_FAILED;
   }

   LoadStateFromGlobals();

   LogInfo(StringFormat("EA Initialized | Symbol=%s | PipSize=%.5f | PipValue=%.2f | State=%s",
           _Symbol, g_pip_size, g_pip_value, StateName(g_state)));
   LogInfo(StringFormat("Strategy: TP=%.2fx, Range %0.0f-%0.0f pips, W1EMA-%d filter=%s",
           InpTPMultiplier, InpMinRangePips, InpMaxRangePips, InpW1EmaPeriod,
           InpUseTrendFilter ? "ON" : "OFF"));
   LogInfo(StringFormat("Risk: %.2f%% per trade | DailyDD halt: %.2f%% | TrailingDD halt: %.2f%%",
           InpRiskPercent, InpDailyLossLimitPct, InpTrailingDDPct));

   if(!InpTradingEnabled)
      LogCritical("TradingEnabled=false (Layer 4 kill switch). EA running in observe-only mode.");

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_w1_ema_handle != INVALID_HANDLE)
      IndicatorRelease(g_w1_ema_handle);
   SaveStateToGlobals();
   LogInfo(StringFormat("EA Deinitialized (reason=%d), state saved", reason));
}

//+------------------------------------------------------------------+
//| W1 EMA (cached weekly)                                           |
//+------------------------------------------------------------------+
bool UpdateW1EMA()
{
   datetime gmt = GetGMTNow();
   int current_week = GetISOWeekNumber(gmt);
   if(current_week != g_w1_ema_cached_week)
   {
      double buffer[];
      ArraySetAsSeries(buffer, true);
      // shift=1 = use the last CLOSED weekly bar (not forming)
      if(CopyBuffer(g_w1_ema_handle, 0, 1, 1, buffer) <= 0)
      {
         LogCritical("Failed to read W1 EMA buffer");
         return false;
      }
      g_w1_ema_value = buffer[0];
      g_w1_ema_cached_week = current_week;
      LogInfo(StringFormat("W1 EMA-%d refreshed: %.5f", InpW1EmaPeriod, g_w1_ema_value));
   }
   return true;
}

//+------------------------------------------------------------------+
//| ASIAN RANGE DETECTION                                            |
//+------------------------------------------------------------------+
bool ComputeAsianRange(datetime day_start_gmt, double &out_high, double &out_low, double &out_range_pips)
{
   // Asian session: day_start + 0..7 hours GMT
   datetime asian_start = day_start_gmt + InpAsianStartHour * 3600;
   datetime asian_end   = day_start_gmt + InpAsianEndHour * 3600;

   // Convert GMT bounds to broker time for CopyRates
   int gmt_offset = GetGMTOffsetSeconds();
   datetime asian_start_broker = asian_start - gmt_offset;
   datetime asian_end_broker   = asian_end - gmt_offset;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, PERIOD_H1, asian_start_broker, asian_end_broker - 1, rates);
   if(copied < 3)
   {
      LogInfo(StringFormat("Asian range: only %d bars available (need >=3), skipping day", copied));
      return false;
   }

   double hi = -DBL_MAX;
   double lo = DBL_MAX;
   for(int i = 0; i < copied; i++)
   {
      if(rates[i].high > hi) hi = rates[i].high;
      if(rates[i].low  < lo) lo = rates[i].low;
   }

   out_high = hi;
   out_low = lo;
   out_range_pips = (hi - lo) / g_pip_size;

   LogInfo(StringFormat("Asian range computed: H=%.5f L=%.5f Range=%.1f pips (bars=%d)",
           hi, lo, out_range_pips, copied));
   return true;
}

//+------------------------------------------------------------------+
//| POSITION SIZING (0.5% equity risk)                               |
//+------------------------------------------------------------------+
double CalculateLots(double stop_pips)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_dollars = equity * (InpRiskPercent / 100.0);
   double lots_raw = risk_dollars / (stop_pips * g_pip_value);

   // Normalize to broker volume step
   double vol_min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vol_max  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vol_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(vol_step <= 0) vol_step = 0.01;
   double lots = MathFloor(lots_raw / vol_step) * vol_step;
   lots = MathMax(vol_min, MathMin(vol_max, lots));

   LogInfo(StringFormat("Position size: equity=$%.2f, risk=$%.2f, stop=%.1f pips, raw_lots=%.4f, final=%.2f",
           equity, risk_dollars, stop_pips, lots_raw, lots));
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
      if(PositionSelectByTicket(ticket))
      {
         if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         trade.PositionClose(ticket);
         LogCritical(StringFormat("Closed position #%I64u | reason: %s", ticket, reason));
      }
   }
}

bool CheckSafetyLayers()
{
   // Layer 4: Manual kill switch
   if(!InpTradingEnabled)
   {
      if(g_state == STATE_TRADE_ACTIVE) CloseAllPositions("Layer 4: Manual kill switch");
      g_trading_allowed = false;
      return false;
   }

   // Layer 0: GMT offset sanity (skip if extreme drift)
   int offset = GetGMTOffsetSeconds();
   if(MathAbs(offset) > 50400) // > 14 hours = clearly wrong
   {
      LogCritical(StringFormat("GMT offset extreme: %d sec - halting", offset));
      g_trading_allowed = false;
      return false;
   }

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   // Layer 2: Trailing peak DD (most critical for The5ers)
   if(g_peak_equity <= 0) g_peak_equity = equity;
   if(equity > g_peak_equity) g_peak_equity = equity;
   double trailing_dd = (g_peak_equity - equity) / g_peak_equity;
   if(trailing_dd > InpTrailingDDPct / 100.0)
   {
      LogCritical(StringFormat("Layer 2: Trailing DD breach! peak=$%.2f, equity=$%.2f, dd=%.2f%%",
                  g_peak_equity, equity, trailing_dd * 100));
      CloseAllPositions("Layer 2: Trailing DD");
      g_trading_allowed = false;
      return false;
   }

   // Layer 1: Daily DD
   if(g_start_day_equity <= 0) g_start_day_equity = equity;
   double daily_loss = (g_start_day_equity - equity) / g_start_day_equity;
   if(daily_loss > InpDailyLossLimitPct / 100.0)
   {
      LogCritical(StringFormat("Layer 1: Daily DD breach! start=$%.2f, equity=$%.2f, loss=%.2f%%",
                  g_start_day_equity, equity, daily_loss * 100));
      CloseAllPositions("Layer 1: Daily DD");
      g_trading_allowed = false;
      return false;
   }

   // Layer 3: Internal SL enforcement (handles spread spikes)
   if(g_state == STATE_TRADE_ACTIVE && g_stop_loss > 0)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      bool sl_breached = false;
      if(g_direction > 0 && bid <= g_stop_loss) sl_breached = true;
      if(g_direction < 0 && ask >= g_stop_loss) sl_breached = true;
      if(sl_breached)
      {
         LogCritical(StringFormat("Layer 3: Internal SL triggered (bid=%.5f, ask=%.5f, sl=%.5f, dir=%d)",
                     bid, ask, g_stop_loss, g_direction));
         CloseAllPositions("Layer 3: Internal SL");
         // State reset handled by position-close detection in main loop
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//| DAILY RESET (at 00:00 GMT)                                       |
//+------------------------------------------------------------------+
void DailyReset(datetime new_day_gmt)
{
   LogInfo("=== DAILY RESET (00:00 GMT) ===");
   g_current_day_gmt = new_day_gmt;
   g_state = STATE_WAITING;
   g_asian_high = 0;
   g_asian_low = 0;
   g_range_pips = 0;
   g_entry_price = 0;
   g_stop_loss = 0;
   g_take_profit = 0;
   g_direction = 0;
   g_trade_taken_today = false;
   g_start_day_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   SaveStateToGlobals();
}

//+------------------------------------------------------------------+
//| LONDON ENTRY DETECTION                                           |
//+------------------------------------------------------------------+
bool CheckLondonBreakout(double &out_direction, double &out_entry, double &out_sl, double &out_tp)
{
   // Get last fully-closed H1 bar
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, PERIOD_H1, 1, 1, rates) <= 0) return false;
   double last_close = rates[0].close;

   // Trend filter
   bool allow_long = true;
   bool allow_short = true;
   if(InpUseTrendFilter)
   {
      if(!UpdateW1EMA()) return false;
      if(g_w1_ema_value <= 0) return false;

      // Ambiguity zone
      double dist_pips = MathAbs(last_close - g_w1_ema_value) / g_pip_size;
      if(dist_pips < InpEmaAmbiguityPips)
      {
         LogInfo(StringFormat("EMA ambiguity: dist=%.1f pips (< %.1f), skipping", dist_pips, InpEmaAmbiguityPips));
         return false;
      }
      allow_long  = (last_close > g_w1_ema_value);
      allow_short = (last_close < g_w1_ema_value);
   }

   // Breakout check
   if(allow_long && last_close > g_asian_high)
   {
      out_direction = 1;
      out_entry = g_asian_high;  // RANGE BOUNDARY, not bar close
      out_sl = g_asian_low;
      out_tp = g_asian_high + (InpTPMultiplier * (g_asian_high - g_asian_low));
      LogInfo(StringFormat("LONG breakout: close=%.5f > AH=%.5f, EMA=%.5f", last_close, g_asian_high, g_w1_ema_value));
      return true;
   }
   if(allow_short && last_close < g_asian_low)
   {
      out_direction = -1;
      out_entry = g_asian_low;
      out_sl = g_asian_high;
      out_tp = g_asian_low - (InpTPMultiplier * (g_asian_high - g_asian_low));
      LogInfo(StringFormat("SHORT breakout: close=%.5f < AL=%.5f, EMA=%.5f", last_close, g_asian_low, g_w1_ema_value));
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

   bool ok = false;
   if(dir > 0)
      ok = trade.Buy(lots, _Symbol, 0, sl, tp, "LB-Long");
   else
      ok = trade.Sell(lots, _Symbol, 0, sl, tp, "LB-Short");

   if(ok)
   {
      g_state = STATE_TRADE_ACTIVE;
      g_direction = (int)dir;
      g_entry_price = entry;
      g_stop_loss = sl;
      g_take_profit = tp;
      g_trade_taken_today = true;
      LogStateTransition(STATE_RANGE_SET, STATE_TRADE_ACTIVE, "London breakout entry");
      LogInfo(StringFormat("ENTERED %s | lots=%.2f | entry=%.5f | SL=%.5f | TP=%.5f",
              dir > 0 ? "LONG" : "SHORT", lots, entry, sl, tp));
      SaveStateToGlobals();
      return true;
   }
   else
   {
      LogCritical(StringFormat("Order failed: %d - %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      return false;
   }
}

//+------------------------------------------------------------------+
//| MAIN TICK HANDLER                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime gmt = GetGMTNow();
   datetime day_start = GetGMTDayStart(gmt);
   int hour = GetGMTHour(gmt);

   // Daily reset
   if(g_current_day_gmt != day_start)
      DailyReset(day_start);

   // Position closure detection (state reset to WAITING)
   if(g_state == STATE_TRADE_ACTIVE)
   {
      bool pos_open = false;
      for(int i = 0; i < PositionsTotal(); i++)
      {
         ulong ticket = PositionGetTicket(i);
         if(PositionSelectByTicket(ticket))
         {
            if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber &&
               PositionGetString(POSITION_SYMBOL) == _Symbol)
            {
               pos_open = true;
               break;
            }
         }
      }
      if(!pos_open)
      {
         LogStateTransition(STATE_TRADE_ACTIVE, STATE_WAITING, "Position closed (SL/TP/manual)");
         g_state = STATE_WAITING;
         g_direction = 0;
         g_entry_price = 0;
         g_stop_loss = 0;
         g_take_profit = 0;
         SaveStateToGlobals();
      }
   }

   // Safety checks (run on every tick)
   if(!CheckSafetyLayers())
      return;

   if(!g_trading_allowed) return;

   // === STATE LOGIC ===

   // STATE_WAITING -> STATE_RANGE_SET (at end of Asian session, hour >= 7)
   if(g_state == STATE_WAITING && hour >= InpAsianEndHour && hour < InpLondonEndHour && !g_trade_taken_today)
   {
      double hi, lo, range_pips;
      if(ComputeAsianRange(day_start, hi, lo, range_pips))
      {
         if(range_pips < InpMinRangePips)
         {
            LogInfo(StringFormat("Range %.1f < min %.1f, skipping day", range_pips, InpMinRangePips));
            g_trade_taken_today = true; // mark to avoid retesting
         }
         else if(range_pips > InpMaxRangePips)
         {
            LogInfo(StringFormat("Range %.1f > max %.1f, skipping day", range_pips, InpMaxRangePips));
            g_trade_taken_today = true;
         }
         else
         {
            g_asian_high = hi;
            g_asian_low = lo;
            g_range_pips = range_pips;
            g_state = STATE_RANGE_SET;
            LogStateTransition(STATE_WAITING, STATE_RANGE_SET,
                               StringFormat("Asian range valid: %.1f pips", range_pips));
            SaveStateToGlobals();
         }
      }
   }

   // STATE_RANGE_SET -> STATE_TRADE_ACTIVE (during London window)
   if(g_state == STATE_RANGE_SET && !g_trade_taken_today)
   {
      // Only check on a new H1 bar close (not every tick)
      static datetime last_checked_bar = 0;
      datetime current_h1_bar_time = iTime(_Symbol, PERIOD_H1, 0);
      if(current_h1_bar_time != last_checked_bar && hour < InpLondonEndHour)
      {
         last_checked_bar = current_h1_bar_time;
         EnterTrade();
      }

      // Window expired without entry
      if(hour >= InpLondonEndHour)
      {
         LogStateTransition(STATE_RANGE_SET, STATE_WAITING, "London window expired without breakout");
         g_state = STATE_WAITING;
         g_trade_taken_today = true;
         SaveStateToGlobals();
      }
   }

   // STATE_TRADE_ACTIVE -> EOD exit (at 17:00 GMT)
   if(g_state == STATE_TRADE_ACTIVE && hour >= InpEodExitHour)
   {
      LogInfo(StringFormat("EOD exit triggered at hour %d GMT", hour));
      CloseAllPositions("EOD exit (17:00 GMT)");
      // State reset will happen on next tick via position-closure detection
   }
}

//+------------------------------------------------------------------+
