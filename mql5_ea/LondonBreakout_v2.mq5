//+------------------------------------------------------------------+
//|                                          LondonBreakout_v2.mq5  |
//|                          GBPUSD London Open Range Breakout EA   |
//|                                                                  |
//|  v2 ADDITIONS:                                                   |
//|   - Per-trade CSV logger for live PF / slippage tracking         |
//|   - Tracks expected entry price, actual fill, slippage in pips   |
//|   - Records spread at entry, exit reason, equity-relative pnl    |
//|   - Required for Council Phase 1 live edge verification          |
//|                                                                  |
//|  Validated edge: OOS PF 1.733, DSR p<0.0005, Bootstrap 99%+      |
//|  Locked params: TP=1.5x, range 15-60 pips, W1 EMA-26             |
//+------------------------------------------------------------------+
#property copyright "London Breakout EA v2 - Council scaling phase 1"
#property version   "2.00"
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

input group "=== Risk Management ==="
input double InpRiskPercent       = 0.5;    // Risk % of equity per trade (0.5 = Phase 1, 1.0 = Phase 2)
input double InpDailyLossLimitPct = 3.5;    // Daily DD halt threshold
input double InpTrailingDDPct     = 4.0;    // Trailing DD halt threshold

input group "=== Safety & Operations ==="
input bool   InpTradingEnabled    = true;   // Master kill switch (Layer 4)
input int    InpGMTOffsetHours    = 0;      // Manual GMT offset override (0 = auto)
input int    InpMagicNumber       = 778899; // Magic number for trade ID
input bool   InpVerboseLogging    = true;   // Verbose decision logging

input group "=== CSV Trade Logger (v2) ==="
input bool   InpCSVLogging        = true;   // Write each trade to CSV (live PF tracking)
input string InpCSVFileName       = "LondonBreakout_trades.csv";  // CSV filename in MQL5/Files/

//+------------------------------------------------------------------+
//| GLOBAL STATE                                                     |
//+------------------------------------------------------------------+
enum ENUM_EA_STATE
{
   STATE_WAITING      = 0,
   STATE_RANGE_SET    = 1,
   STATE_TRADE_ACTIVE = 2
};

string gv_prefix = "";

ENUM_EA_STATE g_state            = STATE_WAITING;
double        g_asian_high       = 0;
double        g_asian_low        = 0;
double        g_range_pips       = 0;
double        g_entry_price      = 0;       // expected entry (range boundary)
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

// === v2: Trade context for CSV logging ===
ulong         g_active_ticket    = 0;       // current open position ticket
double        g_actual_entry     = 0;       // actual fill price from broker
double        g_entry_spread     = 0;       // spread at entry (pips)
double        g_entry_equity     = 0;       // equity at entry
double        g_entry_lots       = 0;       // lots traded
datetime      g_entry_time       = 0;       // entry timestamp (GMT)
string        g_planned_exit_reason = "";   // forecast exit reason

//+------------------------------------------------------------------+
//| GMT HELPERS                                                      |
//+------------------------------------------------------------------+
int GetGMTOffsetSeconds()
{
   if(InpGMTOffsetHours != 0) return InpGMTOffsetHours * 3600;
   return (int)(TimeGMT() - TimeCurrent());
}

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
   if(s == STATE_WAITING) return "WAITING";
   if(s == STATE_RANGE_SET) return "RANGE_SET";
   if(s == STATE_TRADE_ACTIVE) return "TRADE_ACTIVE";
   return "UNKNOWN";
}

void LogStateTransition(ENUM_EA_STATE old_s, ENUM_EA_STATE new_s, string trigger)
{
   LogInfo(StringFormat("STATE: %s -> %s | trigger: %s", StateName(old_s), StateName(new_s), trigger));
}

