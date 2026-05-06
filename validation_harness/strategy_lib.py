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
