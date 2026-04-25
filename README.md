# FX Strategy Validator

Autonomous walk-forward validation pipeline for algorithmic trading strategies.

## Structure

```
/data/                        ← H1 OHLCV CSV files (MT5 export format)
  EURUSD_H1_2013-2020.csv
  EURUSD_H1_2021-2025.csv
  GBPUSD_H1_2013-2020.csv
  GBPUSD_H1_2021-2025.csv
  USDJPY_H1_2013-2020.csv
  USDJPY_H1_2021-2025.csv

/validation_harness/
  harness.py                  ← Core validation pipeline (bootstrap, deflated Sharpe)
  strategy_template.py        ← D1/W1 momentum strategy (parametric)

/results/
  iteration_log.md            ← Auto-generated per-iteration results log
  best_params.json            ← Written when a passing config is found

run_loop.py                   ← Autonomous iteration runner (up to 100 iterations)
Council.md                    ← Manual parameter review guide
```

## Setup

```bash
pip install numpy pandas scipy openpyxl
```

## Run the iteration loop

```bash
# Uses default data dir (./data) and runs up to 100 iterations
python run_loop.py

# Custom data location
python run_loop.py --data-dir "C:/Users/hamza/Downloads/Ai projects"

# Resume from iteration 15 (if interrupted)
python run_loop.py --start-from 15

# Cap at 50 iterations
python run_loop.py --max-iter 50
```

## Run a single backtest manually

```bash
python validation_harness/strategy_template.py \
  --start 2015-01-01 --end 2019-12-31 \
  --data-dir "./data" \
  --w1-ema 12 --d1-ema 20 --atr 14 --sl-mult 2.0 --tp-mult 3.0
```

## Validation Gates (must pass both periods)

| Gate | Threshold |
|------|-----------|
| OOS Profit Factor | >= 1.2 implied by bootstrap gate |
| Bootstrap percentile vs null | >= 95% |
| Deflated Sharpe p-value | < 0.007 |
| IS→OOS degradation | < 30% |
| Min OOS trades | >= 30 |

## Current status

See [results/iteration_log.md](results/iteration_log.md) for latest run results.
