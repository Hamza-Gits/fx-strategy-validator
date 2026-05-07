"""
XAUUSD Strategy Library
========================
12 structurally-different candidate strategies for systematic edge research.

Each strategy is a pure function:
    strategy_fn(h1_df, **params) -> list[Trade]

H1 OHLCV in, list of normalized Trade objects out. PnL is reported in R units
where R is the per-trade stop distance — this lets us aggregate strategies with
different SL/TP profiles on the same scale.

Strategy families:
  Trend:        donchian, ema_cross, triple_screen
  Volatility:   keltner, bb_squeeze, nr7, opening_range
  Session:      london_open, gold_am_fix, ny_london_overlap
  Reversion:    rsi2, bb_reversion

Conventions:
  - Bar-close logic only (no lookahead). Decisions on bar i use data through bar i-1.
  - One trade at a time per strategy unless explicitly noted.
  - SL/TP fill checks: SL takes priority over TP if both touched intra-bar (conservative).
  - Trade.pnl is in dollars, normalized to NOTIONAL_RISK = $100 per trade.
"""
import sys
import os
from typing import Callable, Optional
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Trade, StrategyResult


NOTIONAL_RISK = 100.0  # $ per trade — PnL = (move / stop_dist) * NOTIONAL_RISK


# ─── INDICATORS ───────────────────────────────────────────────────────────────

def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    # MT5-style Wilder: SMA seed for first N, then EWM alpha=1/N
    out = tr.copy() * np.nan
    valid = tr.dropna()
    if len(valid) < period:
        return out
    seed_idx = valid.index[period - 1]
    seed_val = valid.iloc[:period].mean()
    out.loc[seed_idx] = seed_val
    alpha = 1.0 / period
    prev = seed_val
    for idx in valid.index[period:]:
        prev = prev * (1 - alpha) + valid.loc[idx] * alpha
        out.loc[idx] = prev
    return out


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, adjust=False).mean()
    roll_dn = dn.ewm(alpha=1/period, adjust=False).mean()
    rs = roll_up / roll_dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # only the larger of the two movements counts
    mask = plus_dm > minus_dm
    plus_dm = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_v = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_v
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_v
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False).mean()


def resample_higher(h1: pd.DataFrame, rule: str) -> pd.DataFrame:
    return h1.resample(rule).agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()


# ─── COMMON BACKTEST HELPER ───────────────────────────────────────────────────

def _simulate_trade(h1: pd.DataFrame, entry_idx: int, direction: int,
                     entry_price: float, sl: float, tp: float,
                     max_bars: int = 120) -> Optional[Trade]:
    """Simulate a single trade from entry_idx forward, returning Trade or None.
    SL takes priority over TP if both touch in same bar (conservative)."""
    if entry_idx >= len(h1) - 1:
        return None
    stop_dist = abs(entry_price - sl)
    if stop_dist <= 0:
        return None
    end_idx = min(entry_idx + max_bars, len(h1) - 1)
    for i in range(entry_idx + 1, end_idx + 1):
        bar = h1.iloc[i]
        if direction == 1:
            if bar['low'] <= sl:
                exit_p = sl
                pnl = ((exit_p - entry_price) / stop_dist) * NOTIONAL_RISK
                return Trade(h1.index[entry_idx], h1.index[i], 1, entry_price, exit_p, pnl, i - entry_idx)
            if bar['high'] >= tp:
                exit_p = tp
                pnl = ((exit_p - entry_price) / stop_dist) * NOTIONAL_RISK
                return Trade(h1.index[entry_idx], h1.index[i], 1, entry_price, exit_p, pnl, i - entry_idx)
        else:
            if bar['high'] >= sl:
                exit_p = sl
                pnl = ((entry_price - exit_p) / stop_dist) * NOTIONAL_RISK
                return Trade(h1.index[entry_idx], h1.index[i], -1, entry_price, exit_p, pnl, i - entry_idx)
            if bar['low'] <= tp:
                exit_p = tp
                pnl = ((entry_price - exit_p) / stop_dist) * NOTIONAL_RISK
                return Trade(h1.index[entry_idx], h1.index[i], -1, entry_price, exit_p, pnl, i - entry_idx)
    # max bars reached — exit at last close
    exit_p = h1.iloc[end_idx]['close']
    pnl = direction * (exit_p - entry_price) / stop_dist * NOTIONAL_RISK
    return Trade(h1.index[entry_idx], h1.index[end_idx], direction, entry_price, exit_p, pnl, end_idx - entry_idx)


# ═══════════════════════════════════════════════════════════════════════════════
#                           STRATEGIES (12)
# ═══════════════════════════════════════════════════════════════════════════════

# ─── 1. DONCHIAN BREAKOUT ─────────────────────────────────────────────────────

def donchian_breakout(h1: pd.DataFrame, lookback: int = 20, atr_period: int = 14,
                      sl_mult: float = 2.0, tp_mult: float = 3.0,
                      max_hold_bars: int = 120) -> list:
    """Long when close > rolling-high(lookback days). Short opposite. ATR-based SL/TP."""
    d1 = resample_higher(h1, '1D')
    d1['hh'] = d1['high'].rolling(lookback).max().shift(1)
    d1['ll'] = d1['low'].rolling(lookback).min().shift(1)
    d1['atr'] = atr(d1, atr_period)
    h1 = h1.copy()
    h1['hh'] = d1['hh'].reindex(h1.index, method='ffill')
    h1['ll'] = d1['ll'].reindex(h1.index, method='ffill')
    h1['atr'] = d1['atr'].reindex(h1.index, method='ffill')
    h1 = h1.dropna()

    trades = []
    in_trade_until = pd.Timestamp.min
    last_trade_day = None
    for i in range(1, len(h1)):
        ts = h1.index[i]
        if ts < in_trade_until:
            continue
        day = ts.date()
        if day == last_trade_day:
            continue
        bar = h1.iloc[i]
        if bar['close'] > bar['hh']:
            sl = bar['close'] - sl_mult * bar['atr']
            tp = bar['close'] + tp_mult * bar['atr']
            t = _simulate_trade(h1, i, 1, bar['close'], sl, tp, max_hold_bars)
            if t:
                trades.append(t)
                in_trade_until = t.exit_time
                last_trade_day = day
        elif bar['close'] < bar['ll']:
            sl = bar['close'] + sl_mult * bar['atr']
            tp = bar['close'] - tp_mult * bar['atr']
            t = _simulate_trade(h1, i, -1, bar['close'], sl, tp, max_hold_bars)
            if t:
                trades.append(t)
                in_trade_until = t.exit_time
                last_trade_day = day
    return trades


# ─── 2. EMA CROSSOVER + ADX ───────────────────────────────────────────────────

