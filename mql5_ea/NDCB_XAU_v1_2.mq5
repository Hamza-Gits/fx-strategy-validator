//+------------------------------------------------------------------+
//|                                            NDCB_XAU_v1_2.mq5     |
//|     XAUUSD H1 — NY Data-Hour Compression Breakout (NDCB) v1.2    |
//|                                                                  |
//|  v1.2 BUG FIX (over v1.1 — Backtest 4 disaster):                 |
//|  v1.1 had a DRAWDOWN TRAP. EA hit 8.01% abs DD on 2017.11.10,    |
//|  entered 30-day recovery, "completed" recovery — but immediately |
//|  re-entered because g_starting_equity stayed at original value   |
//|  while equity hadn't recovered. Loop trapped EA in recovery for  |
//|  the remaining 7 years. Result: 42 trades (all 2017), -$4,005.   |
//|                                                                  |
//|  v1.2 FIX: When recovery completes, reset g_starting_equity =    |
//|  current equity. This rebases the absolute-DD baseline so the    |
//|  EA can resume trading instead of looping. Same DD semantics,    |
//|  trap removed.                                                   |
//|                                                                  |
//|  v1.1 FIX (kept — Backtest 3 root cause):                        |
//|  v1.0 used TimeCurrent() hour to gate entry into the comp        |
//|  evaluation. In M1-OHLC tick generation mode (which the tester   |
//|  silently fell back to because tick history starts 2026.02.03),  |
//|  the first OnTick() call most days arrived AFTER the entry       |
//|  window closed. The state machine immediately jumped to          |
//|  COMP_DONE on tick #1 and skipped the day. Result: 1862 of 2085  |
//|  days (89%) never reached the filter. Only 42 trades fired       |
//|  over 2017-2025; PF=0.65; -$4,005 loss.                          |
//|                                                                  |
//|  v1.1 FIX: Trigger comp evaluation on the H1 NEW BAR whose       |
//|  broker hour equals InpEntryStartHour (15:00). This is the bar   |
//|  that opens immediately after the comp window closes. Identical  |
//|  to London Breakout v7's bar-close pattern. Tick-frequency       |
//|  independent — works in real-tick, M1-OHLC, and live modes.      |
//|                                                                  |
//|  Validated edge (8/8 years 2017-2024 PASS at CR=0.70):           |
//|     IS=4yr OOS=1yr walk-forward, PF range 1.03-1.71, N=34-52/yr  |
//|     2024 OOS PF = 1.559 (ARCB-XAU failed here at 0.481)          |
//|                                                                  |
//|  Mechanism (Hammoudeh et al. 2024; CME microstructure):          |
//|     The 13:30 UTC bar (broker 15:00 GMT+2) is the highest-sigma  |
//|     timestamp of the trading week (NFP, CPI, claims, FOMC).      |
//|     When pre-NY window (broker 11:00-14:00) compresses below its |
//|     20-day rolling median, the 13:30 UTC release produces an     |
//|     explosive directional expansion as pre-positioned stops and  |
//|     option hedges unwind.                                        |
//|                                                                  |
//|  Why NDCB works where ARCB-XAU failed:                           |
//|   1. SELF-REFERENTIAL compression filter (range vs its own       |
//|      rolling median) — ARCB compared 8h Asian range to 24h ATR   |
//|      which fired trivially on 92% of days.                       |
//|   2. PENDING STOP at boundary + 0.20×ATR buffer — captures move  |
//|      only after break is confirmed (matches Python intrabar      |
//|      fill model).                                                |
//|   3. 2R hard target — ARCB had 0.67R structural negative edge.   |
//|   4. Regime filter — skip already-explosive days (5d D1 range    |
//|      > 1.5x D1 ATR(63)).                                         |
//|                                                                  |
//|  Hours in BROKER time (assumes GMT+2/+3 broker, 13:30 UTC = 15): |
//|     comp window: 11:00-14:00 broker  → 09:00-12:00 UTC          |
//|     entry window: 15:00-18:00 broker → 13:00-16:00 UTC          |
//|     hard close: 21:00 broker         → 19:00 UTC                 |
//|                                                                  |
//|  v1 PARITY ORACLE WARNING:                                       |
//|  London Breakout v6 used pending stops and produced 212 phantom  |
//|  fills due to intrabar tick spikes (Report 20: net -$2,738).     |
//|  NDCB uses pending stops with 0.20×ATR buffer above noise floor, |
//|  but FORWARD-TEST FIRST. CSV log every fill and compare to       |
//|  Python signal log before live deployment.                       |
//+------------------------------------------------------------------+
#property copyright "NDCB-XAU EA v1.2 — recovery trap fix"
#property version   "1.20"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== Strategy (LOCKED — validated CR=0.70) ==="
input double InpCompressionRatio  = 0.70;   // comp_range <= ratio × rolling_median
input double InpEntryBufferATR    = 0.20;   // pending stop = boundary + buf×ATR(14)
input double InpStopATRMult       = 1.00;   // SL = entry - 1.0×ATR(14)
input double InpTPRMultiplier     = 2.00;   // TP = entry + 2R (R = stop distance)
input double InpTrailTriggerR     = 1.50;   // activate trail once 1.5R reached
input double InpTrailATRMult      = 1.00;   // trail = close - 1.0×ATR(14)
input int    InpATRPeriod         = 14;     // H1 ATR for stops
input int    InpVolRatioPeriod    = 20;     // rolling median lookback (days)

