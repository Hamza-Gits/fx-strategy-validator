"""
Strategy Template — D1 Momentum + W1 Trend Filter on FX MAJORS BASKET
======================================================================
Iteration 2 (Council-directed):
  - SAME RULES as iteration 1, NO parameter changes (avoid curve-fitting noise).
  - Add 5-pair basket to multiply OOS sample size from 26 -> ~70-90 trades.
  - Symbols: EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD.
  - Pool all trades into single OOS set for bootstrap + Deflated Sharpe.

Rules (UNCHANGED from iteration 1):
  - W1 close > W1 EMA(12) = bullish regime, < = bearish.
  - D1 close crosses BACK across D1 EMA(20) in W1-trend direction = entry.
  - Stop: 2 x ATR(14) on D1.
  - Target: 3 x ATR(14) on D1 (RR=1.5).
  - Exit on W1 trend flip OR SL/TP.

Per-symbol handling:
  - JPY pairs use 0.01 pip (vs 0.0001 for non-JPY).
  - Position sizing in ATR-relative terms so pip-value differences wash out.
"""

import sys
import os
from datetime import datetime
import warnings
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (
    StrategyResult, Trade, run_validation, GateConfig,
    load_mt5_data, load_mt5_csv_pair
)

# ─── DATA DIRECTORY ────────────────────────────────────────────────────────────
# Folder containing {SYMBOL}_H1_2013-2020.csv and {SYMBOL}_H1_2021-2025.csv
# Set via environment variable DATA_DIR or defaults to the repo /data folder.
DATA_DIR = os.environ.get(
    'DATA_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
)


# ─── INDICATORS ───────────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def resample_to_higher_tf(h1: pd.DataFrame, rule: str) -> pd.DataFrame:
    return h1.resample(rule).agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()


# ─── PER-SYMBOL STRATEGY ──────────────────────────────────────────────────────

def _run_single_symbol(h1: pd.DataFrame, symbol: str,
                       w1_ema_period: int = 12,
                       d1_ema_period: int = 20,
                       atr_period: int = 14,
                       stop_atr_mult: float = 2.0,
                       target_atr_mult: float = 3.0,
                       risk_per_trade_pct: float = 0.5) -> list:
    """Run D1+W1 strategy on a single symbol's H1 data, return list of Trade objects."""

    d1 = resample_to_higher_tf(h1, '1D')
    w1 = resample_to_higher_tf(h1, '1W')

    d1['ema'] = ema(d1['close'], d1_ema_period)
    d1['atr'] = atr(d1, atr_period)
    w1['ema'] = ema(w1['close'], w1_ema_period)

    d1['w1_ema'] = w1['ema'].reindex(d1.index, method='ffill')
    d1['w1_close'] = w1['close'].reindex(d1.index, method='ffill')
    d1 = d1.dropna()

    # Pip scale & lot value (account currency = USD assumed)
    is_jpy = 'JPY' in symbol.upper()
    pip = 0.01 if is_jpy else 0.0001

    trades = []
    in_trade = False
    direction = 0
    entry_price = 0.0
    entry_time = None
    sl = 0.0
    tp = 0.0
    risk_dollars = 0.0  # captured at entry for ATR-relative P&L

    NOTIONAL_RISK = 100.0  # $100 risk per trade -> normalises P&L across symbols

    prev = None
    for ts, row in d1.iterrows():
        if prev is None:
            prev = row
            continue

        # ── Exit logic ──
        if in_trade:
            hit_sl = (direction == 1 and row['low'] <= sl) or (direction == -1 and row['high'] >= sl)
            hit_tp = (direction == 1 and row['high'] >= tp) or (direction == -1 and row['low'] <= tp)
            w1_flip = (direction == 1 and row['w1_close'] < row['w1_ema']) or \
                      (direction == -1 and row['w1_close'] > row['w1_ema'])

            exit_price = None
            if hit_sl:
                exit_price = sl
            elif hit_tp:
                exit_price = tp
            elif w1_flip:
                exit_price = row['close']

            if exit_price is not None:
                # P&L in normalised dollars = (price move / stop distance) * risk_dollars
                stop_distance = abs(entry_price - sl)
                price_move = direction * (exit_price - entry_price)
                pnl = (price_move / stop_distance) * risk_dollars
                trades.append(Trade(
                    entry_time=entry_time, exit_time=ts,
                    direction=direction,
                    entry_price=entry_price, exit_price=exit_price,
                    pnl=pnl, bars_held=0,
                ))
                in_trade = False
                prev = row
                continue

        # ── Entry logic ──
        if not in_trade:
            w1_bullish = row['w1_close'] > row['w1_ema']
            w1_bearish = row['w1_close'] < row['w1_ema']

            cross_up = (prev['close'] < prev['ema']) and (row['close'] > row['ema'])
            cross_dn = (prev['close'] > prev['ema']) and (row['close'] < row['ema'])

            if w1_bullish and cross_up:
                direction = 1
                entry_price = row['close']
                entry_time = ts
                sl = entry_price - stop_atr_mult * row['atr']
                tp = entry_price + target_atr_mult * row['atr']
                risk_dollars = NOTIONAL_RISK
                in_trade = True
            elif w1_bearish and cross_dn:
                direction = -1
                entry_price = row['close']
                entry_time = ts
                sl = entry_price + stop_atr_mult * row['atr']
                tp = entry_price - target_atr_mult * row['atr']
                risk_dollars = NOTIONAL_RISK
                in_trade = True

        prev = row

    return trades


# ─── BASKET DRIVER ─────────────────────────────────────────────────────────────