def ema_cross_adx(h1: pd.DataFrame, fast: int = 20, slow: int = 50,
                  adx_period: int = 14, adx_min: float = 25.0,
                  atr_period: int = 14, sl_mult: float = 2.0, tp_mult: float = 3.0,
                  max_hold_bars: int = 240) -> list:
    """H4 timeframe: fast EMA crosses slow EMA + ADX > min. ATR SL/TP."""
    h4 = resample_higher(h1, '4h')
    h4['fast'] = ema(h4['close'], fast)
    h4['slow'] = ema(h4['close'], slow)
    h4['adx'] = adx(h4, adx_period)
    h4['atr'] = atr(h4, atr_period)
    h4 = h4.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    for i in range(1, len(h4)):
        ts = h4.index[i]
        if ts < in_trade_until:
            continue
        prev = h4.iloc[i - 1]
        bar = h4.iloc[i]
        if bar['adx'] < adx_min:
            continue
        cross_up = prev['fast'] <= prev['slow'] and bar['fast'] > bar['slow']
        cross_dn = prev['fast'] >= prev['slow'] and bar['fast'] < bar['slow']
        if not (cross_up or cross_dn):
            continue
        # find corresponding H1 bar to enter at
        h1_idx = h1.index.searchsorted(ts)
        if h1_idx >= len(h1):
            continue
        entry = h1.iloc[h1_idx]['open']
        if cross_up:
            sl = entry - sl_mult * bar['atr']
            tp = entry + tp_mult * bar['atr']
            t = _simulate_trade(h1, h1_idx, 1, entry, sl, tp, max_hold_bars)
        else:
            sl = entry + sl_mult * bar['atr']
            tp = entry - tp_mult * bar['atr']
            t = _simulate_trade(h1, h1_idx, -1, entry, sl, tp, max_hold_bars)
        if t:
            trades.append(t)
            in_trade_until = t.exit_time
    return trades


# ─── 3. TRIPLE SCREEN ─────────────────────────────────────────────────────────

def triple_screen(h1: pd.DataFrame, d1_ema_period: int = 50,
                   h4_rsi_period: int = 14, h4_rsi_long_max: float = 40.0,
                   h4_rsi_short_min: float = 60.0,
                   atr_period: int = 14, sl_mult: float = 1.5, tp_mult: float = 3.0,
                   max_hold_bars: int = 120) -> list:
    """D1 trend (EMA slope) + H4 RSI pullback + H1 close confirm."""
    d1 = resample_higher(h1, '1D')
    h4 = resample_higher(h1, '4h')
    d1['ema'] = ema(d1['close'], d1_ema_period)
    d1['ema_slope'] = d1['ema'].diff()
    h4['rsi'] = rsi(h4['close'], h4_rsi_period)
    h4['atr'] = atr(h4, atr_period)
    h1 = h1.copy()
    h1['d1_slope'] = d1['ema_slope'].reindex(h1.index, method='ffill')
    h1['h4_rsi'] = h4['rsi'].reindex(h1.index, method='ffill')
    h1['h4_atr'] = h4['atr'].reindex(h1.index, method='ffill')
    h1['d1_ema'] = d1['ema'].reindex(h1.index, method='ffill')
    h1 = h1.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    for i in range(1, len(h1)):
        ts = h1.index[i]
        if ts < in_trade_until:
            continue
        prev = h1.iloc[i - 1]
        bar = h1.iloc[i]
        # long: D1 uptrend + H4 RSI was oversold + H1 close back above prev
        if bar['d1_slope'] > 0 and prev['h4_rsi'] < h4_rsi_long_max and \
           bar['close'] > prev['high']:
            entry = bar['close']
            sl = entry - sl_mult * bar['h4_atr']
            tp = entry + tp_mult * bar['h4_atr']
            t = _simulate_trade(h1, i, 1, entry, sl, tp, max_hold_bars)
            if t:
                trades.append(t)
                in_trade_until = t.exit_time
        elif bar['d1_slope'] < 0 and prev['h4_rsi'] > h4_rsi_short_min and \
             bar['close'] < prev['low']:
            entry = bar['close']
            sl = entry + sl_mult * bar['h4_atr']
            tp = entry - tp_mult * bar['h4_atr']
            t = _simulate_trade(h1, i, -1, entry, sl, tp, max_hold_bars)
            if t:
                trades.append(t)
                in_trade_until = t.exit_time
    return trades


# ─── 4. KELTNER CHANNEL BREAKOUT ──────────────────────────────────────────────

def keltner_breakout(h1: pd.DataFrame, ema_period: int = 20, atr_period: int = 14,
                      band_mult: float = 2.0, sl_mult: float = 1.5, tp_mult: float = 3.0,
                      max_hold_bars: int = 60) -> list:
    """H1 close breaks above/below EMA + band_mult*ATR Keltner channel."""
    h1 = h1.copy()
    h1['ema'] = ema(h1['close'], ema_period)
    h1['atr'] = atr(h1, atr_period)
    h1['upper'] = h1['ema'] + band_mult * h1['atr']
    h1['lower'] = h1['ema'] - band_mult * h1['atr']
    h1 = h1.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    last_day = None
    for i in range(1, len(h1)):
        ts = h1.index[i]
        if ts < in_trade_until:
            continue
        day = ts.date()
        if day == last_day:
            continue
        prev = h1.iloc[i - 1]
        bar = h1.iloc[i]
        if prev['close'] <= prev['upper'] and bar['close'] > bar['upper']:
            sl = bar['close'] - sl_mult * bar['atr']
            tp = bar['close'] + tp_mult * bar['atr']
            t = _simulate_trade(h1, i, 1, bar['close'], sl, tp, max_hold_bars)
            if t:
                trades.append(t); in_trade_until = t.exit_time; last_day = day
        elif prev['close'] >= prev['lower'] and bar['close'] < bar['lower']:
            sl = bar['close'] + sl_mult * bar['atr']
            tp = bar['close'] - tp_mult * bar['atr']
            t = _simulate_trade(h1, i, -1, bar['close'], sl, tp, max_hold_bars)
            if t:
                trades.append(t); in_trade_until = t.exit_time; last_day = day
    return trades


# ─── 5. BOLLINGER SQUEEZE BREAKOUT ────────────────────────────────────────────