input group "=== Regime Filter ==="
input bool   InpUseRegimeFilter   = true;
input int    InpRegimeDays        = 5;      // lookback for avg D1 range
input double InpRegimeMult        = 1.50;   // skip if 5d range > mult × D1 ATR(63)
input int    InpRegimeATRPeriod   = 63;

input group "=== Session Hours (BROKER time, GMT+2/+3) ==="
input int    InpCompStartHour     = 11;
input int    InpCompEndHour       = 14;     // last bar = 14:00 (closes 15:00)
input int    InpEntryStartHour    = 15;     // place pending orders at 15:00
input int    InpEntryEndHour      = 18;     // cancel pending if not filled by 18:00
input int    InpHardCloseHour     = 21;     // close any open position at 21:00

input group "=== Risk ==="
input double InpRiskPercent       = 0.75;   // % equity per trade
input double InpDailyLossLimitPct = 3.0;
input double InpTrailingDDPct     = 8.0;    // halt at 8% drawdown from start

input group "=== Safety ==="
input bool   InpHardHalt          = false;  // true = permanent halt; false = recovery mode
input int    InpRecoveryDays      = 30;
input double InpRecoveryRiskPct   = 0.25;

input group "=== Operations ==="
input bool   InpTradingEnabled    = true;
input int    InpMagicNumber       = 778800;
input bool   InpVerboseLogging    = true;

input group "=== CSV Logging ==="
input bool   InpCSVLogging        = true;
input string InpCSVFileName       = "NDCB_XAU_v1_trades.csv";

//+------------------------------------------------------------------+
//| GLOBAL STATE                                                     |
//+------------------------------------------------------------------+
enum ENUM_NDCB_STATE
{
   NDCB_WAITING       = 0,   // start of day, before comp window done
   NDCB_COMP_DONE     = 1,   // comp window closed, filter checked
   NDCB_PENDING       = 2,   // BuyStop and SellStop placed
   NDCB_TRADE_ACTIVE  = 3    // one pending fired, manage trade
};

string           gv_prefix       = "";
ENUM_NDCB_STATE  g_state         = NDCB_WAITING;
datetime         g_current_day   = 0;

double           g_comp_high     = 0;
double           g_comp_low      = 0;
double           g_h1_atr_at_comp= 0;        // ATR(14) value at 14:00 broker
double           g_pending_buy_p = 0;
double           g_pending_sell_p= 0;

ulong            g_buy_stop_ticket  = 0;
ulong            g_sell_stop_ticket = 0;

ulong            g_active_ticket = 0;
int              g_direction     = 0;        // +1 long, -1 short
double           g_entry_price   = 0;
double           g_stop_loss     = 0;
double           g_take_profit   = 0;
double           g_stop_dist     = 0;
double           g_entry_atr     = 0;        // ATR snapshot at entry for trail
bool             g_trail_active  = false;
datetime         g_entry_time    = 0;
double           g_entry_equity  = 0;
double           g_entry_lots    = 0;
double           g_entry_spread  = 0;

double           g_starting_equity = 0;
double           g_peak_equity     = 0;
double           g_start_day_eq    = 0;
bool             g_daily_halt      = false;
bool             g_permanent_halt  = false;
datetime         g_recovery_start  = 0;

int              g_atr_h1_handle   = INVALID_HANDLE;
int              g_atr_d1_handle   = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| TIME HELPERS                                                     |
//+------------------------------------------------------------------+
int GetBrokerHour(datetime t)
{
   MqlDateTime dt; TimeToStruct(t, dt);
   return dt.hour;
}

datetime GetBrokerDayStart(datetime t)
{
   MqlDateTime dt; TimeToStruct(t, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
}

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
   PrintFormat("[%s] %s", TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS), msg);
}

void LogCritical(string msg)
{
   PrintFormat("[%s] *** CRITICAL: %s ***", TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS), msg);
}

string StateName(ENUM_NDCB_STATE s)
{
   if(s == NDCB_WAITING)      return "WAITING";
   if(s == NDCB_COMP_DONE)    return "COMP_DONE";
   if(s == NDCB_PENDING)      return "PENDING";
   if(s == NDCB_TRADE_ACTIVE) return "TRADE_ACTIVE";
   return "UNKNOWN";
}

//+------------------------------------------------------------------+
//| CSV LOGGER                                                       |
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
         "entry_time","exit_time","symbol","direction",
         "entry_price","exit_price","stop_loss","take_profit",
         "entry_atr","stop_dist","comp_high","comp_low",
         "lots","entry_equity","exit_equity","pnl_dollars","pnl_R",
         "exit_reason","duration_min","entry_spread");
      FileClose(fh);
   }
}

