"""
London Session Open Breakout Strategy
======================================
Documented FX regularity: London open creates directional momentum as
institutional flow enters. Multi-decade replication support in academic
literature for EURUSD, GBPUSD, USDJPY.

Logic:
  - Asian range: bars 00:00-07:00 GMT define the overnight consolidation zone
  - Entry: first H1 bar that CLOSES above Asian high (long) or below Asian low
    (short) between 07:00-10:00 GMT (London open window)
  - Entry price: Asian high/low (range boundary)
  - Stop: opposite side of Asian range (stop = range width)
  - Target: TP multiplier x Asian range
  - Exit: SL, TP, or end-of-day (17:00 GMT), whichever comes first
  - Optional trend filter: W1 close > W1 EMA for longs, < for shorts

Parameters (testable via grid):
  --tp-mult     : target as multiple of Asian range (1.0, 1.5, 2.0, 2.5, 3.0)
  --trend-filter: 0=no filter, 1=W1 EMA trend filter
  --w1-ema      : W1 EMA period for trend filter (10, 20, 26)
  --min-range   : minimum Asian range in pips to take trade (skip choppy days)
  --max-range   : maximum Asian range in pips (skip gap days)
"""

import sys
import os
from datetime import datetime
import numpy as np
import pandas as pd
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (
    StrategyResult, Trade, run_validation, GateConfig,
    load_mt5_csv_pair
)

DATA_DIR = os.environ.get(
    'DATA_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _run_single_symbol(h1: pd.DataFrame, symbol: str,
                       tp_mult: float = 1.5,
                       use_trend_filter: bool = True,
                       w1_ema_period: int = 20,
                       min_range_pips: float = 10.0,
                       max_range_pips: float = 100.0,
                       asian_start: int = 0,
                       asian_end: int = 7,
                       london_start: int = 7,
                       london_end: int = 10,
                       eod_exit_hour: int = 17) -> list:

    is_jpy = 'JPY' in symbol.upper()
    pip = 0.01 if is_jpy else 0.0001
    NOTIONAL_RISK = 100.0  # normalised $100 risk per trade

    h1 = h1.copy()

    # Build W1 trend filter
    if use_trend_filter:
        w1 = h1.resample('1W').agg({'open':'first','high':'max','low':'min',
                                     'close':'last','volume':'sum'}).dropna()
        w1['w1_ema'] = ema(w1['close'], w1_ema_period)
        h1['w1_ema'] = w1['w1_ema'].reindex(h1.index, method='ffill')
        h1['w1_close'] = w1['close'].reindex(h1.index, method='ffill')
        h1 = h1.dropna(subset=['w1_ema'])

    h1_arr = h1.reset_index()  # work with integer positions for speed
    h1_idx = pd.DatetimeIndex(h1_arr['time'])
    h1_arr.index = range(len(h1_arr))

    trades = []
    dates = sorted(set(h1_idx.date))

    for day in dates:
        # Asian session bars for this day
        asian_mask = (
            (h1_idx.date == day) &
            (h1_idx.hour >= asian_start) &
            (h1_idx.hour < asian_end)
        )
        asian_pos = np.where(asian_mask)[0]
        if len(asian_pos) < 3:
            continue

        asian_high = h1_arr.loc[asian_pos, 'high'].max()
        asian_low  = h1_arr.loc[asian_pos, 'low'].min()
        asian_range = asian_high - asian_low
        range_pips  = asian_range / pip

        if range_pips < min_range_pips or range_pips > max_range_pips:
            continue

        # London open window
        london_mask = (
            (h1_idx.date == day) &
            (h1_idx.hour >= london_start) &
            (h1_idx.hour < london_end)
        )
        london_pos = np.where(london_mask)[0]
        if len(london_pos) == 0:
            continue

        # Trend filter: use last Asian bar's values
        if use_trend_filter and 'w1_ema' in h1_arr.columns:
            ref = h1_arr.loc[asian_pos[-1]]
            w1_close = ref.get('w1_close', ref['close'])
            w1_ema_v = ref.get('w1_ema', ref['close'])
            allow_long  = w1_close > w1_ema_v
            allow_short = w1_close < w1_ema_v
        else:
            allow_long = allow_short = True

        traded_today = False
        for lp in london_pos:
            if traded_today:
                break
            bar = h1_arr.loc[lp]
            direction = 0
            entry_price = 0.0
            sl = 0.0
            tp = 0.0

            if allow_long and bar['close'] > asian_high:
                direction   = 1
                entry_price = asian_high
                sl          = asian_low
                tp          = entry_price + tp_mult * asian_range
            elif allow_short and bar['close'] < asian_low:
                direction   = -1
                entry_price = asian_low
                sl          = asian_high
                tp          = entry_price - tp_mult * asian_range

            if direction == 0:
                continue

            stop_dist = abs(entry_price - sl)
            if stop_dist <= 0:
                continue

            entry_time = bar['time']

            # Scan forward for SL/TP/EOD exit
            eod_mask = (
                (h1_idx.date == day) &
                (h1_idx.hour >= eod_exit_hour)
            )
            # Look at bars after entry until eod or next day
            forward_mask = h1_idx > entry_time
            next_day_mask = h1_idx.date > day
            scan_until = np.where(next_day_mask)[0]
            scan_end = scan_until[0] if len(scan_until) > 0 else len(h1_arr)
            scan_pos = np.where(forward_mask)[0]
            scan_pos = scan_pos[scan_pos < scan_end]

            exit_price = None
            exit_time  = None

            for fp in scan_pos:
                fbar = h1_arr.loc[fp]
                ft   = fbar['time']

                # EOD exit
                if ft.hour >= eod_exit_hour:
                    exit_price = fbar['open']
                    exit_time  = ft
                    break

                if direction == 1:
                    if fbar['low'] <= sl:
                        exit_price = sl
                        exit_time  = ft
                        break
                    if fbar['high'] >= tp:
                        exit_price = tp
                        exit_time  = ft
                        break
                else:
                    if fbar['high'] >= sl:
                        exit_price = sl
                        exit_time  = ft
                        break
                    if fbar['low'] <= tp:
                        exit_price = tp
                        exit_time  = ft
                        break

            if exit_price is None or exit_time is None:
                continue

            price_move = direction * (exit_price - entry_price)
            pnl = (price_move / stop_dist) * NOTIONAL_RISK

            trades.append(Trade(
                entry_time=entry_time, exit_time=exit_time,
                direction=direction,
                entry_price=entry_price, exit_price=exit_price,
                pnl=pnl, bars_held=0
            ))
            traded_today = True

    return trades