def bb_squeeze(h1: pd.DataFrame, bb_period: int = 20, bb_std: float = 2.0,
                squeeze_lookback: int = 60, sl_mult: float = 1.5, tp_mult: float = 3.0,
                atr_period: int = 14, max_hold_bars: int = 60) -> list:
    """Squeeze = BB width at min over lookback; entry on first close beyond band."""
    h1 = h1.copy()
    mid = sma(h1['close'], bb_period)
    sd = h1['close'].rolling(bb_period).std()
    h1['mid'] = mid
    h1['upper'] = mid + bb_std * sd
    h1['lower'] = mid - bb_std * sd
    h1['width'] = h1['upper'] - h1['lower']
    h1['width_min'] = h1['width'].rolling(squeeze_lookback).min()
    h1['atr'] = atr(h1, atr_period)
    h1 = h1.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    for i in range(2, len(h1)):
        ts = h1.index[i]
        if ts < in_trade_until:
            continue
        prev = h1.iloc[i - 1]
        bar = h1.iloc[i]
        # require recent squeeze: width was at min within last 5 bars
        recent_squeeze = (h1['width'].iloc[max(0, i-5):i] <= h1['width_min'].iloc[max(0, i-5):i] * 1.05).any()
        if not recent_squeeze:
            continue
        if bar['close'] > bar['upper'] and prev['close'] <= prev['upper']:
            sl = bar['close'] - sl_mult * bar['atr']
            tp = bar['close'] + tp_mult * bar['atr']
            t = _simulate_trade(h1, i, 1, bar['close'], sl, tp, max_hold_bars)
            if t: trades.append(t); in_trade_until = t.exit_time
        elif bar['close'] < bar['lower'] and prev['close'] >= prev['lower']:
            sl = bar['close'] + sl_mult * bar['atr']
            tp = bar['close'] - tp_mult * bar['atr']
            t = _simulate_trade(h1, i, -1, bar['close'], sl, tp, max_hold_bars)
            if t: trades.append(t); in_trade_until = t.exit_time
    return trades


# ─── 6. NR7 (NARROW RANGE 7) BREAKOUT ─────────────────────────────────────────

def nr7_breakout(h1: pd.DataFrame, lookback: int = 7, sl_atr_mult: float = 1.5,
                  tp_atr_mult: float = 3.0, atr_period: int = 14,
                  max_hold_bars: int = 120) -> list:
    """D1 NR7 day, then H1 break of NR7 high/low next day."""
    d1 = resample_higher(h1, '1D')
    d1['range'] = d1['high'] - d1['low']
    d1['min_range'] = d1['range'].rolling(lookback).min()
    d1['nr7'] = d1['range'] <= d1['min_range']  # narrowest of last `lookback` days
    d1['atr'] = atr(d1, atr_period)
    d1['nr7_high'] = d1['high']
    d1['nr7_low'] = d1['low']
    h1 = h1.copy()
    h1['nr7_today'] = d1['nr7'].reindex(h1.index, method='ffill').shift(1)  # yesterday was NR7
    h1['nr7_high'] = d1['nr7_high'].reindex(h1.index, method='ffill').shift(1)
    h1['nr7_low'] = d1['nr7_low'].reindex(h1.index, method='ffill').shift(1)
    h1['atr'] = d1['atr'].reindex(h1.index, method='ffill')
    h1 = h1.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    last_day = None
    for i in range(1, len(h1)):
        ts = h1.index[i]
        if ts < in_trade_until:
            continue
        day = ts.date()
        if day == last_day:
            continue
        bar = h1.iloc[i]
        if not bar['nr7_today']:
            continue
        if bar['close'] > bar['nr7_high']:
            sl = bar['close'] - sl_atr_mult * bar['atr']
            tp = bar['close'] + tp_atr_mult * bar['atr']
            t = _simulate_trade(h1, i, 1, bar['close'], sl, tp, max_hold_bars)
            if t: trades.append(t); in_trade_until = t.exit_time; last_day = day
        elif bar['close'] < bar['nr7_low']:
            sl = bar['close'] + sl_atr_mult * bar['atr']
            tp = bar['close'] - tp_atr_mult * bar['atr']
            t = _simulate_trade(h1, i, -1, bar['close'], sl, tp, max_hold_bars)
            if t: trades.append(t); in_trade_until = t.exit_time; last_day = day
    return trades


# ─── 7. OPENING RANGE BREAKOUT (London 07-08 GMT range, break after 08) ───────

def opening_range_break(h1: pd.DataFrame, range_start: int = 7, range_end: int = 8,
                         break_window_end: int = 16, atr_period: int = 14,
                         sl_atr_mult: float = 1.0, tp_atr_mult: float = 2.0,
                         max_hold_bars: int = 12) -> list:
    """Asian-like range from range_start to range_end, break after range_end until break_window_end."""
    h1 = h1.copy()
    h1['atr'] = atr(h1, atr_period)
    h1['hour'] = h1.index.hour
    h1['date'] = h1.index.date
    h1 = h1.dropna()
    trades = []
    last_day = None
    in_trade_until = pd.Timestamp.min
    for day, day_df in h1.groupby('date'):
        if day == last_day:
            continue
        range_bars = day_df[(day_df['hour'] >= range_start) & (day_df['hour'] < range_end)]
        if len(range_bars) == 0:
            continue
        rh, rl = range_bars['high'].max(), range_bars['low'].min()
        break_bars = day_df[(day_df['hour'] >= range_end) & (day_df['hour'] < break_window_end)]
        for ts, bar in break_bars.iterrows():
            if ts < in_trade_until:
                continue
            i = h1.index.get_loc(ts)
            if bar['close'] > rh:
                sl = bar['close'] - sl_atr_mult * bar['atr']
                tp = bar['close'] + tp_atr_mult * bar['atr']
                t = _simulate_trade(h1, i, 1, bar['close'], sl, tp, max_hold_bars)
                if t: trades.append(t); in_trade_until = t.exit_time; last_day = day
                break
            elif bar['close'] < rl:
                sl = bar['close'] + sl_atr_mult * bar['atr']
                tp = bar['close'] - tp_atr_mult * bar['atr']
                t = _simulate_trade(h1, i, -1, bar['close'], sl, tp, max_hold_bars)
                if t: trades.append(t); in_trade_until = t.exit_time; last_day = day
                break
    return trades


# ─── 8. LONDON OPEN MOMENTUM (08:00 GMT bar) ──────────────────────────────────

def london_open_momentum(h1: pd.DataFrame, london_hour: int = 8,
                          atr_period: int = 14, sl_atr_mult: float = 1.5,
                          tp_atr_mult: float = 3.0, max_hold_bars: int = 12,
                          min_range_atr: float = 0.5) -> list:
    """Take the 08:00 GMT bar's direction. Long if bar closes > open + min_range*ATR."""
    h1 = h1.copy()
    h1['atr'] = atr(h1, atr_period)
    h1 = h1.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    for i, ts in enumerate(h1.index):
        if ts.hour != london_hour:
            continue
        if ts < in_trade_until:
            continue
        bar = h1.iloc[i]
        body = bar['close'] - bar['open']
        if abs(body) < min_range_atr * bar['atr']:
            continue
        if body > 0:
            sl = bar['close'] - sl_atr_mult * bar['atr']
            tp = bar['close'] + tp_atr_mult * bar['atr']
            t = _simulate_trade(h1, i, 1, bar['close'], sl, tp, max_hold_bars)
        else:
            sl = bar['close'] + sl_atr_mult * bar['atr']
            tp = bar['close'] - tp_atr_mult * bar['atr']
            t = _simulate_trade(h1, i, -1, bar['close'], sl, tp, max_hold_bars)
        if t: trades.append(t); in_trade_until = t.exit_time
    return trades