void WriteTradeToCSV(datetime exit_time, double exit_price, double exit_equity, string reason)
{
   if(!InpCSVLogging) return;
   int fh = FileOpen(InpCSVFileName, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(fh == INVALID_HANDLE) return;
   FileSeek(fh, 0, SEEK_END);

   double pnl_dollars = exit_equity - g_entry_equity;
   double pnl_r       = (g_stop_dist > 0) ? (g_direction * (exit_price - g_entry_price) / g_stop_dist) : 0;
   int    dur_min     = (int)((exit_time - g_entry_time) / 60);

   FileWrite(fh,
      TimeToString(g_entry_time, TIME_DATE|TIME_SECONDS),
      TimeToString(exit_time,    TIME_DATE|TIME_SECONDS),
      _Symbol, g_direction > 0 ? "LONG" : "SHORT",
      DoubleToString(g_entry_price, _Digits),
      DoubleToString(exit_price,    _Digits),
      DoubleToString(g_stop_loss,   _Digits),
      DoubleToString(g_take_profit, _Digits),
      DoubleToString(g_entry_atr,   _Digits),
      DoubleToString(g_stop_dist,   _Digits),
      DoubleToString(g_comp_high,   _Digits),
      DoubleToString(g_comp_low,    _Digits),
      DoubleToString(g_entry_lots,  2),
      DoubleToString(g_entry_equity, 2),
      DoubleToString(exit_equity,    2),
      DoubleToString(pnl_dollars,    2),
      DoubleToString(pnl_r,          3),
      reason,
      IntegerToString(dur_min),
      DoubleToString(g_entry_spread, 2));
   FileClose(fh);
}

//+------------------------------------------------------------------+
//| GLOBAL VARIABLE PERSISTENCE                                      |
//+------------------------------------------------------------------+
void SaveState()
{
   GlobalVariableSet(gv_prefix+"State",          (double)g_state);
   GlobalVariableSet(gv_prefix+"CurrentDay",     (double)g_current_day);
   GlobalVariableSet(gv_prefix+"CompHigh",       g_comp_high);
   GlobalVariableSet(gv_prefix+"CompLow",        g_comp_low);
   GlobalVariableSet(gv_prefix+"H1ATRComp",      g_h1_atr_at_comp);
   GlobalVariableSet(gv_prefix+"BuyStopT",       (double)g_buy_stop_ticket);
   GlobalVariableSet(gv_prefix+"SellStopT",      (double)g_sell_stop_ticket);
   GlobalVariableSet(gv_prefix+"ActiveTicket",   (double)g_active_ticket);
   GlobalVariableSet(gv_prefix+"Direction",      (double)g_direction);
   GlobalVariableSet(gv_prefix+"EntryPrice",     g_entry_price);
   GlobalVariableSet(gv_prefix+"StopLoss",       g_stop_loss);
   GlobalVariableSet(gv_prefix+"TakeProfit",     g_take_profit);
   GlobalVariableSet(gv_prefix+"StopDist",       g_stop_dist);
   GlobalVariableSet(gv_prefix+"EntryATR",       g_entry_atr);
   GlobalVariableSet(gv_prefix+"TrailActive",    g_trail_active ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"EntryTime",      (double)g_entry_time);
   GlobalVariableSet(gv_prefix+"EntryEquity",    g_entry_equity);
   GlobalVariableSet(gv_prefix+"EntryLots",      g_entry_lots);
   GlobalVariableSet(gv_prefix+"StartingEquity", g_starting_equity);
   GlobalVariableSet(gv_prefix+"PeakEquity",     g_peak_equity);
   GlobalVariableSet(gv_prefix+"StartDayEq",     g_start_day_eq);
   GlobalVariableSet(gv_prefix+"DailyHalt",      g_daily_halt ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"PermanentHalt",  g_permanent_halt ? 1.0 : 0.0);
   GlobalVariableSet(gv_prefix+"RecoveryStart",  (double)g_recovery_start);
}

void LoadState()
{
   if(!GlobalVariableCheck(gv_prefix+"State"))
   {
      double eq            = AccountInfoDouble(ACCOUNT_EQUITY);
      g_state              = NDCB_WAITING;
      g_starting_equity    = eq;
      g_peak_equity        = eq;
      g_start_day_eq       = eq;
      LogInfo("Fresh state initialised");
      return;
   }
   g_state            = (ENUM_NDCB_STATE)(int)GlobalVariableGet(gv_prefix+"State");
   g_current_day      = (datetime)(long)GlobalVariableGet(gv_prefix+"CurrentDay");
   g_comp_high        = GlobalVariableGet(gv_prefix+"CompHigh");
   g_comp_low         = GlobalVariableGet(gv_prefix+"CompLow");
   g_h1_atr_at_comp   = GlobalVariableGet(gv_prefix+"H1ATRComp");
   g_buy_stop_ticket  = (ulong)GlobalVariableGet(gv_prefix+"BuyStopT");
   g_sell_stop_ticket = (ulong)GlobalVariableGet(gv_prefix+"SellStopT");
   g_active_ticket    = (ulong)GlobalVariableGet(gv_prefix+"ActiveTicket");
   g_direction        = (int)GlobalVariableGet(gv_prefix+"Direction");
   g_entry_price      = GlobalVariableGet(gv_prefix+"EntryPrice");
   g_stop_loss        = GlobalVariableGet(gv_prefix+"StopLoss");
   g_take_profit      = GlobalVariableGet(gv_prefix+"TakeProfit");
   g_stop_dist        = GlobalVariableGet(gv_prefix+"StopDist");
   g_entry_atr        = GlobalVariableGet(gv_prefix+"EntryATR");
   g_trail_active     = GlobalVariableGet(gv_prefix+"TrailActive") > 0.5;
   g_entry_time       = (datetime)(long)GlobalVariableGet(gv_prefix+"EntryTime");
   g_entry_equity     = GlobalVariableGet(gv_prefix+"EntryEquity");
   g_entry_lots       = GlobalVariableGet(gv_prefix+"EntryLots");
   g_starting_equity  = GlobalVariableGet(gv_prefix+"StartingEquity");
   g_peak_equity      = GlobalVariableGet(gv_prefix+"PeakEquity");
   g_start_day_eq     = GlobalVariableGet(gv_prefix+"StartDayEq");
   g_daily_halt       = GlobalVariableGet(gv_prefix+"DailyHalt") > 0.5;
   g_permanent_halt   = GlobalVariableGet(gv_prefix+"PermanentHalt") > 0.5;
   g_recovery_start   = (datetime)(long)GlobalVariableGet(gv_prefix+"RecoveryStart");
   LogInfo(StringFormat("State restored: %s | start_eq=%.2f", StateName(g_state), g_starting_equity));
}

//+------------------------------------------------------------------+
//| INIT / DEINIT                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   gv_prefix = "NDCB12_" + _Symbol + "_";   // v1.2 prefix — avoid stale v1.0/v1.1 state
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(30);

   g_atr_h1_handle = iATR(_Symbol, PERIOD_H1, InpATRPeriod);
   g_atr_d1_handle = iATR(_Symbol, PERIOD_D1, InpRegimeATRPeriod);
   if(g_atr_h1_handle == INVALID_HANDLE || g_atr_d1_handle == INVALID_HANDLE)
   {
      LogCritical("Failed to create ATR indicator handles");
      return INIT_FAILED;
   }

   LoadState();
   EnsureCSVHeader();

   LogInfo(StringFormat("NDCB-XAU v1.2 Init | %s | State=%s | recovery-trap fix", _Symbol, StateName(g_state)));
   LogInfo(StringFormat("Strategy: CR=%.2f Buf=%.2f×ATR SM=%.2f×ATR TP=%.1fR Trail@%.1fR",
           InpCompressionRatio, InpEntryBufferATR, InpStopATRMult, InpTPRMultiplier, InpTrailTriggerR));
   LogInfo(StringFormat("Hours (broker): comp %02d:00-%02d:00, entry %02d:00-%02d:00, hard close %02d:00",
           InpCompStartHour, InpCompEndHour, InpEntryStartHour, InpEntryEndHour, InpHardCloseHour));
   LogInfo(StringFormat("Risk: %.2f%%/trade, daily DD %.1f%%, trailing DD %.1f%%",
           InpRiskPercent, InpDailyLossLimitPct, InpTrailingDDPct));
   if(!InpTradingEnabled) LogCritical("TradingEnabled=false — observe-only mode");

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_atr_h1_handle != INVALID_HANDLE) IndicatorRelease(g_atr_h1_handle);
   if(g_atr_d1_handle != INVALID_HANDLE) IndicatorRelease(g_atr_d1_handle);
   SaveState();
   LogInfo(StringFormat("Deinit (reason=%d), state saved", reason));
}

