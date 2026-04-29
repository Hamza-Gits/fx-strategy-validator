# GBPUSD London Breakout Strategy & Expert Advisor

**Status:** ✅ EA v4 council-locked — ready for MT5 backtest & The5ers prop firm deployment
**Last updated:** 2026-04-28

---

## What is this? (For complete beginners)

This is an **automated trading robot** (Expert Advisor / EA) for the MetaTrader 5 platform. It trades the GBP/USD currency pair using a strategy called the **"London Breakout"**.

In plain English:
- The robot watches what GBP/USD does during the quiet Asian trading hours (midnight to 7 AM London time)
- When London opens at 7 AM, traders flood in. Volume and volatility spike.
- If the price breaks **above** the Asian high → robot buys (long)
- If the price breaks **below** the Asian low → robot sells (short)
- The robot exits at a profit (1.5× the Asian range) or at a loss (the opposite side of the range), or at 5 PM London time (end of day) — whichever comes first
- Each trade lasts a few hours, never overnight

**One trade per day, maximum.** No scalping. No high-frequency anything. No martingale, grid, or doubling-down.

---

## Why does this make money?

A professional FX market has measurable, repeating patterns. The London open is the highest-volume moment in the FX day. Big institutional flows during this window often punch through the overnight range and then continue in that direction for the rest of the morning. The "breakout" of the Asian range is a tradeable signal — not always, but often enough.

The hard math:
- **Win rate:** ~56% (the robot wins more trades than it loses)
- **Reward-to-risk:** ~1.5 to 1 (each win pays 1.5× what each loss costs)
- **Expected value per trade:** ~+0.4% × risk (positive expectancy)

Over hundreds of trades, this compounds. Over thousands of trades, it compounds *significantly*.

---

## Strategy summary (one-liner)

> Trade the H1 close that breaks the Asian range (00:00–07:00 GMT) during the London window (07:00–10:00 GMT), filtered by the W1 EMA-26 trend, with TP at 1.5× Asian range and SL at the opposite range boundary. Force-close at 17:00 GMT.

---

## Validation: Is the edge real or random luck?

We ran every test the academic literature recommends, plus a few extra. Results below.

| Test | Result | What it means |
|---|---|---|
| **Out-of-sample Profit Factor** | 1.733 | For every $1 lost, $1.73 won — on data the strategy was NOT optimised on |
| **DSR (Deflated Sharpe Ratio)** | p < 0.0005 | Out of 2,000 random permutations, NONE underperformed real strategy. Edge is statistically significant |
| **Bootstrap (Period A)** | 99%+ | 99% of resampled trade sequences showed positive expectancy |
| **Bootstrap (Period B)** | 99.6%+ | Edge holds in 2021–2024 (a different market regime than 2015–2020) |
| **Walk-forward** | Pass | 70/30 in-sample/out-of-sample split passed both periods |
| **Configs tested** | 180 | Robust optimisation grid; only 55 passed all gates |

In plain English: **the edge is not luck**. It survived tests designed specifically to detect overfitting and false signals.

---

## Backtest results — $25,000 personal account

Our Python backtest replays every trade from 2015–2024 GBPUSD H1 data with realistic costs (1.0 pip round-trip — The5ers actual is 0.9–1.3 pips).

| Metric | Value |
|---|---|
| Starting equity | $25,000 |
| Final equity (10y) | **$197,200** |
| CAGR | **23.1%** |
| Time to 2x ($50k) | **2.67 years** |
| Time to 3x ($75k) | 4.85 years |
| Max drawdown | **19.1%** |
| Total trades (10y) | 667 (~67/year) |
| Win rate | 56% |
| Profit factor (live-cost) | 1.535 |

This uses **Iter 1** sizing (council pick): 0.5% risk for 30 days, then 1.0% for 60 days, then 1.5% thereafter. See [ITERATION_REPORT.md](ITERATION_REPORT.md) for all 9 iterations tested and why the council chose Iter 1.

---

## Quick start: Run the EA on MT5