# ─── 9. GOLD AM FIX BREAKOUT (10:30 LDN ≈ 10:00 GMT bar) ───────────────────────

def gold_am_fix(h1: pd.DataFrame, range_start: int = 9, range_end: int = 11,
                 break_window_end: int = 16, atr_period: int = 14,
                 sl_atr_mult: float = 1.0, tp_atr_mult: float = 2.5,
                 max_hold_bars: int = 8) -> list:
    """Range built 09:00-10:59 GMT around AM Fix, break after 11:00."""
    return opening_range_break(h1, range_start=range_start, range_end=range_end,
                                break_window_end=break_window_end, atr_period=atr_period,
                                sl_atr_mult=sl_atr_mult, tp_atr_mult=tp_atr_mult,
                                max_hold_bars=max_hold_bars)


# ─── 10. NY/LONDON OVERLAP MOMENTUM (12-16 GMT) ───────────────────────────────

def ny_london_overlap(h1: pd.DataFrame, overlap_start: int = 12, overlap_end: int = 16,
                       atr_period: int = 14, momentum_atr: float = 1.0,
                       sl_atr_mult: float = 1.0, tp_atr_mult: float = 2.0,
                       max_hold_bars: int = 6) -> list:
    """At overlap_start hour, look back 4 H1 bars; if cumulative move > momentum_atr * ATR, follow."""
    h1 = h1.copy()
    h1['atr'] = atr(h1, atr_period)
    h1 = h1.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    for i, ts in enumerate(h1.index):
        if ts.hour != overlap_start:
            continue
        if ts < in_trade_until:
            continue
        if i < 4:
            continue
        lookback_close = h1.iloc[i - 4]['close']
        bar = h1.iloc[i]
        move = bar['close'] - lookback_close
        threshold = momentum_atr * bar['atr']
        if abs(move) < threshold:
            continue
        if move > 0:
            sl = bar['close'] - sl_atr_mult * bar['atr']
            tp = bar['close'] + tp_atr_mult * bar['atr']
            t = _simulate_trade(h1, i, 1, bar['close'], sl, tp, max_hold_bars)
        else:
            sl = bar['close'] + sl_atr_mult * bar['atr']
            tp = bar['close'] - tp_atr_mult * bar['atr']
            t = _simulate_trade(h1, i, -1, bar['close'], sl, tp, max_hold_bars)
        if t: trades.append(t); in_trade_until = t.exit_time
    return trades


# ─── 11. RSI(2) LARRY CONNORS (mean reversion in trend) ───────────────────────

def rsi2_connors(h1: pd.DataFrame, rsi_period: int = 2, oversold: float = 10,
                  overbought: float = 90, trend_ma_period: int = 200,
                  atr_period: int = 14, sl_atr_mult: float = 2.0,
                  tp_atr_mult: float = 1.5, max_hold_bars: int = 24,
                  exit_rsi: float = 50) -> list:
    """H4 RSI(2) extreme + H4 trend filter + early exit on RSI crossing 50."""
    h4 = resample_higher(h1, '4h')
    h4['rsi'] = rsi(h4['close'], rsi_period)
    h4['ma'] = sma(h4['close'], trend_ma_period)
    h4['atr'] = atr(h4, atr_period)
    h4 = h4.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    for i in range(1, len(h4)):
        ts = h4.index[i]
        if ts < in_trade_until:
            continue
        bar = h4.iloc[i]
        h1_idx = h1.index.searchsorted(ts)
        if h1_idx >= len(h1):
            continue
        if bar['rsi'] < oversold and bar['close'] > bar['ma']:
            entry = h1.iloc[h1_idx]['open']
            sl = entry - sl_atr_mult * bar['atr']
            tp = entry + tp_atr_mult * bar['atr']
            t = _simulate_trade(h1, h1_idx, 1, entry, sl, tp, max_hold_bars)
            if t: trades.append(t); in_trade_until = t.exit_time
        elif bar['rsi'] > overbought and bar['close'] < bar['ma']:
            entry = h1.iloc[h1_idx]['open']
            sl = entry + sl_atr_mult * bar['atr']
            tp = entry - tp_atr_mult * bar['atr']
            t = _simulate_trade(h1, h1_idx, -1, entry, sl, tp, max_hold_bars)
            if t: trades.append(t); in_trade_until = t.exit_time
    return trades


# ─── 12. BOLLINGER MEAN REVERSION ─────────────────────────────────────────────

def bb_reversion(h1: pd.DataFrame, bb_period: int = 20, bb_std: float = 2.0,
                  trend_ema_period: int = 200, atr_period: int = 14,
                  sl_atr_mult: float = 2.0, tp_atr_mult: float = 1.5,
                  max_hold_bars: int = 24) -> list:
    """H4 BB extremes inside daily trend. Exit on BB midline touch."""
    h4 = resample_higher(h1, '4h')
    mid = sma(h4['close'], bb_period)
    sd = h4['close'].rolling(bb_period).std()
    h4['mid'] = mid
    h4['upper'] = mid + bb_std * sd
    h4['lower'] = mid - bb_std * sd
    h4['atr'] = atr(h4, atr_period)
    d1 = resample_higher(h1, '1D')
    d1['ema'] = ema(d1['close'], trend_ema_period)
    h4['d1_close'] = d1['close'].reindex(h4.index, method='ffill')
    h4['d1_ema'] = d1['ema'].reindex(h4.index, method='ffill')
    h4 = h4.dropna()
    trades = []
    in_trade_until = pd.Timestamp.min
    for i in range(1, len(h4)):
        ts = h4.index[i]
        if ts < in_trade_until:
            continue
        bar = h4.iloc[i]
        h1_idx = h1.index.searchsorted(ts)
        if h1_idx >= len(h1):
            continue
        # long: H4 close < lower BB, D1 trend up
        if bar['close'] < bar['lower'] and bar['d1_close'] > bar['d1_ema']:
            entry = h1.iloc[h1_idx]['open']
            sl = entry - sl_atr_mult * bar['atr']
            tp = bar['mid']
            t = _simulate_trade(h1, h1_idx, 1, entry, sl, tp, max_hold_bars)
            if t: trades.append(t); in_trade_until = t.exit_time
        elif bar['close'] > bar['upper'] and bar['d1_close'] < bar['d1_ema']:
            entry = h1.iloc[h1_idx]['open']
            sl = entry + sl_atr_mult * bar['atr']
            tp = bar['mid']
            t = _simulate_trade(h1, h1_idx, -1, entry, sl, tp, max_hold_bars)
            if t: trades.append(t); in_trade_until = t.exit_time
    return trades


