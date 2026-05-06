//+------------------------------------------------------------------+
//|                                                   XAUUSD_v1.mq5  |
//|     XAUUSD Daily ATR Breakout EA — Aqua Trader $50k              |
//|                                                                  |
//|  Strategy (Phase 4 OOS validated robust config):                 |
//|    ATR multiplier   = 1.0 (D1 ATR-14)                            |
//|    Trend filter     = W1 EMA-26 (long if W1 close > EMA)         |
//|    SL = 1.5 * ATR(14), TP = 3.0 * ATR(14)                        |
//|    Max hold         = 5 trading days                             |
//|    BE move          = +0.2R after +1.0R reached                  |
//|    Trail            = 1*ATR after +2.0R reached                  |
//|    One trade per day, bar-close market orders only.              |
//|                                                                  |
//|  OOS stats (2021-2025): PF 1.50, WR 50%, AvgR 0.177, N=34.       |
//|                                                                  |
//|  Risk architecture (Aqua Trader $50k, 5%/10% DD):                |
//|    Risk per trade   = 1.0% flat                                  |
//|    Daily loss halt  = 4.0% (1% buffer below 5% Aqua hard rule)   |
//|    Total DD halt    = 9.0% absolute (1% buffer below 10% rule)   |
//|                                                                  |
//|  v7 LESSONS APPLIED:                                             |
//|    - Bar-close market orders (IsNewBar pattern), no pending stops|
//|    - Absolute DD cap from starting equity (not trailing)         |
//|    - No EMA ambiguity zone — direct > / < comparison             |
//|    - Parity trace logger writes identical schema to Python       |
//+------------------------------------------------------------------+
#property copyright "XAUUSD Daily ATR Breakout v1"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== Strategy (LOCKED, Phase 4 OOS) ==="
input double InpAtrMultiplier   = 1.0;
input double InpSlAtrMult       = 1.5;
input double InpTpAtrMult       = 3.0;
input int    InpAtrPeriod       = 14;
input int    InpW1EmaPeriod     = 26;
input int    InpMaxHoldDays     = 5;
input double InpBeTriggerR      = 1.0;
input double InpBeOffsetR       = 0.2;
input double InpTrailTriggerR   = 2.0;
input double InpTrailAtrMult    = 1.0;

input group "=== Risk (Aqua Trader $50k) ==="
input double InpRiskPercent       = 1.0;
input double InpDailyLossLimitPct = 4.0;
input double InpTotalDDLimitPct   = 9.0;

input group "=== Safety & Operations ==="
input bool   InpTradingEnabled  = true;
input int    InpMagicNumber     = 778900;
input bool   InpVerboseLogging  = true;

input group "=== Parity Oracle Trace ==="
input bool   InpTraceLogging    = true;
input string InpTraceFileName   = "decision_trace_mql5_XAUUSD_2024.csv";

input group "=== CSV Trade Logger ==="
input bool   InpCSVLogging      = true;
input string InpCSVFileName     = "XAUUSD_v1_trades.csv";

//+------------------------------------------------------------------+
//| GLOBAL STATE                                                     |
//+------------------------------------------------------------------+
double       g_starting_equity   = 0.0;
datetime     g_last_bar_time     = 0;
datetime     g_last_day_reset    = 0;
double       g_day_start_equity  = 0.0;
bool         g_trade_taken_today = false;
bool         g_halted_total      = false;
bool         g_halted_today      = false;

int          g_atr_handle        = INVALID_HANDLE;
int          g_w1_ema_handle     = INVALID_HANDLE;

int          g_trace_handle      = INVALID_HANDLE;
int          g_csv_handle        = INVALID_HANDLE;

// Active trade context for trail/BE management
struct TradeCtx {
   ulong   ticket;
   int     direction;       // +1 long, -1 short
   double  entry;
   double  initial_sl;
   double  current_sl;
   double  tp;
   double  stop_dist;
   double  atr_at_entry;
   datetime open_time;
   bool    be_moved;
   bool    trail_active;
};
TradeCtx g_ctx;
bool     g_has_trade = false;