1. Open MetaTrader 5
2. **File → Open Data Folder** → `MQL5/Experts/`
3. Copy `mql5_ea/LondonBreakout_v5.mq5` here
4. In MT5: Navigator (Ctrl+N) → Experts → right-click `LondonBreakout_v4` → Compile
5. **View → Strategy Tester** (Ctrl+R)
6. Symbol: GBPUSD, Timeframe: H1, **Date Range: 2014.11.01 – 2024.12.31** (start 2 months early to seed the W1 EMA), Initial Deposit: 25000, Leverage: 1:100
7. Click Start

Full guide: [mql5_ea/HOW_TO_BACKTEST.md](mql5_ea/HOW_TO_BACKTEST.md)

---

## The5ers prop firm settings

Want to use this on a [The5ers](https://the5ers.com) $10k evaluation? Change these inputs:

| Input | Value | Why |
|---|---|---|
| `InpHardHalt` | `true` | Permanent halt if DD exceeded — failing the evaluation is final anyway |
| `InpTrailingDDPct` | `4.0` | The5ers max total DD is 4% |
| `InpDailyLossLimitPct` | `3.0` | Conservative within their daily rules |
| `InpUseProgressiveRisk` | `false` | Flat risk during evaluation |
| `InpRiskPercent` | `0.5` | $50 risk per trade on $10k account |

Expected time to +8% target (their pass rule): ~6–8 months. The5ers has no time limit on evaluations.

**Compliance:** This EA does not violate any of The5ers' 13 prohibited practices. See [docs/the5ers_compliance.md](docs/the5ers_compliance.md) for the full audit.

---

## File map

| File | Purpose |
|---|---|
| `mql5_ea/LondonBreakout_v5.mq5` | **The EA** — copy this to your MT5 |
| `mql5_ea/HOW_TO_BACKTEST.md` | Step-by-step MT5 backtest guide |
| `mql5_ea/ARCHITECTURE.md` | EA design specification |
| `STRATEGY_REPORT.md` | Full 13-section research report |
| `ITERATION_REPORT.md` | Position sizing — all 9 iterations + council verdict |
| `CONTEXT.md` | Master session resume file (for AI continuity) |
| `docs/the5ers_compliance.md` | Prohibited-practices audit |
| `iterate_to_target.py` | Python iterator (reproduces all backtest results) |
| `generate_trade_excel.py` | Generates the full Excel of all trades |
| `results/trades_all.xlsx` | **Full Excel** — every trade with 22 columns |
| `results/iteration_to_target.json` | All 9 iteration results (numerical) |
| `results/gbpusd_top.json` | Top 5 validated parameter sets |
| `data/GBPUSD_H1_*.csv` | Historical price data (10 years H1) |
| `validation_harness/` | Python backtest engine |

---

## v4 changes (over v3.2)

| Change | Why |
|---|---|
| **Soft recovery halt** replaces permanent halt | The v3.2 EA stopped trading permanently after one bad stretch (2018–2019 flatline). v4 pauses for 30 days at 0.5% risk after a DD breach, then resets the peak watermark and resumes. |
| **`InpHardHalt` toggle** | True permanent halt available for prop firm mode (The5ers 4% rule). False for personal account (soft recovery). |
| **`InpTrailingDDPct` default 15 → 25** | Python max DD on Iter 1 is 19.1%; 25% gives margin without false triggers. |
| **`InpRecoveryRiskPct`** | Tunable risk during the 30-day recovery period (default 0.5%). |
| **CSV columns expanded** | Now logs phase, asian range, recovery flag per trade for live PF tracking. |

---

## Reading order for new contributors

1. This README (you're here)
2. [CONTEXT.md](CONTEXT.md) — project state and version history
3. [STRATEGY_REPORT.md](STRATEGY_REPORT.md) — research results
4. [ITERATION_REPORT.md](ITERATION_REPORT.md) — sizing decisions
5. [mql5_ea/HOW_TO_BACKTEST.md](mql5_ea/HOW_TO_BACKTEST.md) — run it yourself
6. `mql5_ea/LondonBreakout_v5.mq5` — the EA source

---

## Disclaimer

This is a research project. Past performance does not guarantee future results. Backtest results assume retail execution at 1.0 pip round-trip cost; live trading may underperform by 20–40% due to slippage, regime changes, and broker-specific spread widening at the London open. Use at your own risk. Test on demo before live.