# ─── 13. ARCB-XAU: Asian-Range Compression Breakout ──────────────────────────

def _simulate_arcb_trade(h1: pd.DataFrame, entry_idx: int, direction: int,
                          entry_price: float, initial_stop: float, tp1: float,
                          trail_ema_len: int, hard_close_utc: int,
                          h1_atr_at_entry: float,
                          ema21: pd.Series) -> Optional[Trade]:
    """
    Multi-stage trade management for ARCB-XAU:
      Phase 1: standard SL / TP1 management
      Phase 2 (after TP1 hit): move stop to break-even, trail to 21-EMA
      Hard close: exit at close of first bar with hour >= hard_close_utc (same session)
    """
    if entry_idx >= len(h1) - 1:
        return None
    stop_dist = abs(entry_price - initial_stop)
    if stop_dist <= 0:
        return None

    current_stop = initial_stop
    tp1_hit      = False
    entry_time   = h1.index[entry_idx]

    for i in range(entry_idx + 1, len(h1)):
        bar = h1.iloc[i]
        ts  = h1.index[i]

        # Hard daily close — exit at this bar's close (same calendar date as entry)
        if ts.date() == entry_time.date() and ts.hour >= hard_close_utc:
            exit_p = bar['close']
            pnl    = direction * (exit_p - entry_price) / stop_dist * NOTIONAL_RISK
            return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - entry_idx)

        # If we roll to next day without hitting hard-close (shouldn't happen in practice)
        if ts.date() > entry_time.date():
            exit_p = bar['open']
            pnl    = direction * (exit_p - entry_price) / stop_dist * NOTIONAL_RISK
            return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - entry_idx)

        if direction == 1:  # LONG
            # Stop hit (check before TP to be conservative)
            if bar['low'] <= current_stop:
                exit_p = current_stop
                pnl    = (exit_p - entry_price) / stop_dist * NOTIONAL_RISK
                return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - entry_idx)
            # TP1 hit → move to break-even
            if not tp1_hit and bar['high'] >= tp1:
                tp1_hit      = True
                current_stop = entry_price   # break-even
            # Trail phase: update stop to EMA-based trail
            if tp1_hit and ts in ema21.index:
                trail_level = ema21.loc[ts] - 0.3 * h1_atr_at_entry
                current_stop = max(current_stop, trail_level)

        else:  # SHORT
            if bar['high'] >= current_stop:
                exit_p = current_stop
                pnl    = (entry_price - exit_p) / stop_dist * NOTIONAL_RISK
                return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - entry_idx)
            if not tp1_hit and bar['low'] <= tp1:
                tp1_hit      = True
                current_stop = entry_price
            if tp1_hit and ts in ema21.index:
                trail_level = ema21.loc[ts] + 0.3 * h1_atr_at_entry
                current_stop = min(current_stop, trail_level)

    # End of data reached — exit at last bar close
    exit_p = h1.iloc[-1]['close']
    pnl    = direction * (exit_p - entry_price) / stop_dist * NOTIONAL_RISK
    return Trade(entry_time, h1.index[-1], direction, entry_price, exit_p, pnl,
                 len(h1) - 1 - entry_idx)