def load_symbol_data(symbols, data_dir):
    symbol_data = {}
    for sym in symbols:
        try:
            df = load_mt5_csv_pair(data_dir, sym)
            symbol_data[sym] = df
            print(f"  {sym}: {len(df)} H1 bars  ({df.index[0].date()} to {df.index[-1].date()})")
        except Exception as e:
            print(f"  {sym}: FAILED -- {e}")
    return symbol_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='London session breakout validation')
    parser.add_argument('--start',        default='2015-01-01')
    parser.add_argument('--end',          default='2024-12-31')
    parser.add_argument('--data-dir',     default=DATA_DIR)
    parser.add_argument('--label',        default='London Breakout')
    parser.add_argument('--tp-mult',      type=float, default=1.5,  help='TP as x times Asian range')
    parser.add_argument('--trend-filter', type=int,   default=1,    help='1=W1 trend filter, 0=none')
    parser.add_argument('--w1-ema',       type=int,   default=20,   help='W1 EMA period')
    parser.add_argument('--min-range',    type=float, default=10.0, help='Min Asian range (pips)')
    parser.add_argument('--max-range',    type=float, default=100.0,help='Max Asian range (pips)')
    parser.add_argument('--num-trials',   type=int,   default=9)
    args = parser.parse_args()

    START = datetime.strptime(args.start, '%Y-%m-%d')
    END   = datetime.strptime(args.end,   '%Y-%m-%d')
    SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY']

    print(f"=== {args.label} ===")
    print(f"Params: TP={args.tp_mult}x  TrendFilter={'ON' if args.trend_filter else 'OFF'}  "
          f"W1_EMA={args.w1_ema}  RangePips={args.min_range}-{args.max_range}")
    print(f"Period: {START.date()} to {END.date()}")
    print()

    symbol_data = load_symbol_data(SYMBOLS, args.data_dir)
    if not symbol_data:
        print("No symbols loaded.")
        sys.exit(1)

    for sym in list(symbol_data.keys()):
        symbol_data[sym] = symbol_data[sym].loc[START:END]
        if len(symbol_data[sym]) < 100:
            del symbol_data[sym]

    if not symbol_data:
        print("No data after filtering.")
        sys.exit(1)

    def strategy_fn(anchor_data: pd.DataFrame) -> StrategyResult:
        start = anchor_data.index[0]
        end   = anchor_data.index[-1]
        all_trades = []
        for symbol, h1 in symbol_data.items():
            sliced = h1.loc[start:end].copy()
            if len(sliced) < 100:
                continue
            t = _run_single_symbol(
                sliced, symbol,
                tp_mult=args.tp_mult,
                use_trend_filter=bool(args.trend_filter),
                w1_ema_period=args.w1_ema,
                min_range_pips=args.min_range,
                max_range_pips=args.max_range,
            )
            all_trades.extend(t)
        all_trades.sort(key=lambda x: x.entry_time)
        return StrategyResult(trades=all_trades, name='london_breakout')

    anchor_data = symbol_data[list(symbol_data.keys())[0]]

    config = GateConfig(
        min_oos_trades=100,
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