//+------------------------------------------------------------------+
//| INDICATOR HELPERS                                                |
//+------------------------------------------------------------------+
double GetH1ATR(int shift = 1)
{
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(g_atr_h1_handle, 0, shift, 1, buf) <= 0) return 0;
   return buf[0];
}

double GetD1ATR(int shift = 1)
{
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(g_atr_d1_handle, 0, shift, 1, buf) <= 0) return 0;
   return buf[0];
}

//+------------------------------------------------------------------+
//| Compute compression range from H1 bars within window             |
//| Returns true if window has all expected bars (n >= 3)            |
//+------------------------------------------------------------------+
bool GetCompRangeForDay(datetime day_start, double &out_high, double &out_low, int &out_n)
{
   datetime t_from = day_start + InpCompStartHour * 3600;
   datetime t_to   = day_start + (InpCompEndHour + 1) * 3600 - 1;  // include 14:00 bar

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, PERIOD_H1, t_from, t_to, rates);
   if(copied <= 0) { out_n = 0; return false; }

   double hi = -DBL_MAX, lo = DBL_MAX;
   int n = 0;
   for(int i = 0; i < copied; i++)
   {
      int h = GetBrokerHour(rates[i].time);
      if(h < InpCompStartHour || h > InpCompEndHour) continue;
      if(rates[i].high > hi) hi = rates[i].high;
      if(rates[i].low  < lo) lo = rates[i].low;
      n++;
   }
   out_high = hi;
   out_low  = lo;
   out_n    = n;
   return (n >= 3);
}