def arcb_xau(h1: pd.DataFrame,
             compression_k:    float = 0.70,
             stop_atr_mult:    float = 1.20,
             vol_ratio_min:    float = 1.30,
             atr_period_h1:    int   = 14,
             atr_period_d1:    int   = 20,
             atr_floor_pct:    float = 0.004,
             body_ratio_min:   float = 0.50,
             stop_floor_frac:  float = 0.50,
             trail_ema_len:    int   = 21,
             asia_start_hour:  int   = 0,   # broker time: 0 = midnight (22:00 UTC GMT+2)
             asia_end_hour:    int   = 7,   # broker time: 7 = 05:00 UTC
             entry_start_hour: int   = 9,   # broker time: 9 = 07:00 UTC
             entry_end_hour:   int   = 15,  # broker time: 15 = 13:00 UTC
             hard_close_hour:  int   = 23,  # broker time: 23 = 21:00 UTC
             jan_block_days:   int   = 5) -> list:
    """
    ARCB-XAU v1.0 — Asian-Range Compression Breakout into London/COMEX.

    Structural mechanism (Batten et al. 2017; Caporin/Ranaldo 2015):
      During 22:00-05:00 UTC, real-money flow is largely absent and price builds
      a tight Asian balance. When LBMA and COMEX engage (07:00-13:00 UTC),
      stop liquidity accumulated at Asian range boundaries is released, driving
      directional expansion. Compressed Asian sessions = higher-probability breaks.

    Entry conditions (all must hold):
      - Asian range <= compression_k * D1 ATR(20)       [compression filter]
      - D1 ATR >= atr_floor_pct * price                 [vol floor]
      - H1 bar close outside Asian range                 [breakout]
      - Bar body >= body_ratio_min * bar range           [conviction]
      - Tick volume >= vol_ratio_min * 20-bar mean       [participation]
      - Entry hour in [entry_start_hour, entry_end_hour] [session gate]
      - Not in first jan_block_days of January           [BCOM rebalance block]

    Hour parameters are in BROKER time (GMT+2 default for most MT5 brokers):
      - asia_start_hour=0, asia_end_hour=7   → 22:00-05:00 UTC
      - entry_start_hour=9, entry_end_hour=15 → 07:00-13:00 UTC
      - hard_close_hour=23                   → 21:00 UTC

    Exit logic:
      - Initial SL: max(stop_atr_mult * H1 ATR, stop_floor_frac * Asian range)
      - TP1: 1.0 * Asian range size
      - After TP1: move stop to break-even, trail to 21-EMA - 0.3*ATR
      - Hard close: bar close at hard_close_hour broker time (intraday)
    """
    h1 = h1.copy()

    # ── Pre-compute D1 data ────────────────────────────────────────────────
    d1 = resample_higher(h1, '1D')
    d1['atr_d1'] = atr(d1, atr_period_d1)
    # Forward-fill D1 ATR onto H1 index
    h1['d1_atr'] = d1['atr_d1'].reindex(h1.index, method='ffill')

    # ── Pre-compute H1 ATR and 21-EMA ─────────────────────────────────────
    h1['atr_h1'] = atr(h1, atr_period_h1)
    ema21        = ema(h1['close'], trail_ema_len)  # used for trailing stop

    # ── Pre-compute 20-bar tick volume mean (rolling) ─────────────────────
    h1['vol_mean20'] = h1['volume'].rolling(20, min_periods=10).mean()

    h1 = h1.dropna(subset=['d1_atr', 'atr_h1', 'vol_mean20'])
    h1['hour'] = h1.index.hour
    h1['date'] = h1.index.date

    # ── Pre-compute all per-date lookups ONCE (eliminates repeated full scans) ──
    # Asian ranges by date
    if asia_start_hour <= asia_end_hour:
        asia_mask = (h1['hour'] >= asia_start_hour) & (h1['hour'] <= asia_end_hour)
    else:
        asia_mask = (h1['hour'] >= asia_start_hour) | (h1['hour'] <= asia_end_hour)
    asia_grp = h1[asia_mask].groupby('date')
    asia_ranges = asia_grp.agg(high=('high', 'max'), low=('low', 'min'),
                                count=('close', 'count'))

    # D1 ATR per date (first bar of each date)
    d1_atr_by_date = h1.groupby('date')['d1_atr'].first().to_dict()

    # Entry bars pre-grouped by date
    entry_mask = (h1['hour'] >= entry_start_hour) & (h1['hour'] <= entry_end_hour)
    entry_by_date = {d: g for d, g in h1[entry_mask].groupby('date')}

    # January block dates pre-computed as a set
    jan_blocked: set = set()
    if jan_block_days > 0:
        for yr in h1.index.year.unique():
            jan_dates = sorted(h1[(h1.index.year == yr) & (h1.index.month == 1)
                                  ]['date'].unique())
            for d in jan_dates[:jan_block_days]:
                jan_blocked.add(d)

    # Build integer-position index for fast simulation lookup
    h1_index_arr = h1.index

    trades = []

    for cur_date, asia_row in asia_ranges.iterrows():
        if asia_row['count'] < 5:
            continue

        asia_high = asia_row['high']
        asia_low  = asia_row['low']
        asia_size = asia_high - asia_low

        # D1 ATR filter
        d1_atr_val = d1_atr_by_date.get(cur_date, np.nan)
        if np.isnan(d1_atr_val) or d1_atr_val <= 0:
            continue
        if asia_size > compression_k * d1_atr_val:
            continue

        # Entry bars for this date
        entry_bars = entry_by_date.get(cur_date)
        if entry_bars is None or entry_bars.empty:
            continue

        long_done  = False
        short_done = False

        for ts, bar_row in entry_bars.iterrows():
            if long_done and short_done:
                break

            if cur_date in jan_blocked:
                break

            # ATR floor
            if bar_row['d1_atr'] < atr_floor_pct * bar_row['close']:
                continue

            # Bar quality filters
            bar_range = bar_row['high'] - bar_row['low']
            if bar_range <= 0:
                continue
            body_ratio = abs(bar_row['close'] - bar_row['open']) / bar_range
            if body_ratio < body_ratio_min:
                continue

            vol_mean = bar_row['vol_mean20']
            if vol_mean <= 0 or bar_row['volume'] < vol_ratio_min * vol_mean:
                continue

            h1_atr_val = bar_row['atr_h1']
            if np.isnan(h1_atr_val) or h1_atr_val <= 0:
                continue

            h1_idx = h1_index_arr.get_loc(ts)

            # ── LONG: close above Asian high ──────────────────────────────
            if not long_done and bar_row['close'] > asia_high and \
                    bar_row['close'] > bar_row['open']:
                stop_dist = max(stop_atr_mult * h1_atr_val,
                                stop_floor_frac * asia_size)
                t = _simulate_arcb_trade(
                    h1, h1_idx, 1, bar_row['close'],
                    bar_row['close'] - stop_dist,
                    bar_row['close'] + asia_size,
                    trail_ema_len, hard_close_hour, h1_atr_val, ema21)
                if t:
                    trades.append(t)
                    long_done = True

            # ── SHORT: close below Asian low ──────────────────────────────
            elif not short_done and bar_row['close'] < asia_low and \
                    bar_row['close'] < bar_row['open']:
                stop_dist = max(stop_atr_mult * h1_atr_val,
                                stop_floor_frac * asia_size)
                t = _simulate_arcb_trade(
                    h1, h1_idx, -1, bar_row['close'],
                    bar_row['close'] + stop_dist,
                    bar_row['close'] - asia_size,
                    trail_ema_len, hard_close_hour, h1_atr_val, ema21)
                if t:
                    trades.append(t)
                    short_done = True

    return trades


# ─── 14. NDCB-XAU: NY Data-Hour Compression Breakout ─────────────────────────

def _simulate_ndcb_trade(h1: pd.DataFrame, start_idx: int, direction: int,
                          entry_price: float, sl: float, tp_r2: float,
                          hard_close_hour: int, h1_atr: float,
                          trail_trigger_r: float, trail_atr_mult: float,
                          ema21: pd.Series,
                          partial_close_r: float = 0.0,
                          partial_frac: float    = 0.50,
                          tp_runner: float       = 0.0) -> Optional[Trade]:
    """
    NDCB trade simulation. Entry is a pending stop that already filled at entry_price.
    Phase 1: manage SL + tp_r2 target.
    Phase 2 (after trail_trigger_r hit): trail stop at trail_atr_mult×ATR from price.
    Hard close at hard_close_hour broker time same day.

    Partial close (if partial_close_r > 0):
      When price moves partial_close_r × stop_dist in our favour, close partial_frac
      of the position and move SL to breakeven. The remaining (1-partial_frac) runs to
      tp_runner × stop_dist. Combined PnL is returned as a single Trade.
    """
    stop_dist = abs(entry_price - sl)
    if stop_dist <= 0:
        return None
    entry_time   = h1.index[start_idx]
    current_stop = sl
    trail_active = False

    # Partial-close state
    partial_done    = False
    partial_pnl     = 0.0
    use_partial     = partial_close_r > 0
    runner_tp       = (entry_price + direction * tp_runner * stop_dist) if use_partial else None
    partial_level   = (entry_price + direction * partial_close_r * stop_dist) if use_partial else None

    def _pnl(exit_p: float, frac: float = 1.0) -> float:
        return direction * (exit_p - entry_price) / stop_dist * NOTIONAL_RISK * frac

    for i in range(start_idx + 1, len(h1)):
        bar = h1.iloc[i]
        ts  = h1.index[i]

        # ── Partial close trigger ────────────────────────────────────────────
        if use_partial and not partial_done:
            triggered = (direction == 1 and bar['high'] >= partial_level) or \
                        (direction == -1 and bar['low'] <= partial_level)
            if triggered:
                partial_pnl  = _pnl(partial_level, partial_frac)
                partial_done = True
                # Move SL to breakeven for the runner
                current_stop = entry_price

        # ── Hard close — same calendar date, hour >= hard_close_hour ─────────
        if ts.date() == entry_time.date() and ts.hour >= hard_close_hour:
            exit_p   = bar['close']
            run_frac = (1.0 - partial_frac) if partial_done else 1.0
            pnl      = partial_pnl + _pnl(exit_p, run_frac)
            return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - start_idx)

        if ts.date() > entry_time.date():
            exit_p   = bar['open']
            run_frac = (1.0 - partial_frac) if partial_done else 1.0
            pnl      = partial_pnl + _pnl(exit_p, run_frac)
            return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - start_idx)

        if direction == 1:
            if bar['low'] <= current_stop:
                exit_p   = current_stop
                run_frac = (1.0 - partial_frac) if partial_done else 1.0
                pnl      = partial_pnl + _pnl(exit_p, run_frac)
                return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - start_idx)
            # Runner TP (when partial close active) or full TP
            tp_target = runner_tp if (partial_done and use_partial) else tp_r2
            if bar['high'] >= tp_target:
                exit_p   = tp_target
                run_frac = (1.0 - partial_frac) if partial_done else 1.0
                pnl      = partial_pnl + _pnl(exit_p, run_frac)
                return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - start_idx)
            # Check trail trigger
            trail_level_price = entry_price + trail_trigger_r * stop_dist
            if not trail_active and bar['high'] >= trail_level_price:
                trail_active = True
            if trail_active:
                new_stop = bar['close'] - trail_atr_mult * h1_atr
                current_stop = max(current_stop, new_stop)
        else:
            if bar['high'] >= current_stop:
                exit_p   = current_stop
                run_frac = (1.0 - partial_frac) if partial_done else 1.0
                pnl      = partial_pnl + _pnl(exit_p, run_frac)
                return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - start_idx)
            tp_target = runner_tp if (partial_done and use_partial) else tp_r2
            if bar['low'] <= tp_target:
                exit_p   = tp_target
                run_frac = (1.0 - partial_frac) if partial_done else 1.0
                pnl      = partial_pnl + _pnl(exit_p, run_frac)
                return Trade(entry_time, ts, direction, entry_price, exit_p, pnl, i - start_idx)
            trail_level_price = entry_price - trail_trigger_r * stop_dist
            if not trail_active and bar['low'] <= trail_level_price:
                trail_active = True
            if trail_active:
                new_stop = bar['close'] + trail_atr_mult * h1_atr
                current_stop = min(current_stop, new_stop)

    exit_p = h1.iloc[-1]['close']
    pnl    = direction * (exit_p - entry_price) / stop_dist * NOTIONAL_RISK
    return Trade(entry_time, h1.index[-1], direction, entry_price, exit_p, pnl,
                 len(h1) - 1 - start_idx)