//+------------------------------------------------------------------+
//| INIT                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetMarginMode();
   trade.SetTypeFillingBySymbol(_Symbol);

   g_starting_equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   g_day_start_equity = g_starting_equity;
   g_last_day_reset   = iTime(_Symbol, PERIOD_D1, 0);

   g_atr_handle    = iATR(_Symbol, PERIOD_D1, InpAtrPeriod);
   g_w1_ema_handle = iMA(_Symbol, PERIOD_W1, InpW1EmaPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(g_atr_handle == INVALID_HANDLE || g_w1_ema_handle == INVALID_HANDLE) {
      Print("ERROR: failed to init indicator handles");
      return INIT_FAILED;
   }

   if(InpTraceLogging) OpenTraceFile();
   if(InpCSVLogging)   OpenCsvFile();

   PrintFormat("XAUUSD_v1 init: starting_equity=$%.2f  ATR_mult=%.2f  W1_EMA=%d  RiskPct=%.2f%%",
               g_starting_equity, InpAtrMultiplier, InpW1EmaPeriod, InpRiskPercent);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| DEINIT                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(g_atr_handle    != INVALID_HANDLE) IndicatorRelease(g_atr_handle);
   if(g_w1_ema_handle != INVALID_HANDLE) IndicatorRelease(g_w1_ema_handle);
   if(g_trace_handle  != INVALID_HANDLE) FileClose(g_trace_handle);
   if(g_csv_handle    != INVALID_HANDLE) FileClose(g_csv_handle);
}