//+------------------------------------------------------------------+
//| Rolling median of compression range over last N trading days    |
//+------------------------------------------------------------------+
double GetCompRangeRollingMedian(datetime today_start)
{
   double values[];
   ArrayResize(values, InpVolRatioPeriod);
   int collected = 0;

   datetime d = today_start;
   int safety = 0;
   while(collected < InpVolRatioPeriod && safety < InpVolRatioPeriod * 3)
   {
      d -= 86400;
      safety++;

      MqlDateTime dt; TimeToStruct(d, dt);
      if(dt.day_of_week == 0 || dt.day_of_week == 6) continue;  // skip weekend

      double hi, lo;
      int n;
      if(!GetCompRangeForDay(d, hi, lo, n)) continue;
      values[collected] = hi - lo;
      collected++;
   }
   if(collected < 10) return 0;  // need min 10 samples

   ArrayResize(values, collected);
   ArraySort(values);
   if(collected % 2 == 1) return values[collected / 2];
   return 0.5 * (values[collected / 2 - 1] + values[collected / 2]);
}

//+------------------------------------------------------------------+
//| Regime filter: skip if last 5 D1 ranges avg > 1.5 × D1 ATR(63)  |
//+------------------------------------------------------------------+
bool IsExplosiveRegime()
{
   if(!InpUseRegimeFilter) return false;

   MqlRates d1[];
   ArraySetAsSeries(d1, true);
   int copied = CopyRates(_Symbol, PERIOD_D1, 1, InpRegimeDays, d1);
   if(copied < InpRegimeDays) return false;

   double sum_range = 0;
   for(int i = 0; i < InpRegimeDays; i++) sum_range += (d1[i].high - d1[i].low);
   double avg_range = sum_range / InpRegimeDays;

   double d1_atr = GetD1ATR(1);
   if(d1_atr <= 0) return false;

   bool explosive = (avg_range > InpRegimeMult * d1_atr);
   if(explosive)
      LogInfo(StringFormat("Regime: 5d avg D1 range %.2f > %.2f x D1_ATR(%d) %.2f — SKIP",
              avg_range, InpRegimeMult, InpRegimeATRPeriod, d1_atr));
   return explosive;
}

//+------------------------------------------------------------------+
//| RISK SIZING                                                      |
//+------------------------------------------------------------------+
double CalculateLots(double stop_dist_price)
{
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_pct = (g_recovery_start != 0) ? InpRecoveryRiskPct : InpRiskPercent;
   double risk_dollars = equity * (risk_pct / 100.0);

   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0) return 0;

   double loss_per_lot = (stop_dist_price / tick_size) * tick_value;
   if(loss_per_lot <= 0) return 0;

   double lots_raw = risk_dollars / loss_per_lot;
   double vol_min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vol_max  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vol_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vol_step <= 0) vol_step = 0.01;

   double lots = MathFloor(lots_raw / vol_step) * vol_step;
   if(lots < vol_min) lots = vol_min;
   if(lots > vol_max) lots = vol_max;

   LogInfo(StringFormat("Lots: equity=%.2f risk=%.2f%% ($%.2f) stop_dist=%.2f loss/lot=$%.2f → %.2f lots",
           equity, risk_pct, risk_dollars, stop_dist_price, loss_per_lot, lots));
   return lots;
}

//+------------------------------------------------------------------+
//| SAFETY LAYERS                                                    |
//+------------------------------------------------------------------+
void CloseAllPositionsAndPendings(string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      trade.PositionClose(t);
      LogCritical(StringFormat("Closed position #%I64u | %s", t, reason));
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(!OrderSelect(t)) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagicNumber) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      trade.OrderDelete(t);
      LogCritical(StringFormat("Cancelled pending #%I64u | %s", t, reason));
   }
}