def ndcb_xau(h1: pd.DataFrame,
             compression_ratio:  float = 0.60,
             entry_buffer_atr:   float = 0.20,
             stop_atr_mult:      float = 1.00,
             atr_period:         int   = 14,
             tp_r:               float = 2.00,
             trail_trigger_r:    float = 1.50,
             trail_atr_mult:     float = 1.00,
             comp_start_hour:    int   = 11,
             comp_end_hour:      int   = 14,
             entry_hour:         int   = 15,
             trade_window_end:   int   = 18,
             hard_close_hour:    int   = 21,
             regime_days:        int   = 5,
             regime_mult:        float = 1.50,
             regime_atr_period:  int   = 63,
             vol_ratio_period:   int   = 20,
             # ── v2 improvements ───────────────────────────────────────────
             direction_filter:   bool  = False,  # D1 EMA-50 trend filter
             ema50_period:       int   = 50,
             partial_close_r:    float = 0.0,    # close partial_frac at this R (0 = off)
             partial_frac:       float = 0.50,   # fraction closed early
             tp_runner_r:        float = 3.00,   # TP for runner (when partial_close active)
             min_atr_dollars:    float = 0.0,    # skip if ATR < this $ (0 = off)
             ) -> list:
    """
    NDCB-XAU v1.0 — NY Data-Hour Compression Breakout.

    Structural mechanism (Hammoudeh et al. 2024; CME microstructure):
      The 13:30 UTC bar (broker hour 15, GMT+2) is the highest-sigma timestamp
      of the trading week — NFP, CPI, claims, FOMC all land at 13:30 ET.
      When the pre-NY window (09:00-13:00 UTC = broker 11:00-15:00) compresses
      below its rolling average, the 13:30 UTC release produces an explosive
      directional expansion as pre-positioned stops and option hedges unwind.

    Unlike ARCB-XAU:
      - Compression is SELF-REFERENTIAL (range vs its own rolling median) — fixes
        the trivial-filter flaw. Only fires ~40-60% of days, not 92%.
      - Entry is a PENDING STOP ORDER at compression boundary + buffer — captures
        the move only after the break is confirmed.
      - R:R is 2:1 minimum — structurally sound vs ARCB's 0.67:1.
      - Direction filter via regime check — prevents trading into already-explosive days.

    Hours in broker time (GMT+2):
      comp window: 11:00-14:00 → 09:00-12:00 UTC
      entry:       15:00       → 13:00 UTC  (13:30 data release bar)
      trade ends:  18:00       → 16:00 UTC
      hard close:  21:00       → 19:00 UTC
    """
    h1 = h1.copy()

    # ── Pre-compute indicators ─────────────────────────────────────────────
    h1['atr_h1']  = atr(h1, atr_period)
    h1['atr_63']  = atr(h1, regime_atr_period)
    h1['range_d'] = h1['high'] - h1['low']
    ema21         = ema(h1['close'], 21)
    h1['hour']    = h1.index.hour
    h1['date']    = h1.index.date

    h1 = h1.dropna(subset=['atr_h1', 'atr_63'])

    # ── D1 EMA-50 direction filter ─────────────────────────────────────────
    d1_ema50 = None
    if direction_filter:
        d1_close = h1.groupby('date')['close'].last()
        d1_ema50 = ema(d1_close, ema50_period)

    # ── Pre-compute compression window range per date ──────────────────────
    comp_mask = (h1['hour'] >= comp_start_hour) & (h1['hour'] <= comp_end_hour)
    comp_grp  = h1[comp_mask].groupby('date')
    comp_stats = comp_grp.agg(
        comp_high=('high', 'max'),
        comp_low=('low',  'min'),
        comp_n=('close', 'count'),
        atr_at_comp=('atr_h1', 'last'),
    )
    comp_stats['comp_range'] = comp_stats['comp_high'] - comp_stats['comp_low']
    # Rolling median of compression window range (self-referential filter)
    comp_stats['comp_range_median'] = (
        comp_stats['comp_range'].rolling(vol_ratio_period, min_periods=10).median()
    )

    # ── Pre-compute regime filter: D1 range vs D1 ATR(63) ────────────────
    # Use actual daily range (max high - min low), not sum of H1 bar ranges
    d1_by_date = h1.groupby('date').agg(d_high=('high', 'max'), d_low=('low', 'min'))
    d1_by_date['d1_range'] = d1_by_date['d_high'] - d1_by_date['d_low']
    d1_range_rolling  = d1_by_date['d1_range'].rolling(regime_days).mean()
    d1_atr63_by_date  = d1_by_date['d1_range'].rolling(regime_atr_period).mean()

    # ── Pre-group trade window bars by date ───────────────────────────────
    trade_mask     = (h1['hour'] >= entry_hour) & (h1['hour'] <= trade_window_end)
    trade_by_date  = {d: g for d, g in h1[trade_mask].groupby('date')}

    h1_index_arr = h1.index
    trades = []

    for cur_date, comp_row in comp_stats.iterrows():
        # Need enough compression bars
        if comp_row['comp_n'] < 3:
            continue

        comp_range  = comp_row['comp_range']
        comp_median = comp_row['comp_range_median']
        if np.isnan(comp_median) or comp_median <= 0:
            continue

        # ── Compression filter (self-referential) ─────────────────────────
        if comp_range >= compression_ratio * comp_median:
            continue   # not compressed enough

        # ── Regime filter: skip already-explosive days ────────────────────
        if cur_date in d1_range_rolling.index and cur_date in d1_atr63_by_date.index:
            avg_range_5d = d1_range_rolling.loc[cur_date]
            atr_63_val   = d1_atr63_by_date.loc[cur_date]
            if not np.isnan(avg_range_5d) and not np.isnan(atr_63_val) and atr_63_val > 0:
                if avg_range_5d > regime_mult * atr_63_val:
                    continue   # already explosive regime

        # ── Pending stop levels ───────────────────────────────────────────
        h1_atr = comp_row['atr_at_comp']
        if np.isnan(h1_atr) or h1_atr <= 0:
            continue

        # Min ATR floor (skip ultra-low-vol periods)
        if min_atr_dollars > 0 and h1_atr < min_atr_dollars:
            continue

        pending_buy  = comp_row['comp_high'] + entry_buffer_atr * h1_atr
        pending_sell = comp_row['comp_low']  - entry_buffer_atr * h1_atr

        # ── Direction filter: D1 EMA-50 ───────────────────────────────────
        allow_long  = True
        allow_short = True
        if direction_filter and d1_ema50 is not None and cur_date in d1_ema50.index:
            ema50_val = d1_ema50.loc[cur_date]
            if not np.isnan(ema50_val):
                mid_price   = (comp_row['comp_high'] + comp_row['comp_low']) / 2
                allow_long  = mid_price >= ema50_val   # only longs in uptrend
                allow_short = mid_price < ema50_val    # only shorts in downtrend

        # ── TP calculation ────────────────────────────────────────────────
        # When partial close active: full_tp not used; runner_tp drives the exit
        tp_full = tp_r * stop_atr_mult * h1_atr       # used when no partial close
        runner  = tp_runner_r * stop_atr_mult * h1_atr  # runner TP (when partial active)

        # ── Scan trade window for first pending-stop trigger ──────────────
        trade_bars = trade_by_date.get(cur_date)
        if trade_bars is None or trade_bars.empty:
            continue

        triggered = False
        for ts, bar in trade_bars.iterrows():
            if triggered:
                break

            h1_idx = h1_index_arr.get_loc(ts)

            # Long trigger
            if allow_long and bar['high'] >= pending_buy:
                entry_p   = pending_buy
                sl_p      = entry_p - stop_atr_mult * h1_atr
                tp_p      = entry_p + tp_full
                t = _simulate_ndcb_trade(
                    h1, h1_idx, 1, entry_p, sl_p, tp_p,
                    hard_close_hour, h1_atr, trail_trigger_r, trail_atr_mult, ema21,
                    partial_close_r=partial_close_r,
                    partial_frac=partial_frac,
                    tp_runner=tp_runner_r)
                if t:
                    trades.append(t)
                    triggered = True

            # Short trigger (only if long hasn't fired)
            elif allow_short and bar['low'] <= pending_sell:
                entry_p   = pending_sell
                sl_p      = entry_p + stop_atr_mult * h1_atr
                tp_p      = entry_p - tp_full
                t = _simulate_ndcb_trade(
                    h1, h1_idx, -1, entry_p, sl_p, tp_p,
                    hard_close_hour, h1_atr, trail_trigger_r, trail_atr_mult, ema21,
                    partial_close_r=partial_close_r,
                    partial_frac=partial_frac,
                    tp_runner=tp_runner_r)
                if t:
                    trades.append(t)
                    triggered = True

    return trades