//+------------------------------------------------------------------+
//| FILE LOGGERS                                                     |
//+------------------------------------------------------------------+
void OpenTraceFile()
{
   g_trace_handle = FileOpen(InpTraceFileName, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(g_trace_handle == INVALID_HANDLE) {
      PrintFormat("WARN: trace file open failed: %s", InpTraceFileName);
      return;
   }
   FileWrite(g_trace_handle,
      "date","bar_time_gmt","bar_close","bar_high","bar_low",
      "atr_d1","prev_high","prev_low","w1_close","w1_ema","trend_dir",
      "threshold","allow_long","allow_short","signal","skip_reason",
      "entry_price","sl","tp");
}

void OpenCsvFile()
{
   g_csv_handle = FileOpen(InpCSVFileName, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(g_csv_handle == INVALID_HANDLE) {
      PrintFormat("WARN: csv file open failed: %s", InpCSVFileName);
      return;
   }
   FileWrite(g_csv_handle,
      "open_time","close_time","direction","entry","exit","sl_initial","tp",
      "atr","stop_dist","pnl_R","pnl_money","equity_after","be_moved","trail_active","exit_reason");
}

void WriteTrace(datetime bar_time, double bar_close, double bar_high, double bar_low,
                double atr_d1, double prev_h, double prev_l,
                double w1_close, double w1_ema, string trend_dir,
                double threshold, bool allow_long, bool allow_short,
                string signal, string skip_reason,
                double entry_p, double sl_p, double tp_p)
{
   if(g_trace_handle == INVALID_HANDLE) return;
   string date_s = TimeToString(bar_time, TIME_DATE);
   string time_s = TimeToString(bar_time, TIME_DATE|TIME_MINUTES|TIME_SECONDS);
   FileWrite(g_trace_handle,
      date_s, time_s,
      DoubleToString(bar_close, 2), DoubleToString(bar_high, 2), DoubleToString(bar_low, 2),
      DoubleToString(atr_d1, 3), DoubleToString(prev_h, 2), DoubleToString(prev_l, 2),
      DoubleToString(w1_close, 2), DoubleToString(w1_ema, 3), trend_dir,
      DoubleToString(threshold, 3),
      allow_long ? "YES" : "NO",
      allow_short ? "YES" : "NO",
      signal, skip_reason,
      entry_p > 0 ? DoubleToString(entry_p, 2) : "",
      sl_p    > 0 ? DoubleToString(sl_p,    2) : "",
      tp_p    > 0 ? DoubleToString(tp_p,    2) : "");
}

//+------------------------------------------------------------------+
//| BAR-CLOSE GATE                                                   |
//+------------------------------------------------------------------+
bool IsNewH1Bar()
{
   datetime t = iTime(_Symbol, PERIOD_H1, 0);
   if(t == 0) return false;
   if(t != g_last_bar_time) {
      g_last_bar_time = t;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| RISK / DD GUARDS                                                 |
//+------------------------------------------------------------------+
void CheckDayReset()
{
   datetime today = iTime(_Symbol, PERIOD_D1, 0);
   if(today != g_last_day_reset) {
      g_last_day_reset    = today;
      g_day_start_equity  = AccountInfoDouble(ACCOUNT_EQUITY);
      g_trade_taken_today = false;
      g_halted_today      = false;
   }
}

bool DDGuardsOk()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);

   double total_dd_pct = 100.0 * (g_starting_equity - eq) / g_starting_equity;
   if(total_dd_pct >= InpTotalDDLimitPct) {
      if(!g_halted_total) {
         PrintFormat("HALTED-TOTAL: total DD %.2f%% >= %.2f%% — closing positions", total_dd_pct, InpTotalDDLimitPct);
         CloseAllPositions("TOTAL_DD_HALT");
      }
      g_halted_total = true;
      return false;
   }

   double daily_dd_pct = 100.0 * (g_day_start_equity - eq) / g_day_start_equity;
   if(daily_dd_pct >= InpDailyLossLimitPct) {
      if(!g_halted_today) {
         PrintFormat("HALTED-DAY: daily DD %.2f%% >= %.2f%% — closing positions", daily_dd_pct, InpDailyLossLimitPct);
         CloseAllPositions("DAILY_DD_HALT");
      }
      g_halted_today = true;
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| LOT SIZING                                                       |
//+------------------------------------------------------------------+
double CalcLot(double entry, double sl)
{
   double risk_money = AccountInfoDouble(ACCOUNT_EQUITY) * (InpRiskPercent / 100.0);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tick_size <= 0 || tick_value <= 0) return 0.0;

   double dist_price = MathAbs(entry - sl);
   double dist_ticks = dist_price / tick_size;
   double loss_per_lot = dist_ticks * tick_value;
   if(loss_per_lot <= 0) return 0.0;

   double lot = risk_money / loss_per_lot;

   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double lot_min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double lot_max  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   lot = MathFloor(lot / lot_step) * lot_step;
   if(lot < lot_min) lot = lot_min;
   if(lot > lot_max) lot = lot_max;
   return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------+
//| CLOSE ALL POSITIONS                                              |
//+------------------------------------------------------------------+
void CloseAllPositions(string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      trade.PositionClose(ticket);
   }
   g_has_trade = false;
}

//+------------------------------------------------------------------+
//| ENTRY LOGIC (per H1 bar close)                                   |
//+------------------------------------------------------------------+
void EvaluateEntry(datetime bar_time)
{
   double atr_buf[1], ema_buf[1];
   if(CopyBuffer(g_atr_handle, 0, 1, 1, atr_buf) <= 0) return;     // shift=1 -> previous D1 ATR
   if(CopyBuffer(g_w1_ema_handle, 0, 0, 1, ema_buf) <= 0) return;
   double atr    = atr_buf[0];
   double w1_ema = ema_buf[0];
   if(atr <= 0) return;

   double prev_high = iHigh(_Symbol, PERIOD_D1, 1);
   double prev_low  = iLow(_Symbol,  PERIOD_D1, 1);
   double w1_close  = iClose(_Symbol, PERIOD_W1, 0);

   double bar_close = iClose(_Symbol, PERIOD_H1, 1);
   double bar_high  = iHigh(_Symbol,  PERIOD_H1, 1);
   double bar_low   = iLow(_Symbol,   PERIOD_H1, 1);

   string trend_dir = "FLAT";
   bool allow_long  = false;
   bool allow_short = false;
   if(w1_close > w1_ema)      { trend_dir = "LONG";  allow_long = true; }
   else if(w1_close < w1_ema) { trend_dir = "SHORT"; allow_short = true; }

   double threshold   = InpAtrMultiplier * atr;
   string signal      = "NONE";
   string skip_reason = "";
   double entry_p     = 0, sl_p = 0, tp_p = 0;

   bool can_open = !g_has_trade && !g_trade_taken_today && !g_halted_today && !g_halted_total;

   if(g_trade_taken_today)        skip_reason = "TRADE_ALREADY_TAKEN";
   else if(g_has_trade)           skip_reason = "TRADE_ACTIVE";
   else if(!(allow_long || allow_short)) skip_reason = "TREND_FILTER";
   else if(allow_long  && bar_close > prev_high + threshold) {
      signal  = "LONG";
      entry_p = bar_close;
      sl_p    = bar_close - InpSlAtrMult * atr;
      tp_p    = bar_close + InpTpAtrMult * atr;
   }
   else if(allow_short && bar_close < prev_low - threshold) {
      signal  = "SHORT";
      entry_p = bar_close;
      sl_p    = bar_close + InpSlAtrMult * atr;
      tp_p    = bar_close - InpTpAtrMult * atr;
   }
   else                           skip_reason = "NO_BREAKOUT";

   datetime trace_ts = iTime(_Symbol, PERIOD_H1, 1);
   if(InpTraceLogging) {
      WriteTrace(trace_ts, bar_close, bar_high, bar_low,
                 atr, prev_high, prev_low, w1_close, w1_ema, trend_dir,
                 threshold, allow_long, allow_short,
                 signal, skip_reason, entry_p, sl_p, tp_p);
   }

   if(!can_open || signal == "NONE" || !InpTradingEnabled) return;

   double lot = CalcLot(entry_p, sl_p);
   if(lot <= 0) return;

   bool ok = false;
   double current_price = (signal == "LONG")
                           ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                           : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(signal == "LONG")
      ok = trade.Buy(lot, _Symbol, 0.0, sl_p, tp_p, "XAUUSD_v1");
   else
      ok = trade.Sell(lot, _Symbol, 0.0, sl_p, tp_p, "XAUUSD_v1");

   if(!ok) {
      PrintFormat("ENTRY FAILED %s lot=%.2f sl=%.2f tp=%.2f err=%d",
                  signal, lot, sl_p, tp_p, trade.ResultRetcode());
      return;
   }

   g_ctx.ticket       = trade.ResultDeal();
   g_ctx.direction    = (signal == "LONG") ? 1 : -1;
   g_ctx.entry        = current_price;
   g_ctx.initial_sl   = sl_p;
   g_ctx.current_sl   = sl_p;
   g_ctx.tp           = tp_p;
   g_ctx.stop_dist    = MathAbs(current_price - sl_p);
   g_ctx.atr_at_entry = atr;
   g_ctx.open_time    = TimeCurrent();
   g_ctx.be_moved     = false;
   g_ctx.trail_active = false;
   g_has_trade        = true;
   g_trade_taken_today = true;

   if(InpVerboseLogging)
      PrintFormat("ENTRY %s lot=%.2f entry=%.2f sl=%.2f tp=%.2f atr=%.2f",
                  signal, lot, current_price, sl_p, tp_p, atr);
}

//+------------------------------------------------------------------+
//| MANAGE OPEN TRADE (BE move, trail, max hold)                     |
//+------------------------------------------------------------------+
void ManageOpenTrade()
{
   if(!g_has_trade) return;

   if(!PositionSelectByTicket(g_ctx.ticket)) {
      // Position closed externally (SL/TP hit or manual)
      g_has_trade = false;
      return;
   }

   double cur_price = (g_ctx.direction == 1)
                       ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                       : SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   double profit_R = g_ctx.direction * (cur_price - g_ctx.entry) / g_ctx.stop_dist;

   // BE move at +1.0R
   if(!g_ctx.be_moved && profit_R >= InpBeTriggerR) {
      double new_sl = (g_ctx.direction == 1)
                       ? g_ctx.entry + InpBeOffsetR * g_ctx.stop_dist
                       : g_ctx.entry - InpBeOffsetR * g_ctx.stop_dist;
      if((g_ctx.direction == 1 && new_sl > g_ctx.current_sl) ||
         (g_ctx.direction == -1 && new_sl < g_ctx.current_sl)) {
         if(trade.PositionModify(g_ctx.ticket, new_sl, g_ctx.tp)) {
            g_ctx.current_sl = new_sl;
            g_ctx.be_moved   = true;
            if(InpVerboseLogging) PrintFormat("BE MOVE: sl -> %.2f at +%.2fR", new_sl, profit_R);
         }
      }
   }

   // Trail at +2.0R by 1*ATR
   if(profit_R >= InpTrailTriggerR) {
      double trail_offset = InpTrailAtrMult * g_ctx.atr_at_entry;
      double new_sl = (g_ctx.direction == 1)
                       ? cur_price - trail_offset
                       : cur_price + trail_offset;
      if((g_ctx.direction == 1 && new_sl > g_ctx.current_sl) ||
         (g_ctx.direction == -1 && new_sl < g_ctx.current_sl)) {
         if(trade.PositionModify(g_ctx.ticket, new_sl, g_ctx.tp)) {
            g_ctx.current_sl   = new_sl;
            g_ctx.trail_active = true;
         }
      }
   }

   // Max hold cutoff
   int held_days = (int)((TimeCurrent() - g_ctx.open_time) / 86400);
   if(held_days >= InpMaxHoldDays) {
      if(InpVerboseLogging) PrintFormat("MAX_HOLD: closing after %d days", held_days);
      trade.PositionClose(g_ctx.ticket);
      g_has_trade = false;
   }
}

//+------------------------------------------------------------------+
//| ON TICK                                                          |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewH1Bar()) {
      if(g_has_trade) ManageOpenTrade();
      return;
   }

   CheckDayReset();
   if(!DDGuardsOk()) return;

   ManageOpenTrade();
   if(!g_has_trade) EvaluateEntry(iTime(_Symbol, PERIOD_H1, 0));
}
//+------------------------------------------------------------------+