bool CheckSafetyLayers()
{
   if(!InpTradingEnabled)
   {
      if(g_state >= NDCB_PENDING) CloseAllPositionsAndPendings("Manual kill");
      g_permanent_halt = true;
      return false;
   }
   if(g_permanent_halt) return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_peak_equity <= 0) g_peak_equity = equity;
   if(equity > g_peak_equity) g_peak_equity = equity;

   // Absolute DD from starting equity
   if(g_starting_equity > 0)
   {
      double abs_dd = (g_starting_equity - equity) / g_starting_equity;
      if(abs_dd > InpTrailingDDPct / 100.0)
      {
         if(InpHardHalt)
         {
            LogCritical(StringFormat("Absolute DD %.2f%% — PERMANENT halt", abs_dd * 100));
            CloseAllPositionsAndPendings("Absolute DD hard halt");
            g_permanent_halt = true;
            return false;
         }
         else if(g_recovery_start == 0)
         {
            g_recovery_start = TimeCurrent();
            CloseAllPositionsAndPendings("Absolute DD recovery start");
            LogCritical(StringFormat("RECOVERY MODE: DD %.2f%% — pause %d days", abs_dd * 100, InpRecoveryDays));
            return false;
         }
         else
         {
            long days_in = (long)((TimeCurrent() - g_recovery_start) / 86400);
            if(days_in < InpRecoveryDays) return false;
            // v1.2 fix: rebase BOTH starting and peak equity to current.
            // Without rebasing g_starting_equity, abs_dd from start is still
            // 8% on the next tick and recovery re-triggers immediately
            // (the "drawdown trap" that froze v1.1 for 7 years from 2017.11).
            double pre_rebase_start = g_starting_equity;
            g_starting_equity = equity;
            g_peak_equity     = equity;
            g_recovery_start  = 0;
            LogCritical(StringFormat("Recovery complete — rebasing baseline: start_eq %.2f -> %.2f, resuming",
                        pre_rebase_start, equity));
            SaveState();
         }
      }
   }

   // Daily loss limit
   if(g_start_day_eq <= 0) g_start_day_eq = equity;
   double daily_loss = (g_start_day_eq - equity) / g_start_day_eq;
   if(daily_loss > InpDailyLossLimitPct / 100.0)
   {
      LogCritical(StringFormat("Daily DD %.2f%% — halted today", daily_loss * 100));
      CloseAllPositionsAndPendings("Daily DD");
      g_daily_halt = true;
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| DAILY RESET (broker midnight)                                    |
//+------------------------------------------------------------------+
void DailyReset(datetime new_day)
{
   LogInfo("=== DAILY RESET (broker 00:00) ===");
   g_current_day    = new_day;
   g_state          = NDCB_WAITING;
   g_comp_high      = 0;
   g_comp_low       = 0;
   g_h1_atr_at_comp = 0;
   g_pending_buy_p  = 0;
   g_pending_sell_p = 0;
   g_buy_stop_ticket  = 0;
   g_sell_stop_ticket = 0;
   g_start_day_eq   = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_daily_halt) { g_daily_halt = false; LogInfo("Daily halt lifted"); }
   SaveState();
}