def basket_strategy_factory(symbol_data_map: dict):
    """
    Returns a strategy_fn(data) -> StrategyResult that runs the per-symbol logic
    on each symbol in the basket and POOLS the trades chronologically.

    The validation harness expects strategy_fn(data) -> StrategyResult with a single
    DataFrame, but the harness's walk_forward_split slices on time. So we slice each
    symbol's data the same way externally and pass an "anchor" DataFrame for the split.
    """
    # The "anchor" is the first symbol's H1 data — used by the harness's split.
    # Inside the strategy_fn we ignore the anchor and re-slice each symbol by the
    # anchor's date range.
    def strategy_fn(anchor_data: pd.DataFrame) -> StrategyResult:
        start = anchor_data.index[0]
        end = anchor_data.index[-1]
        all_trades = []
        for symbol, h1 in symbol_data_map.items():
            sliced = h1.loc[start:end]
            if len(sliced) < 100:
                continue
            trades = _run_single_symbol(sliced, symbol)
            all_trades.extend(trades)
        # Sort chronologically so it looks like a single time-ordered trade stream
        all_trades.sort(key=lambda t: t.entry_time)
        return StrategyResult(trades=all_trades, name="d1_w1_momentum_basket")
    return strategy_fn


# ─── RUN VALIDATION ───────────────────────────────────────────────────────────

def load_symbol_data(symbols: list, data_dir: str) -> dict:
    """Load H1 CSV data for each symbol from data_dir. Returns dict of symbol -> DataFrame."""
    symbol_data = {}
    failed = []
    for sym in symbols:
        try:
            df = load_mt5_csv_pair(data_dir, sym)
            symbol_data[sym] = df
            print(f"  {sym}: {len(df)} H1 bars  ({df.index[0].date()} to {df.index[-1].date()})")
        except Exception as e:
            print(f"  {sym}: FAILED -- {e}")
            failed.append(sym)
    if failed:
        print(f"  WARNING: {len(failed)} symbol(s) failed: {failed}")
    return symbol_data


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Run D1/W1 momentum basket strategy validation')
    parser.add_argument('--start', default='2015-01-01', help='Start date YYYY-MM-DD')
    parser.add_argument('--end',   default='2024-12-31', help='End date YYYY-MM-DD')
    parser.add_argument('--data-dir', default=DATA_DIR, help='Directory containing H1 CSV files')
    parser.add_argument('--label', default='Iteration', help='Label for this run (e.g. "Iteration 3")')
    # Strategy parameters (overridable per iteration)
    parser.add_argument('--w1-ema',    type=int,   default=12,  help='W1 EMA period')
    parser.add_argument('--d1-ema',    type=int,   default=20,  help='D1 EMA period')
    parser.add_argument('--atr',       type=int,   default=14,  help='ATR period')
    parser.add_argument('--sl-mult',   type=float, default=2.0, help='Stop ATR multiplier')
    parser.add_argument('--tp-mult',   type=float, default=3.0, help='Target ATR multiplier')
    parser.add_argument('--num-trials',type=int,   default=9,   help='Number of prior trials (for Deflated Sharpe)')
    args = parser.parse_args()

    START = datetime.strptime(args.start, '%Y-%m-%d')
    END   = datetime.strptime(args.end,   '%Y-%m-%d')

    SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY']

    print(f"=== {args.label} ===")
    print(f"Params: W1_EMA={args.w1_ema}, D1_EMA={args.d1_ema}, ATR={args.atr}, "
          f"SL={args.sl_mult}x, TP={args.tp_mult}x")
    print(f"Period: {START.date()} to {END.date()}")
    print(f"Data dir: {args.data_dir}")
    print()

    symbol_data = load_symbol_data(SYMBOLS, args.data_dir)

    if not symbol_data:
        print("No symbols loaded. Aborting.")
        sys.exit(1)

    # Slice to requested date range
    for sym in list(symbol_data.keys()):
        symbol_data[sym] = symbol_data[sym].loc[START:END]
        if len(symbol_data[sym]) < 100:
            print(f"  {sym}: insufficient bars after date filter, dropping")
            del symbol_data[sym]

    if not symbol_data:
        print("No data after date filtering. Aborting.")
        sys.exit(1)

    # Rebuild factory with custom parameters
    def param_strategy_factory(sym_data):
        def strategy_fn(anchor_data: pd.DataFrame) -> StrategyResult:
            start = anchor_data.index[0]
            end   = anchor_data.index[-1]
            all_trades = []
            for symbol, h1 in sym_data.items():
                sliced = h1.loc[start:end]
                if len(sliced) < 100:
                    continue
                trades = _run_single_symbol(
                    sliced, symbol,
                    w1_ema_period=args.w1_ema,
                    d1_ema_period=args.d1_ema,
                    atr_period=args.atr,
                    stop_atr_mult=args.sl_mult,
                    target_atr_mult=args.tp_mult,
                )
                all_trades.extend(trades)
            all_trades.sort(key=lambda t: t.entry_time)
            return StrategyResult(trades=all_trades, name="d1_w1_momentum_basket")
        return strategy_fn

    anchor_data = symbol_data[list(symbol_data.keys())[0]]
    strategy_fn = param_strategy_factory(symbol_data)

    config = GateConfig(
        min_oos_trades=30,
        bootstrap_percentile=95.0,
        deflated_sharpe_pvalue=0.007,
        max_is_oos_degradation=0.30,
        n_resamples=10000,
        num_prior_trials=args.num_trials,
    )

    verdict = run_validation(strategy_fn, anchor_data, config=config)

    print()
    if verdict.passed:
        print(">>> PASSED")
        sys.exit(0)
    else:
        print(">>> FAILED")
        sys.exit(1)