//+------------------------------------------------------------------+
//| CSV TRADE LOGGER (v2 NEW)                                        |
//+------------------------------------------------------------------+
void EnsureCSVHeader()
{
   if(!InpCSVLogging) return;
   // Only write header if file doesn't exist
   int fh = FileOpen(InpCSVFileName, FILE_READ|FILE_CSV|FILE_ANSI, ',');
   if(fh != INVALID_HANDLE)
   {
      FileClose(fh);
      return; // already exists
   }
   // Create with header
   fh = FileOpen(InpCSVFileName, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(fh != INVALID_HANDLE)
   {
      FileWrite(fh,
         "entry_time_gmt", "exit_time_gmt", "symbol", "direction",
         "expected_entry", "actual_entry", "slippage_pips",
         "stop_loss", "take_profit", "exit_price",
         "entry_spread_pips", "lots",
         "entry_equity", "exit_equity", "pnl_dollars", "pnl_pct",
         "exit_reason", "duration_minutes"
      );
      FileClose(fh);
      LogInfo(StringFormat("CSV created: %s", InpCSVFileName));
   }
   else
   {
      LogCritical(StringFormat("Failed to create CSV file: %s (err=%d)", InpCSVFileName, GetLastError()));
   }
}

void WriteTradeToCSV(datetime exit_time_gmt, double exit_price, double exit_equity, string exit_reason)
{
   if(!InpCSVLogging) return;
   int fh = FileOpen(InpCSVFileName, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(fh == INVALID_HANDLE)
   {
      LogCritical(StringFormat("Failed to open CSV for append: %s (err=%d)", InpCSVFileName, GetLastError()));
      return;
   }
   FileSeek(fh, 0, SEEK_END);

   double slippage_pips = (g_actual_entry - g_entry_price) / g_pip_size * (g_direction > 0 ? 1 : -1);
   double pnl_dollars = exit_equity - g_entry_equity;
   double pnl_pct = (g_entry_equity > 0) ? (pnl_dollars / g_entry_equity * 100.0) : 0;
   int duration_min = (int)((exit_time_gmt - g_entry_time) / 60);

   FileWrite(fh,
      TimeToString(g_entry_time, TIME_DATE|TIME_SECONDS),
      TimeToString(exit_time_gmt, TIME_DATE|TIME_SECONDS),
      _Symbol,
      g_direction > 0 ? "LONG" : "SHORT",
      DoubleToString(g_entry_price, _Digits),
      DoubleToString(g_actual_entry, _Digits),
      DoubleToString(slippage_pips, 2),
      DoubleToString(g_stop_loss, _Digits),
      DoubleToString(g_take_profit, _Digits),
      DoubleToString(exit_price, _Digits),
      DoubleToString(g_entry_spread, 2),
      DoubleToString(g_entry_lots, 2),
      DoubleToString(g_entry_equity, 2),
      DoubleToString(exit_equity, 2),
      DoubleToString(pnl_dollars, 2),
      DoubleToString(pnl_pct, 4),
      exit_reason,
      IntegerToString(duration_min)
   );
   FileClose(fh);
   LogInfo(StringFormat("CSV row written: %s | pnl=$%.2f (%.3f%%) | slippage=%.2f pips",
           exit_reason, pnl_dollars, pnl_pct, slippage_pips));
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
   // v2: Trade context
   GlobalVariableSet(gv_prefix + "ActiveTicket", (double)g_active_ticket);
   GlobalVariableSet(gv_prefix + "ActualEntry", g_actual_entry);
   GlobalVariableSet(gv_prefix + "EntrySpread", g_entry_spread);
   GlobalVariableSet(gv_prefix + "EntryEquity", g_entry_equity);
   GlobalVariableSet(gv_prefix + "EntryLots", g_entry_lots);
   GlobalVariableSet(gv_prefix + "EntryTime", (double)g_entry_time);
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
      // v2: Trade context
      g_active_ticket     = (ulong)GlobalVariableGet(gv_prefix + "ActiveTicket");
      g_actual_entry      = GlobalVariableGet(gv_prefix + "ActualEntry");
      g_entry_spread      = GlobalVariableGet(gv_prefix + "EntrySpread");
      g_entry_equity      = GlobalVariableGet(gv_prefix + "EntryEquity");
      g_entry_lots        = GlobalVariableGet(gv_prefix + "EntryLots");
      g_entry_time        = (datetime)(long)GlobalVariableGet(gv_prefix + "EntryTime");
      LogInfo(StringFormat("State restored: %s, peak=%.2f, ticket=%I64u",
              StateName(g_state), g_peak_equity, g_active_ticket));
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

   bool is_jpy = (StringFind(_Symbol, "JPY") >= 0);
   g_pip_size = is_jpy ? 0.01 : 0.0001;

   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   g_pip_value = (tick_size > 0) ? (g_pip_size / tick_size) * tick_value : 10.0;

   int offset = GetGMTOffsetSeconds();
   LogInfo(StringFormat("GMT offset: %d sec (%.2f hrs)", offset, offset/3600.0));

   g_w1_ema_handle = iMA(_Symbol, PERIOD_W1, InpW1EmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(g_w1_ema_handle == INVALID_HANDLE)
   {
      LogCritical("Failed to create W1 EMA handle");
      return INIT_FAILED;
   }

   LoadStateFromGlobals();
   EnsureCSVHeader();

   LogInfo(StringFormat("EA v2.0 Init | %s | PipSize=%.5f | PipValue=%.2f | State=%s",
           _Symbol, g_pip_size, g_pip_value, StateName(g_state)));
   LogInfo(StringFormat("Strategy: TP=%.2fx | Range %.0f-%.0f | EMA-%d filter=%s",
           InpTPMultiplier, InpMinRangePips, InpMaxRangePips, InpW1EmaPeriod,
           InpUseTrendFilter ? "ON" : "OFF"));
   LogInfo(StringFormat("Risk=%.2f%% | DailyDD=%.2f%% | TrailingDD=%.2f%% | CSV=%s",
           InpRiskPercent, InpDailyLossLimitPct, InpTrailingDDPct,
           InpCSVLogging ? InpCSVFileName : "OFF"));

   if(!InpTradingEnabled)
      LogCritical("TradingEnabled=false (Layer 4 OFF). Observe-only mode.");

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_w1_ema_handle != INVALID_HANDLE) IndicatorRelease(g_w1_ema_handle);
   SaveStateToGlobals();
   LogInfo(StringFormat("EA v2 Deinit (reason=%d), state saved", reason));
}

//+------------------------------------------------------------------+
//| W1 EMA (cached weekly)                                           |
//+------------------------------------------------------------------+
bool UpdateW1EMA()
{
   int current_week = GetISOWeekNumber(GetGMTNow());
   if(current_week != g_w1_ema_cached_week)
   {
      double buffer[];
      ArraySetAsSeries(buffer, true);
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
//| ASIAN RANGE                                                      |
//+------------------------------------------------------------------+
bool ComputeAsianRange(datetime day_start_gmt, double &out_high, double &out_low, double &out_range_pips)
{
   datetime asian_start = day_start_gmt + InpAsianStartHour * 3600;
   datetime asian_end   = day_start_gmt + InpAsianEndHour * 3600;
   int gmt_offset = GetGMTOffsetSeconds();
   datetime asian_start_broker = asian_start - gmt_offset;
   datetime asian_end_broker   = asian_end - gmt_offset;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, PERIOD_H1, asian_start_broker, asian_end_broker - 1, rates);
   if(copied < 3)
   {
      LogInfo(StringFormat("Asian range: only %d bars (need >=3)", copied));
      return false;
   }

   double hi = -DBL_MAX, lo = DBL_MAX;
   for(int i = 0; i < copied; i++)
   {
      if(rates[i].high > hi) hi = rates[i].high;
      if(rates[i].low  < lo) lo = rates[i].low;
   }
   out_high = hi; out_low = lo;
   out_range_pips = (hi - lo) / g_pip_size;
   LogInfo(StringFormat("Asian range: H=%.5f L=%.5f R=%.1f pips (%d bars)",
           hi, lo, out_range_pips, copied));
   return true;
}

//+------------------------------------------------------------------+
//| POSITION SIZING                                                  |
//+------------------------------------------------------------------+
double CalculateLots(double stop_pips)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_dollars = equity * (InpRiskPercent / 100.0);
   double lots_raw = risk_dollars / (stop_pips * g_pip_value);

   double vol_min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vol_max  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vol_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vol_step <= 0) vol_step = 0.01;

   double lots = MathFloor(lots_raw / vol_step) * vol_step;
   lots = MathMax(vol_min, MathMin(vol_max, lots));

   LogInfo(StringFormat("Sizing: equity=$%.2f risk=$%.2f stop=%.1f raw=%.4f final=%.2f",
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
         g_planned_exit_reason = reason;
         trade.PositionClose(ticket);
         LogCritical(StringFormat("Closed #%I64u | reason: %s", ticket, reason));
      }
   }
}

bool CheckSafetyLayers()
{
   if(!InpTradingEnabled)
   {
      if(g_state == STATE_TRADE_ACTIVE) CloseAllPositions("Layer 4: Manual kill");
      g_trading_allowed = false;
      return false;
   }

   int offset = GetGMTOffsetSeconds();
   if(MathAbs(offset) > 50400)
   {
      LogCritical(StringFormat("GMT offset extreme: %d sec - halting", offset));
      g_trading_allowed = false;
      return false;
   }

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_peak_equity <= 0) g_peak_equity = equity;
   if(equity > g_peak_equity) g_peak_equity = equity;
   double trailing_dd = (g_peak_equity - equity) / g_peak_equity;
   if(trailing_dd > InpTrailingDDPct / 100.0)
   {
      LogCritical(StringFormat("Layer 2: Trailing DD breach! peak=$%.2f equity=$%.2f dd=%.2f%%",
                  g_peak_equity, equity, trailing_dd * 100));
      CloseAllPositions("Layer 2: Trailing DD");
      g_trading_allowed = false;
      return false;
   }

   if(g_start_day_equity <= 0) g_start_day_equity = equity;
   double daily_loss = (g_start_day_equity - equity) / g_start_day_equity;
   if(daily_loss > InpDailyLossLimitPct / 100.0)
   {
      LogCritical(StringFormat("Layer 1: Daily DD breach! start=$%.2f equity=$%.2f loss=%.2f%%",
                  g_start_day_equity, equity, daily_loss * 100));
      CloseAllPositions("Layer 1: Daily DD");
      g_trading_allowed = false;
      return false;
   }

   if(g_state == STATE_TRADE_ACTIVE && g_stop_loss > 0)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      bool sl_breached = false;
      if(g_direction > 0 && bid <= g_stop_loss) sl_breached = true;
      if(g_direction < 0 && ask >= g_stop_loss) sl_breached = true;
      if(sl_breached)
      {
         LogCritical(StringFormat("Layer 3: Internal SL (bid=%.5f ask=%.5f sl=%.5f)",
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
   g_current_day_gmt = new_day_gmt;
   g_state = STATE_WAITING;
   g_asian_high = 0; g_asian_low = 0; g_range_pips = 0;
   g_entry_price = 0; g_stop_loss = 0; g_take_profit = 0;
   g_direction = 0;
   g_trade_taken_today = false;
   g_start_day_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   SaveStateToGlobals();
}

//+------------------------------------------------------------------+
//| LONDON BREAKOUT DETECTION                                        |
//+------------------------------------------------------------------+
bool CheckLondonBreakout(double &out_direction, double &out_entry, double &out_sl, double &out_tp)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, PERIOD_H1, 1, 1, rates) <= 0) return false;
   double last_close = rates[0].close;

   bool allow_long = true, allow_short = true;
   if(InpUseTrendFilter)
   {
      if(!UpdateW1EMA()) return false;
      if(g_w1_ema_value <= 0) return false;
      double dist_pips = MathAbs(last_close - g_w1_ema_value) / g_pip_size;
      if(dist_pips < InpEmaAmbiguityPips)
      {
         LogInfo(StringFormat("EMA ambiguity: dist=%.1f pips, skipping", dist_pips));
         return false;
      }
      allow_long  = (last_close > g_w1_ema_value);
      allow_short = (last_close < g_w1_ema_value);
   }

   if(allow_long && last_close > g_asian_high)
   {
      out_direction = 1;
      out_entry = g_asian_high;
      out_sl = g_asian_low;
      out_tp = g_asian_high + (InpTPMultiplier * (g_asian_high - g_asian_low));
      LogInfo(StringFormat("LONG breakout: close=%.5f > AH=%.5f", last_close, g_asian_high));
      return true;
   }
   if(allow_short && last_close < g_asian_low)
   {
      out_direction = -1;
      out_entry = g_asian_low;
      out_sl = g_asian_high;
      out_tp = g_asian_low - (InpTPMultiplier * (g_asian_high - g_asian_low));
      LogInfo(StringFormat("SHORT breakout: close=%.5f < AL=%.5f", last_close, g_asian_low));
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| EXECUTE ENTRY (with v2 trade context capture)                    |
//+------------------------------------------------------------------+
bool EnterTrade()
{
   double dir, entry, sl, tp;
   if(!CheckLondonBreakout(dir, entry, sl, tp)) return false;

   double stop_pips = MathAbs(entry - sl) / g_pip_size;
   if(stop_pips <= 0) return false;

   double lots = CalculateLots(stop_pips);
   if(lots <= 0) return false;

   // Capture pre-entry context for CSV
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double pre_spread_pips = (ask - bid) / g_pip_size;
   double pre_equity = AccountInfoDouble(ACCOUNT_EQUITY);

   bool ok = (dir > 0) ? trade.Buy(lots, _Symbol, 0, sl, tp, "LB-Long")
                       : trade.Sell(lots, _Symbol, 0, sl, tp, "LB-Short");

   if(ok)
   {
      g_state = STATE_TRADE_ACTIVE;
      g_direction = (int)dir;
      g_entry_price = entry;        // expected (range boundary)
      g_stop_loss = sl;
      g_take_profit = tp;
      g_trade_taken_today = true;

      // v2: Capture trade context
      g_active_ticket = trade.ResultOrder();
      g_actual_entry  = trade.ResultPrice(); // actual fill price
      if(g_actual_entry <= 0) g_actual_entry = (dir > 0) ? ask : bid;
      g_entry_spread  = pre_spread_pips;
      g_entry_equity  = pre_equity;
      g_entry_lots    = lots;
      g_entry_time    = GetGMTNow();
      g_planned_exit_reason = "";

      LogStateTransition(STATE_RANGE_SET, STATE_TRADE_ACTIVE, "London breakout entry");
      LogInfo(StringFormat("ENTERED %s | lots=%.2f | expected=%.5f | actual=%.5f | spread=%.2f | SL=%.5f | TP=%.5f",
              dir > 0 ? "LONG" : "SHORT", lots, entry, g_actual_entry, pre_spread_pips, sl, tp));
      SaveStateToGlobals();
      return true;
   }
   else
   {
      LogCritical(StringFormat("Order failed: %d - %s",
                  trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      return false;
   }
}

//+------------------------------------------------------------------+
//| TRADE CLOSE DETECTION + CSV LOG (v2 NEW)                         |
//+------------------------------------------------------------------+
string DetermineExitReason(double exit_price)
{
   if(g_planned_exit_reason != "") return g_planned_exit_reason;

   double tol = g_pip_size * 2.0; // 2-pip tolerance
   if(g_direction > 0)
   {
      if(MathAbs(exit_price - g_take_profit) < tol) return "TP";
      if(MathAbs(exit_price - g_stop_loss)   < tol) return "SL";
   }
   else
   {
      if(MathAbs(exit_price - g_take_profit) < tol) return "TP";
      if(MathAbs(exit_price - g_stop_loss)   < tol) return "SL";
   }
   int hour = GetGMTHour(GetGMTNow());
   if(hour >= InpEodExitHour) return "EOD";
   return "Other";
}

void HandleTradeClose()
{
   // Find the just-closed deal in history for this magic
   if(!HistorySelect(g_entry_time, TimeCurrent() + 60)) return;

   ulong last_close_deal = 0;
   double last_exit_price = 0;
   datetime last_exit_time = 0;

   int total = HistoryDealsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong deal_ticket = HistoryDealGetTicket(i);
      if(deal_ticket == 0) continue;
      if(HistoryDealGetInteger(deal_ticket, DEAL_MAGIC) != InpMagicNumber) continue;
      if(HistoryDealGetString(deal_ticket, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(deal_ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
      // matches our position? (positionId == g_active_ticket order or related)
      last_close_deal = deal_ticket;
      last_exit_price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
      last_exit_time  = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
      break;
   }

   if(last_close_deal == 0)
   {
      LogInfo("Trade close detected but exit deal not yet in history; will retry");
      return;
   }

   double exit_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   string reason = DetermineExitReason(last_exit_price);
   WriteTradeToCSV(last_exit_time, last_exit_price, exit_equity, reason);

   // Clear context
   g_active_ticket = 0;
   g_actual_entry = 0;
   g_entry_spread = 0;
   g_entry_equity = 0;
   g_entry_lots = 0;
   g_entry_time = 0;
   g_planned_exit_reason = "";
}

//+------------------------------------------------------------------+
//| MAIN TICK HANDLER                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime gmt = GetGMTNow();
   datetime day_start = GetGMTDayStart(gmt);
   int hour = GetGMTHour(gmt);

   if(g_current_day_gmt != day_start) DailyReset(day_start);

   // Position closure detection
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
            { pos_open = true; break; }
         }
      }
      if(!pos_open)
      {
         // v2: Log the closed trade to CSV
         HandleTradeClose();

         LogStateTransition(STATE_TRADE_ACTIVE, STATE_WAITING, "Position closed");
         g_state = STATE_WAITING;
         g_direction = 0;
         g_entry_price = 0; g_stop_loss = 0; g_take_profit = 0;
         SaveStateToGlobals();
      }
   }

   if(!CheckSafetyLayers()) return;
   if(!g_trading_allowed) return;

   // STATE_WAITING -> STATE_RANGE_SET
   if(g_state == STATE_WAITING && hour >= InpAsianEndHour && hour < InpLondonEndHour && !g_trade_taken_today)
   {
      double hi, lo, range_pips;
      if(ComputeAsianRange(day_start, hi, lo, range_pips))
      {
         if(range_pips < InpMinRangePips)
         {
            LogInfo(StringFormat("Range %.1f < min %.1f, skip", range_pips, InpMinRangePips));
            g_trade_taken_today = true;
         }
         else if(range_pips > InpMaxRangePips)
         {
            LogInfo(StringFormat("Range %.1f > max %.1f, skip", range_pips, InpMaxRangePips));
            g_trade_taken_today = true;
         }
         else
         {
            g_asian_high = hi; g_asian_low = lo; g_range_pips = range_pips;
            g_state = STATE_RANGE_SET;
            LogStateTransition(STATE_WAITING, STATE_RANGE_SET,
                               StringFormat("Asian range valid: %.1f pips", range_pips));
            SaveStateToGlobals();
         }
      }
   }

   // STATE_RANGE_SET -> STATE_TRADE_ACTIVE
   if(g_state == STATE_RANGE_SET && !g_trade_taken_today)
   {
      static datetime last_checked_bar = 0;
      datetime current_h1_bar_time = iTime(_Symbol, PERIOD_H1, 0);
      if(current_h1_bar_time != last_checked_bar && hour < InpLondonEndHour)
      {
         last_checked_bar = current_h1_bar_time;
         EnterTrade();
      }
      if(hour >= InpLondonEndHour)
      {
         LogStateTransition(STATE_RANGE_SET, STATE_WAITING, "London window expired");
         g_state = STATE_WAITING;
         g_trade_taken_today = true;
         SaveStateToGlobals();
      }
   }

   // EOD exit
   if(g_state == STATE_TRADE_ACTIVE && hour >= InpEodExitHour)
   {
      LogInfo(StringFormat("EOD exit at hour %d GMT", hour));
      g_planned_exit_reason = "EOD";
      CloseAllPositions("EOD exit (17:00 GMT)");
   }
}

//+------------------------------------------------------------------+
