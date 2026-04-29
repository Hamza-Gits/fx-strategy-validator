"""
Generate the full trade Excel spreadsheet from the Python backtest.

Reproduces the Iter 1 (council pick) backtest and writes:
  results/trades_all.xlsx with 4 sheets:
    1. All Trades — full ledger (20+ columns)
    2. Monthly Summary — P&L by month
    3. Annual Summary — P&L by year
    4. Iteration Comparison — all 9 sizing rules side by side
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'validation_harness'))

from datetime import datetime
import numpy as np
import pandas as pd
from strategy_london_breakout import _run_single_symbol
from harness import load_mt5_csv_pair

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
OUT_PATH = os.path.join(os.path.dirname(__file__), 'results', 'trades_all.xlsx')

STRATEGY = dict(
    tp_mult=1.5, use_trend_filter=True, w1_ema_period=26,
    min_range_pips=15.0, max_range_pips=60.0,
    asian_start=0, asian_end=7, london_start=7, london_end=10, eod_exit_hour=17,
)
COST_PIPS = 1.0
PIP_SIZE = 0.0001  # GBPUSD


def get_phase(days_elapsed):
    if days_elapsed < 30: return 1
    elif days_elapsed < 90: return 2
    else: return 3


def get_risk_pct(days_elapsed):
    """Iter 1 — council pick"""
    if days_elapsed < 30: return 0.5
    elif days_elapsed < 90: return 1.0
    else: return 1.5


def build_full_trade_log(trades_raw, starting_equity):
    """Replay trades with Iter 1 sizing, capture every detail."""
    rows = []
    equity = starting_equity
    peak_equity = starting_equity
    start_date = trades_raw[0].entry_time

    for i, t in enumerate(trades_raw):
        days_elapsed = (t.entry_time - start_date).days
        phase = get_phase(days_elapsed)
        risk_pct = get_risk_pct(days_elapsed)

        # Recover stop distance
        move = abs(t.entry_price - t.exit_price)
        if t.pnl != 0:
            stop_dist = move / abs(t.pnl) * 100
        else:
            stop_dist = move
        stop_pips = stop_dist / PIP_SIZE
        if stop_pips < 5 or stop_pips > 200:
            stop_pips = 35

        risk_dollars = equity * (risk_pct / 100.0)
        lots = max(0.01, risk_dollars / (stop_pips * 10.0))

        # PnL scaled by actual risk
        actual_pnl = t.pnl * (risk_dollars / 100.0)
        cost_dollars = lots * (COST_PIPS * 10.0)
        actual_pnl -= cost_dollars

        equity_before = equity
        equity += actual_pnl
        if equity > peak_equity:
            peak_equity = equity
        dd_pct = (peak_equity - equity) / peak_equity * 100

        # Direction inferred: long if entry_price < exit_price for winning, etc.
        # Actually: use sign of pnl + relative price levels
        direction = "LONG" if (t.exit_price > t.entry_price and t.pnl > 0) or \
                              (t.exit_price < t.entry_price and t.pnl < 0) else "SHORT"

        # Exit reason inference
        if abs(t.pnl - 150) < 5:  # ~1.5x risk → TP
            exit_reason = "TP"
        elif abs(t.pnl - (-100)) < 5:  # ~-1x risk → SL
            exit_reason = "SL"
        else:
            exit_reason = "EOD/Other"

        # Range pips (Asian range was the stop_dist for the breakout)
        asian_range_pips = stop_pips

        rows.append({
            'Trade #': i + 1,
            'Entry Date/Time (GMT)': t.entry_time,
            'Exit Date/Time (GMT)': t.exit_time,
            'Symbol': 'GBPUSD',
            'Direction': direction,
            'Phase': phase,
            'Risk %': risk_pct,
            'Entry Price': round(t.entry_price, 5),
            'Exit Price': round(t.exit_price, 5),
            'Stop Distance (pips)': round(stop_pips, 1),
            'Asian Range (pips)': round(asian_range_pips, 1),
            'Lot Size': round(lots, 2),
            'Risk ($)': round(risk_dollars, 2),
            'Cost ($)': round(cost_dollars, 2),
            'P&L ($)': round(actual_pnl, 2),
            'P&L (%)': round(actual_pnl / equity_before * 100, 4),
            'Equity Before': round(equity_before, 2),
            'Equity After': round(equity, 2),
            'Peak Equity': round(peak_equity, 2),
            'Drawdown (%)': round(dd_pct, 2),
            'Exit Reason': exit_reason,
            'Days Elapsed': days_elapsed,
        })
    return pd.DataFrame(rows)


def build_monthly_summary(df):
    df = df.copy()
    df['Month'] = pd.to_datetime(df['Entry Date/Time (GMT)']).dt.to_period('M')
    monthly = df.groupby('Month').agg(
        Trades=('Trade #', 'count'),
        Wins=('P&L ($)', lambda x: (x > 0).sum()),
        Losses=('P&L ($)', lambda x: (x < 0).sum()),
        Total_PnL=('P&L ($)', 'sum'),
        Avg_PnL=('P&L ($)', 'mean'),
        Best_Trade=('P&L ($)', 'max'),
        Worst_Trade=('P&L ($)', 'min'),
        End_Equity=('Equity After', 'last'),
    ).reset_index()
    monthly['Win Rate %'] = (monthly['Wins'] / monthly['Trades'] * 100).round(1)
    monthly['Month'] = monthly['Month'].astype(str)
    return monthly


def build_annual_summary(df):
    df = df.copy()
    df['Year'] = pd.to_datetime(df['Entry Date/Time (GMT)']).dt.year
    annual = df.groupby('Year').agg(
        Trades=('Trade #', 'count'),
        Wins=('P&L ($)', lambda x: (x > 0).sum()),
        Total_PnL=('P&L ($)', 'sum'),
        Avg_PnL=('P&L ($)', 'mean'),
        Max_DD=('Drawdown (%)', 'max'),
        Start_Equity=('Equity Before', 'first'),
        End_Equity=('Equity After', 'last'),
    ).reset_index()
    annual['Win Rate %'] = (annual['Wins'] / annual['Trades'] * 100).round(1)
    annual['Annual Return %'] = ((annual['End_Equity'] / annual['Start_Equity'] - 1) * 100).round(2)
    return annual


def load_iteration_results():
    """Pull the iteration_to_target.json we just produced."""
    p = os.path.join(os.path.dirname(__file__), 'results', 'iteration_to_target.json')
    if not os.path.exists(p):
        return pd.DataFrame()
    data = json.load(open(p))
    rows = []
    for it in data.get('iterations', []):
        rows.append({
            'Iteration': it.get('label', ''),
            'Final Equity ($)': round(it.get('final_equity', 0), 0),
            'Total Return (%)': round(it.get('total_return_pct', 0), 1),
            'CAGR (%)': round(it.get('cagr_pct', 0), 2),
            'Trades': it.get('n_trades', 0),
            'Win Rate (%)': round(it.get('win_rate_pct', 0), 1),
            'Profit Factor': round(it.get('profit_factor', 0), 3),
            'Max DD (%)': round(it.get('max_dd_pct', 0), 2),
            'Time to 2x (years)': it.get('time_to_2x_years'),
            'Time to 3x (years)': it.get('time_to_3x_years'),
            'Council Verdict': 'CHOSEN' if 'ITER 1' in it.get('label', '') else '',
        })
    return pd.DataFrame(rows)


def main():
    print("Loading GBPUSD H1 2015-2024...")
    df = load_mt5_csv_pair(DATA_DIR, 'GBPUSD')
    df = df.loc[datetime(2015,1,1):datetime(2024,12,31)]
    print(f"  Loaded {len(df)} bars")

    print("Running strategy with locked params...")
    trades_raw = _run_single_symbol(df, 'GBPUSD', **STRATEGY)
    print(f"  {len(trades_raw)} trades generated")

    print("Building full trade log (Iter 1 — council pick)...")
    trade_df = build_full_trade_log(trades_raw, starting_equity=25000)
    print(f"  {len(trade_df)} rows")

    monthly = build_monthly_summary(trade_df)
    annual = build_annual_summary(trade_df)
    iter_compare = load_iteration_results()

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    print(f"Writing {OUT_PATH}...")
    with pd.ExcelWriter(OUT_PATH, engine='openpyxl') as xw:
        trade_df.to_excel(xw, sheet_name='All Trades', index=False)
        monthly.to_excel(xw, sheet_name='Monthly Summary', index=False)
        annual.to_excel(xw, sheet_name='Annual Summary', index=False)
        if not iter_compare.empty:
            iter_compare.to_excel(xw, sheet_name='Iteration Comparison', index=False)

    print(f"  Done. {len(trade_df)} trades, {len(monthly)} months, {len(annual)} years.")
    print(f"  Final equity: ${trade_df['Equity After'].iloc[-1]:,.2f}")
    print(f"  Max DD: {trade_df['Drawdown (%)'].max():.2f}%")


if __name__ == "__main__":
    main()