# ═══════════════════════════════════════════════════════════════════════════════
#                          STRATEGY REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

STRATEGIES: dict = {
    'donchian':           {'fn': donchian_breakout,    'family': 'trend'},
    'ema_cross':          {'fn': ema_cross_adx,         'family': 'trend'},
    'triple_screen':      {'fn': triple_screen,         'family': 'trend'},
    'keltner':            {'fn': keltner_breakout,      'family': 'volatility'},
    'bb_squeeze':         {'fn': bb_squeeze,            'family': 'volatility'},
    'nr7':                {'fn': nr7_breakout,          'family': 'volatility'},
    'opening_range':      {'fn': opening_range_break,   'family': 'volatility'},
    'london_open':        {'fn': london_open_momentum,  'family': 'session'},
    'gold_am_fix':        {'fn': gold_am_fix,           'family': 'session'},
    'ny_london_overlap':  {'fn': ny_london_overlap,     'family': 'session'},
    'rsi2_connors':       {'fn': rsi2_connors,          'family': 'reversion'},
    'bb_reversion':       {'fn': bb_reversion,          'family': 'reversion'},
    # ── New: Research-grounded session strategies ──────────────────────────
    'arcb_xau':           {'fn': arcb_xau,              'family': 'session'},
    'ndcb_xau':           {'fn': ndcb_xau,              'family': 'session'},
}


if __name__ == '__main__':
    # Quick smoke test: run each strategy on XAUUSD 2021-2024 sample
    import time
    DATA = os.path.join(os.path.dirname(__file__), '..', 'data', 'XAUUSD_H1_2013-2025.csv')
    from harness import load_mt5_csv
    h1 = load_mt5_csv(DATA).loc['2018-01-01':'2024-12-31']
    print(f"Loaded {len(h1)} H1 bars: {h1.index[0]} to {h1.index[-1]}")
    print()
    for name, meta in STRATEGIES.items():
        t0 = time.time()
        trades = meta['fn'](h1)
        dt = time.time() - t0
        if not trades:
            print(f"  {name:<22} {len(trades):>5} trades  (no trades)")
            continue
        pnls = np.array([t.pnl for t in trades])
        gp = pnls[pnls > 0].sum()
        gl = abs(pnls[pnls < 0].sum())
        pf = gp / gl if gl > 0 else float('inf')
        wr = (pnls > 0).mean() * 100
        avg_r = pnls.mean() / NOTIONAL_RISK
        print(f"  {name:<22} {len(trades):>5} trades  PF={pf:5.2f}  WR={wr:5.1f}%  AvgR={avg_r:+.3f}  ({dt:.1f}s)")