//+------------------------------------------------------------------+
//| Check compression filter & place pending stop orders             |
//+------------------------------------------------------------------+
void EvaluateCompressionAndPlaceOrders()
{
   datetime day_start = g_current_day;

   double hi, lo;
   int n;
   if(!GetCompRangeForDay(day_start, hi, lo, n))
   {
      LogInfo(StringFormat("Comp window: only %d bars (need >=3) — skip day", n));
      g_state = NDCB_COMP_DONE;
      SaveState();
      return;
   }

   double comp_range = hi - lo;
   double median = GetCompRangeRollingMedian(day_start);
   if(median <= 0)
   {
      LogInfo("Insufficient history for rolling median — skip day");
      g_state = NDCB_COMP_DONE;
      SaveState();
      return;
   }

   double ratio = comp_range / median;
   LogInfo(StringFormat("Comp: H=%.2f L=%.2f range=%.2f median=%.2f ratio=%.3f (need <%.2f)",
           hi, lo, comp_range, median, ratio, InpCompressionRatio));

   if(comp_range >= InpCompressionRatio * median)
   {
      LogInfo("Comp filter FAIL — not compressed enough");
      g_state = NDCB_COMP_DONE;
      SaveState();
      return;
   }

   if(IsExplosiveRegime())
   {
      g_state = NDCB_COMP_DONE;
      SaveState();
      return;
   }

   // ATR(14) at end of comp window
   double atr14 = GetH1ATR(1);
   if(atr14 <= 0)
   {
      LogInfo("ATR not available — skip day");
      g_state = NDCB_COMP_DONE;
      SaveState();
      return;
   }

   g_comp_high       = hi;
   g_comp_low        = lo;
   g_h1_atr_at_comp  = atr14;
   g_pending_buy_p   = NormalizeDouble(hi + InpEntryBufferATR * atr14, _Digits);
   g_pending_sell_p  = NormalizeDouble(lo - InpEntryBufferATR * atr14, _Digits);

   double sl_dist = InpStopATRMult * atr14;
   double lots    = CalculateLots(sl_dist);
   if(lots <= 0)
   {
      LogInfo("Lots = 0 — skip day");
      g_state = NDCB_COMP_DONE;
      SaveState();
      return;
   }

   // Pending order expiry = entry window end
   datetime expiry = day_start + (InpEntryEndHour + 1) * 3600;

   double buy_sl  = NormalizeDouble(g_pending_buy_p  - sl_dist, _Digits);
   double buy_tp  = NormalizeDouble(g_pending_buy_p  + InpTPRMultiplier * sl_dist, _Digits);
   double sell_sl = NormalizeDouble(g_pending_sell_p + sl_dist, _Digits);
   double sell_tp = NormalizeDouble(g_pending_sell_p - InpTPRMultiplier * sl_dist, _Digits);

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // BuyStop must be above current Ask (with stops level offset)
   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double point     = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double min_dist  = stops_level * point;

   if(g_pending_buy_p <= ask + min_dist)
   {
      LogInfo(StringFormat("BuyStop %.2f too close to ask %.2f (min dist %.2f) — skip long",
              g_pending_buy_p, ask, min_dist));
   }
   else
   {
      if(trade.BuyStop(lots, g_pending_buy_p, _Symbol, buy_sl, buy_tp,
                        ORDER_TIME_SPECIFIED, expiry, "NDCB-Long"))
      {
         g_buy_stop_ticket = trade.ResultOrder();
         LogInfo(StringFormat("BuyStop placed: ticket=%I64u @%.2f SL=%.2f TP=%.2f exp=%s",
                 g_buy_stop_ticket, g_pending_buy_p, buy_sl, buy_tp,
                 TimeToString(expiry, TIME_DATE|TIME_MINUTES)));
      }
      else
      {
         LogCritical(StringFormat("BuyStop failed: retcode=%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      }
   }

   if(g_pending_sell_p >= bid - min_dist)
   {
      LogInfo(StringFormat("SellStop %.2f too close to bid %.2f (min dist %.2f) — skip short",
              g_pending_sell_p, bid, min_dist));
   }
   else
   {
      if(trade.SellStop(lots, g_pending_sell_p, _Symbol, sell_sl, sell_tp,
                         ORDER_TIME_SPECIFIED, expiry, "NDCB-Short"))
      {
         g_sell_stop_ticket = trade.ResultOrder();
         LogInfo(StringFormat("SellStop placed: ticket=%I64u @%.2f SL=%.2f TP=%.2f exp=%s",
                 g_sell_stop_ticket, g_pending_sell_p, sell_sl, sell_tp,
                 TimeToString(expiry, TIME_DATE|TIME_MINUTES)));
      }
      else
      {
         LogCritical(StringFormat("SellStop failed: retcode=%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
      }
   }

   if(g_buy_stop_ticket > 0 || g_sell_stop_ticket > 0)
   {
      g_state = NDCB_PENDING;
      SaveState();
   }
   else
   {
      LogInfo("No pending orders placed — day done");
      g_state = NDCB_COMP_DONE;
      SaveState();
   }
}

//+------------------------------------------------------------------+
//| Detect pending fill, capture entry, cancel opposite              |
//+------------------------------------------------------------------+
void CheckPendingFill()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      // Found the filled position — capture state
      g_active_ticket = t;
      g_direction     = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      g_entry_price   = PositionGetDouble(POSITION_PRICE_OPEN);
      g_stop_loss     = PositionGetDouble(POSITION_SL);
      g_take_profit   = PositionGetDouble(POSITION_TP);
      g_stop_dist     = MathAbs(g_entry_price - g_stop_loss);
      g_entry_atr     = g_h1_atr_at_comp;
      g_trail_active  = false;
      g_entry_time    = (datetime)PositionGetInteger(POSITION_TIME);
      g_entry_lots    = PositionGetDouble(POSITION_VOLUME);
      g_entry_equity  = AccountInfoDouble(ACCOUNT_EQUITY);
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      g_entry_spread  = (ask - bid);

      LogInfo(StringFormat("PENDING FILLED: %s @%.2f SL=%.2f TP=%.2f stop_dist=%.2f",
              g_direction > 0 ? "LONG" : "SHORT", g_entry_price, g_stop_loss, g_take_profit, g_stop_dist));

      // Cancel the opposite pending
      ulong opposite = (g_direction > 0) ? g_sell_stop_ticket : g_buy_stop_ticket;
      if(opposite > 0)
      {
         if(OrderSelect(opposite))
         {
            trade.OrderDelete(opposite);
            LogInfo(StringFormat("Cancelled opposite pending #%I64u", opposite));
         }
      }
      g_buy_stop_ticket  = 0;
      g_sell_stop_ticket = 0;
      g_state = NDCB_TRADE_ACTIVE;
      SaveState();
      return;
   }
}

//+------------------------------------------------------------------+
//| Manage active trade: trail logic, hard close                     |
//+------------------------------------------------------------------+
void ManageActiveTrade()
{
   // Position still open?
   if(!PositionSelectByTicket(g_active_ticket))
   {
      // Position closed — log it
      double exit_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double exit_price  = 0;
      datetime exit_time = TimeCurrent();
      string reason = "Other";

      // Try to find the closing deal
      if(HistorySelect(g_entry_time, TimeCurrent() + 60))
      {
         int total = HistoryDealsTotal();
         for(int i = total - 1; i >= 0; i--)
         {
            ulong d = HistoryDealGetTicket(i);
            if(d == 0) continue;
            if(HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagicNumber) continue;
            if(HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol) continue;
            if(HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
            exit_price = HistoryDealGetDouble(d, DEAL_PRICE);
            exit_time  = (datetime)HistoryDealGetInteger(d, DEAL_TIME);

            double tol = _Point * 30;
            if(MathAbs(exit_price - g_take_profit) < tol) reason = "TP";
            else if(MathAbs(exit_price - g_stop_loss) < tol) reason = "SL";
            else if(GetBrokerHour(exit_time) >= InpHardCloseHour) reason = "HARD_CLOSE";
            else if(g_trail_active) reason = "TRAIL";
            break;
         }
      }
      WriteTradeToCSV(exit_time, exit_price, exit_equity, reason);
      LogInfo(StringFormat("Trade closed: %s @%.2f pnl=$%.2f",
              reason, exit_price, exit_equity - g_entry_equity));

      g_active_ticket = 0;
      g_state         = NDCB_COMP_DONE;
      g_trail_active  = false;
      SaveState();
      return;
   }

   // Hard close at HardCloseHour broker
   int cur_hour = GetBrokerHour(TimeCurrent());
   if(cur_hour >= InpHardCloseHour)
   {
      LogInfo(StringFormat("Hard close at hour %d", cur_hour));
      trade.PositionClose(g_active_ticket);
      return;
   }

   // Trail update on each new bar
   if(!IsNewBar()) return;

   double atr14 = GetH1ATR(1);
   if(atr14 <= 0) return;

   // Get last completed bar close
   MqlRates bars[];
   ArraySetAsSeries(bars, true);
   if(CopyRates(_Symbol, PERIOD_H1, 1, 1, bars) < 1) return;
   double last_close = bars[0].close;

   // Activate trail if 1.5R hit (intrabar)
   if(!g_trail_active)
   {
      double trigger_price = (g_direction > 0)
         ? (g_entry_price + InpTrailTriggerR * g_stop_dist)
         : (g_entry_price - InpTrailTriggerR * g_stop_dist);

      if((g_direction > 0 && bars[0].high >= trigger_price) ||
         (g_direction < 0 && bars[0].low  <= trigger_price))
      {
         g_trail_active = true;
         LogInfo(StringFormat("Trail activated (1.5R hit) trigger=%.2f", trigger_price));
      }
   }

   if(g_trail_active)
   {
      double new_stop = (g_direction > 0)
         ? NormalizeDouble(last_close - InpTrailATRMult * atr14, _Digits)
         : NormalizeDouble(last_close + InpTrailATRMult * atr14, _Digits);

      bool should_update = (g_direction > 0 && new_stop > g_stop_loss) ||
                           (g_direction < 0 && new_stop < g_stop_loss);

      if(should_update)
      {
         if(trade.PositionModify(g_active_ticket, new_stop, g_take_profit))
         {
            LogInfo(StringFormat("Trail updated: SL %.2f → %.2f", g_stop_loss, new_stop));
            g_stop_loss = new_stop;
            SaveState();
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Cancel pending orders. Called when new H1 bar opens at or past   |
//| InpEntryEndHour (caller already checked the time gate).          |
//+------------------------------------------------------------------+
void CheckPendingExpiry()
{
   bool deleted = false;
   if(g_buy_stop_ticket > 0)
   {
      if(OrderSelect(g_buy_stop_ticket)) { trade.OrderDelete(g_buy_stop_ticket); deleted = true; }
      g_buy_stop_ticket = 0;
   }
   if(g_sell_stop_ticket > 0)
   {
      if(OrderSelect(g_sell_stop_ticket)) { trade.OrderDelete(g_sell_stop_ticket); deleted = true; }
      g_sell_stop_ticket = 0;
   }
   if(deleted) LogInfo("Entry window expired — pendings cancelled");
   g_state = NDCB_COMP_DONE;
   SaveState();
}

//+------------------------------------------------------------------+
//| MAIN TICK HANDLER — v1.1 bar-close driven                       |
//|                                                                  |
//| Daily reset and safety checks run every tick. The signal logic   |
//| (comp evaluation, pending placement, expiry) is gated on H1      |
//| NEW BAR events to be tick-frequency independent. Trade           |
//| management (fill detection, hard close) runs every tick because  |
//| pending fills and SL/TP can hit intrabar.                        |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime now       = TimeCurrent();
   datetime day_start = GetBrokerDayStart(now);

   if(g_current_day != day_start) DailyReset(day_start);

   if(!CheckSafetyLayers()) return;
   if(g_daily_halt || g_permanent_halt) return;

   // Pending fill detection runs every tick (intrabar fills must be caught)
   if(g_state == NDCB_PENDING)
   {
      CheckPendingFill();   // promotes to TRADE_ACTIVE if any position exists
   }

   // Trade management runs every tick (SL/TP/hard close are intrabar events)
   if(g_state == NDCB_TRADE_ACTIVE)
   {
      ManageActiveTrade();
   }

   // ── H1 bar-close driven signal logic ──────────────────────────────────
   if(!IsNewBar()) return;

   // Use the just-closed bar's hour as the trigger. iTime(0) is the new bar
   // that just opened; its hour tells us we are AT THE START of that hour.
   datetime new_bar_time = iTime(_Symbol, PERIOD_H1, 0);
   int      bar_hour     = GetBrokerHour(new_bar_time);

   // Trigger 1: New bar opens at InpEntryStartHour (15:00 broker).
   //            Comp window 11:00-14:59 has just closed. Evaluate filter.
   if(g_state == NDCB_WAITING && bar_hour == InpEntryStartHour)
   {
      EvaluateCompressionAndPlaceOrders();
      return;
   }

   // Trigger 2: New bar opens at or past entry window end → cancel pendings
   //            and mark day done.
   if(g_state == NDCB_PENDING && bar_hour >= InpEntryEndHour)
   {
      CheckPendingExpiry();
      return;
   }

   // Defensive: if state is still WAITING after the entry window passed
   //            (e.g. EA started mid-day, missed the 15:00 trigger), close out.
   if(g_state == NDCB_WAITING && bar_hour >= InpEntryEndHour)
   {
      g_state = NDCB_COMP_DONE;
      SaveState();
   }
}
//+------------------------------------------------------------------+
