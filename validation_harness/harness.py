"""
Strategy Validation Harness
============================
External validation pipeline for any trading strategy candidate.
Sits OUTSIDE the MT5 Strategy Tester to avoid optimizer curve-fit bias.

Pipeline:
  1. Load price data (from MT5 or CSV)
  2. Run candidate strategy function on data, produce trade list
  3. Walk-forward split: in-sample (IS) vs out-of-sample (OOS)
  4. Bootstrap PF distribution on OOS trades
  5. Compute Deflated Sharpe Ratio (Lopez de Prado 2014)
  6. Compare against zero-edge null model
  7. Verdict: PASS or FAIL based on multi-trial-corrected gates

Gates (calibrated for 7 prior failed EA trials):
  - Bootstrap OOS PF must beat 95th percentile of null
  - Deflated Sharpe p-value < 0.007
  - Minimum N = 100 OOS trades (preferably 200+)
  - OOS performance within 30% of IS performance (no severe degradation)
"""

import sys
import os
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

# Fix Windows console encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf-16'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass


# ─── DATA LOADING ─────────────────────────────────────────────────────────────

def load_mt5_data(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Pull OHLCV bars from running MT5 terminal via the MetaTrader5 package."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise ImportError("MetaTrader5 package not installed. Run: pip install MetaTrader5")

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    tf_map = {
        'M1': mt5.TIMEFRAME_M1, 'M5': mt5.TIMEFRAME_M5, 'M15': mt5.TIMEFRAME_M15,
        'M30': mt5.TIMEFRAME_M30, 'H1': mt5.TIMEFRAME_H1, 'H4': mt5.TIMEFRAME_H4,
        'D1': mt5.TIMEFRAME_D1, 'W1': mt5.TIMEFRAME_W1, 'MN1': mt5.TIMEFRAME_MN1,
    }
    if timeframe not in tf_map:
        raise ValueError(f"Unknown timeframe: {timeframe}. Use one of {list(tf_map.keys())}")

    rates = mt5.copy_rates_range(symbol, tf_map[timeframe], start, end)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data returned for {symbol} {timeframe} between {start} and {end}")

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    df.rename(columns={'tick_volume': 'volume'}, inplace=True)
    return df[['open', 'high', 'low', 'close', 'volume']]


def load_csv_data(filepath: str) -> pd.DataFrame:
    """Load OHLCV data from a CSV with columns: time, open, high, low, close, volume."""
    df = pd.read_csv(filepath, parse_dates=['time'])
    df.set_index('time', inplace=True)
    return df


def load_mt5_csv(filepath: str) -> pd.DataFrame:
    """
    Load MT5 native tab-separated export format.
    Columns: <DATE>  <TIME>  <OPEN>  <HIGH>  <LOW>  <CLOSE>  <TICKVOL>  <VOL>  <SPREAD>
    """
    df = pd.read_csv(filepath, sep='\t')
    df.columns = [c.strip('<>').lower() for c in df.columns]
    df['time'] = pd.to_datetime(df['date'] + ' ' + df['time'],
                                format='%Y.%m.%d %H:%M:%S')
    df.set_index('time', inplace=True)
    df.rename(columns={'tickvol': 'volume'}, inplace=True)
    return df[['open', 'high', 'low', 'close', 'volume']].sort_index()


def load_mt5_csv_pair(prefix_path: str, symbol: str) -> pd.DataFrame:
    """
    Load and concatenate two MT5 CSV exports for a symbol:
      {prefix_path}/{SYMBOL}_H1_2013-2020.csv
      {prefix_path}/{SYMBOL}_H1_2021-2025.csv
    Deduplicates and returns a sorted DataFrame.
    """
    import glob
    pattern = os.path.join(prefix_path, f"{symbol}_H1_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No CSV files found matching: {pattern}")
    parts = [load_mt5_csv(f) for f in files]
    df = pd.concat(parts)
    df = df[~df.index.duplicated(keep='first')].sort_index()
    return df


# ─── TRADE STRUCTURE ──────────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    direction: int           # +1 long, -1 short
    entry_price: float
    exit_price: float
    pnl: float               # in account currency
    bars_held: int = 0


@dataclass
class StrategyResult:
    trades: list = field(default_factory=list)
    name: str = "candidate"

    @property
    def pnls(self) -> np.ndarray:
        return np.array([t.pnl for t in self.trades]) if self.trades else np.array([])

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def profit_factor(self) -> float:
        pnls = self.pnls
        if len(pnls) == 0:
            return 0.0
        gp = pnls[pnls > 0].sum()
        gl = abs(pnls[pnls < 0].sum())
        return gp / gl if gl > 0 else float('inf')

    @property
    def sharpe(self) -> float:
        pnls = self.pnls
        if len(pnls) < 2 or pnls.std() == 0:
            return 0.0
        return pnls.mean() / pnls.std() * np.sqrt(252)

    @property
    def win_rate(self) -> float:
        pnls = self.pnls
        return (pnls > 0).mean() if len(pnls) > 0 else 0.0


# ─── BOOTSTRAP & NULL MODEL ───────────────────────────────────────────────────

def bootstrap_pf(pnls: np.ndarray, n_resamples: int = 10000) -> np.ndarray:
    n = len(pnls)
    out = np.empty(n_resamples)
    for i in range(n_resamples):
        s = np.random.choice(pnls, size=n, replace=True)
        gp = s[s > 0].sum()
        gl = abs(s[s < 0].sum())
        out[i] = gp / gl if gl > 0 else 0
    return out


def null_model_pf(pnls: np.ndarray, n_resamples: int = 10000) -> np.ndarray:
    """Zero-edge null: random sign assignment preserving magnitude distribution."""
    abs_p = np.abs(pnls)
    n = len(pnls)
    out = np.empty(n_resamples)
    for i in range(n_resamples):
        signs = np.random.choice([-1, 1], size=n)
        s = abs_p * signs
        gp = s[s > 0].sum()
        gl = abs(s[s < 0].sum())
        out[i] = gp / gl if gl > 0 else 0
    return out


# ─── DEFLATED SHARPE (Lopez de Prado 2014) ────────────────────────────────────

def deflated_sharpe(sr: float, n: int, num_trials: int, skew: float = 0.0,
                    kurt: float = 3.0) -> tuple:
    """
    Deflated Sharpe Ratio: corrects observed Sharpe for multiple-testing bias.

    Args:
        sr: observed Sharpe ratio
        n: number of trades
        num_trials: number of independent strategy variants tested (7 prior EAs)
        skew: trade-return skewness (0 if unknown)
        kurt: trade-return kurtosis (3 = normal)

    Returns:
        (deflated_sr, p_value)
    """
    from scipy.stats import norm

    if num_trials < 1:
        num_trials = 1

    emc = 0.5772156649  # Euler-Mascheroni
    expected_max_sr = (
        (1 - emc) * norm.ppf(1 - 1.0 / num_trials)
        + emc * norm.ppf(1 - 1.0 / (num_trials * np.e))
    )

    sr_std = np.sqrt(
        (1 - skew * sr + ((kurt - 1) / 4.0) * sr**2) / (n - 1)
    )

    if sr_std == 0:
        return 0.0, 1.0

    dsr = (sr - expected_max_sr) / sr_std
    p_value = 1 - norm.cdf(dsr)
    return dsr, p_value


# ─── WALK-FORWARD SPLIT ───────────────────────────────────────────────────────

def walk_forward_split(data: pd.DataFrame, is_fraction: float = 0.7) -> tuple:
    n = len(data)
    split = int(n * is_fraction)
    return data.iloc[:split].copy(), data.iloc[split:].copy()


# ─── VALIDATION GATES ─────────────────────────────────────────────────────────

@dataclass
class GateConfig:
    min_oos_trades: int = 100
    bootstrap_percentile: float = 95.0     # OOS PF must beat this % of null
    deflated_sharpe_pvalue: float = 0.007  # corrected for 7 prior trials
    max_is_oos_degradation: float = 0.30   # OOS PF can drop max 30% from IS PF
    n_resamples: int = 10000
    num_prior_trials: int = 7


@dataclass
class ValidationVerdict:
    passed: bool
    is_pf: float
    oos_pf: float
    oos_n: int
    bootstrap_percentile_achieved: float
    deflated_sharpe: float
    deflated_pvalue: float
    is_oos_degradation: float
    failures: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def run_validation(strategy_fn: Callable[[pd.DataFrame], StrategyResult],
                   data: pd.DataFrame,
                   config: GateConfig = None,
                   verbose: bool = True) -> ValidationVerdict:
    """Run a strategy through the full validation pipeline."""

    if config is None:
        config = GateConfig()

    if verbose:
        print("=" * 60)
        print("  STRATEGY VALIDATION HARNESS")
        print("=" * 60)
        print(f"  Data:          {data.index[0]} to {data.index[-1]}")
        print(f"  Bars:          {len(data)}")
        print(f"  Prior trials:  {config.num_prior_trials}")
        print(f"  Gates:")
        print(f"    Bootstrap OOS PF >= {config.bootstrap_percentile}th percentile of null")
        print(f"    Deflated Sharpe p-value < {config.deflated_sharpe_pvalue}")
        print(f"    OOS trades >= {config.min_oos_trades}")
        print(f"    IS->OOS degradation < {config.max_is_oos_degradation*100:.0f}%")
        print()

    # 1. Walk-forward split
    is_data, oos_data = walk_forward_split(data, is_fraction=0.7)
    if verbose:
        print(f"  IS:  {is_data.index[0].date()} to {is_data.index[-1].date()}")
        print(f"  OOS: {oos_data.index[0].date()} to {oos_data.index[-1].date()}")
        print()

    # 2. Run strategy on both
    is_result = strategy_fn(is_data)
    oos_result = strategy_fn(oos_data)

    is_pf = is_result.profit_factor
    oos_pf = oos_result.profit_factor
    oos_n = oos_result.n

    if verbose:
        print(f"  IS:  {is_result.n} trades, PF={is_pf:.3f}, WR={is_result.win_rate*100:.1f}%")
        print(f"  OOS: {oos_n} trades, PF={oos_pf:.3f}, WR={oos_result.win_rate*100:.1f}%")
        print()

    failures = []
    notes = []

    # GATE 1: Minimum OOS sample size
    if oos_n < config.min_oos_trades:
        failures.append(f"OOS N={oos_n} below minimum {config.min_oos_trades}")

    # GATE 2: IS -> OOS degradation
    degradation = 0.0
    if is_pf > 0:
        degradation = (is_pf - oos_pf) / is_pf
    if degradation > config.max_is_oos_degradation:
        failures.append(
            f"IS->OOS degradation {degradation*100:.1f}% exceeds {config.max_is_oos_degradation*100:.0f}%"
        )

    # GATE 3: Bootstrap percentile vs null
    bootstrap_pct = 0.0
    if oos_n >= 30:  # bootstrap needs minimum sample
        oos_pnls = oos_result.pnls
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            null_dist = null_model_pf(oos_pnls, n_resamples=config.n_resamples)
        bootstrap_pct = (null_dist < oos_pf).mean() * 100
        if verbose:
            print(f"  Bootstrap: OOS PF beats {bootstrap_pct:.1f}% of zero-edge null")
        if bootstrap_pct < config.bootstrap_percentile:
            failures.append(
                f"Bootstrap percentile {bootstrap_pct:.1f}% < required {config.bootstrap_percentile}%"
            )
    else:
        failures.append("OOS sample too small for bootstrap (<30 trades)")

    # GATE 4: Deflated Sharpe
    dsr, p_val = 0.0, 1.0
    if oos_n >= 30:
        try:
            from scipy.stats import skew as sp_skew, kurtosis as sp_kurt
            oos_pnls = oos_result.pnls
            sk = sp_skew(oos_pnls)
            kt = sp_kurt(oos_pnls, fisher=False)
            dsr, p_val = deflated_sharpe(
                oos_result.sharpe, oos_n,
                config.num_prior_trials, skew=sk, kurt=kt
            )
            if verbose:
                print(f"  Deflated Sharpe: {dsr:.3f}  (p={p_val:.4f})")
            if p_val > config.deflated_sharpe_pvalue:
                failures.append(
                    f"Deflated Sharpe p={p_val:.4f} >= {config.deflated_sharpe_pvalue}"
                )
        except ImportError:
            notes.append("scipy not installed -- skipping Deflated Sharpe gate")

    if verbose:
        print()
        print("=" * 60)
        if not failures:
            print("  VERDICT: PASS")
            print("  Strategy clears all validation gates.")
            print("  Safe to port to MQL5 EA.")
        else:
            print("  VERDICT: FAIL")
            print(f"  {len(failures)} gate(s) failed:")
            for f in failures:
                print(f"    - {f}")
            print()
            print("  Recommendation: do not port to MQL5.")
            print("  Iterate on signal logic or pick a different strategy.")
        print("=" * 60)

    return ValidationVerdict(
        passed=(len(failures) == 0),
        is_pf=is_pf,
        oos_pf=oos_pf,
        oos_n=oos_n,
        bootstrap_percentile_achieved=bootstrap_pct,
        deflated_sharpe=dsr,
        deflated_pvalue=p_val,
        is_oos_degradation=degradation,
        failures=failures,
        notes=notes,
    )


# ─── EXAMPLE / SELF-TEST ──────────────────────────────────────────────────────

def _example_random_strategy(data: pd.DataFrame) -> StrategyResult:
    """Random coin-flip 'strategy' for harness self-test. Should FAIL all gates."""
    np.random.seed(42)
    trades = []
    for i in range(0, len(data) - 10, 20):
        if np.random.rand() > 0.5:
            entry = data.iloc[i]
            exit_ = data.iloc[i + 10]
            direction = np.random.choice([-1, 1])
            pnl = direction * (exit_['close'] - entry['close']) * 10000
            trades.append(Trade(
                entry_time=data.index[i],
                exit_time=data.index[i + 10],
                direction=direction,
                entry_price=entry['close'],
                exit_price=exit_['close'],
                pnl=pnl,
                bars_held=10,
            ))
    return StrategyResult(trades=trades, name="random_coinflip")


if __name__ == "__main__":
    print("Running self-test on synthetic random data...")
    print()

    # Generate synthetic price data
    np.random.seed(0)
    n_bars = 5000
    rets = np.random.normal(0, 0.001, n_bars)
    prices = 1.10 * np.exp(np.cumsum(rets))
    data = pd.DataFrame({
        'open': prices,
        'high': prices * 1.001,
        'low': prices * 0.999,
        'close': prices,
        'volume': 1000,
    }, index=pd.date_range('2019-01-01', periods=n_bars, freq='4h'))

    verdict = run_validation(_example_random_strategy, data)
    print()
    print(f"Self-test verdict: {'PASSED (unexpected!)' if verdict.passed else 'FAILED (expected)'}")
