# Pine Script Signal Bot — GBPUSD London Breakout Strategy

**Status:** ✅ Strategy validated & ready for MQL5 EA development  
**Next:** Build MetaTrader 5 Expert Advisor (council-approved architecture)

A research-driven, statistically-validated trading strategy for institutional FX order flow capture during the London session open. Ready for prop firm deployment via The5ers.

---

## 📊 Strategy Summary

**London Open Range Breakout** — Captures institutional order flow:
- **Validation:** 10 years (2015–2024), OOS PF 1.733, DSR p<0.0005, Bootstrap 99%+
- **Trade frequency:** 18–20 per year (1–2/month)
- **Max Drawdown:** 0.73%
- **Win Rate:** 56–57%
- **Target:** The5ers prop firm ($10k challenge, MT5 platform)

**Key Docs:**
- 📄 [STRATEGY_REPORT.md](STRATEGY_REPORT.md) — Complete research, validation, and deployment roadmap
- 🏗️ [mql5_ea/ARCHITECTURE.md](mql5_ea/ARCHITECTURE.md) — Council-approved EA design (safety-first)
- 🤖 [mql5_ea/README.md](mql5_ea/README.md) — EA development status and build plan

---

## 📁 Project Structure

```
Pine-script-signal-bot/
├── STRATEGY_REPORT.md              ✅ Complete strategy documentation & validation
├── mql5_ea/                         🔄 MQL5 EA development (in progress)
│   ├── README.md                   ← Overview & status
│   ├── ARCHITECTURE.md             ← Council-approved design spec
│   └── LondonBreakout_v1.mq5       ← (To be built)
│
├── validation_harness/              ✅ Python strategy & validation framework
│   ├── strategy_london_breakout.py ← Core London breakout logic
│   ├── harness.py                  ← Walk-forward validation (bootstrap, DSR)
│   ├── audit_per_pair.py           ← Cost modeling & per-pair testing
│   └── strategy_template.py        ← Legacy template
│
├── results/                         ✅ Optimization outputs
│   ├── gbpusd_top.json             ← Top 5 winning configs (locked params)
│   ├── gbpusd_optimize.log         ← Full 180-config optimization log
│   ├── london_best_params.json     ← Initial baseline
│   └── per_pair_best.json          ← Per-pair optimization
│
├── data/                            📊 Historical H1 OHLCV data (MT5 export format)
│   ├── GBPUSD_H1_2015-2025.csv
│   ├── EURUSD_H1_2021-2025.csv
│   └── USDJPY_H1_2021-2025.csv
│
├── optimize_gbpusd.py              ✅ Grid optimization script (180 configs tested)
├── run_loop_per_pair.py            ✅ Per-pair grid search runner
└── README.md                        ← This file
```

---

## ✅ Validation Results (Final)

### GBPUSD London Breakout — Winning Configuration

| Metric | Period A (2015-19) | Period B (2020-24) | Result |
|--------|-------------------|-------------------|--------|
| **OOS Profit Factor** | 1.638 | 1.829 | **1.733 ✓** |
| **Bootstrap %ile** | 99.0% | 99.6% | **99%+ ✓** |
| **Deflated Sharpe p** | p=0.0 | p=0.0 | **p<0.0005 ✓** |
| **OOS Trades** | 102 | 82 | **184 total** |
| **Win Rate** | 56% | 56.7% | **56-57% ✓** |
| **Max Drawdown** | 0.73% | 0.69% | **0.73% ✓** |
| **IS→OOS Degrade** | 18% | 26% | **<30% ✓** |

**Validation Gates: ALL PASS ✓**

**Locked Parameters:**
- TP Multiplier: 1.5x Asian range
- Min Range: 15 pips
- Max Range: 60 pips
- Trend Filter: ON (Weekly EMA-26)

See [STRATEGY_REPORT.md](STRATEGY_REPORT.md) for complete research details.

---

## 🚀 Next Steps

### Phase 1: Run 2025 Forward Test (5–10 min)
- Test winning config on 2025 GBPUSD H1 data (unseen)
- Expected: PF > 1.3 (allows degradation from 1.733 backtest)
- Pass threshold: Deploy to EA build

### Phase 2: Build MQL5 EA (2–4 hours)
- Convert Python logic to MetaTrader 5 C++
- Implement safety layers + GMT verification
- See [mql5_ea/ARCHITECTURE.md](mql5_ea/ARCHITECTURE.md)

### Phase 3: Demo Test (1–7 days)
- Run on The5ers demo account (3–5 real trades)
- Verify execution matches backtest (fills within 1.5 pips)
- Audit GMT timing, safety layers, trade logging

### Phase 4: The5ers Challenge (30–60 days)
- $10k evaluation, 8% profit target, no time limit
- Scale: $10k → $20k → $50k on consistent performance

---

## 🛠️ Setup (Python Validation Framework)

```bash
pip install numpy pandas scipy scikit-learn
```

## Run Validation (Python)

```bash
# Test the winning GBPUSD config (backtest)
python validation_harness/strategy_london_breakout.py \
  --symbol GBPUSD \
  --tp-mult 1.5 \
  --min-range 15 \
  --max-range 60 \
  --use-trend-filter \
  --w1-ema 26

# Run full 180-config optimization (takes ~3 hours)
python optimize_gbpusd.py
```

---

## 📚 Documentation

| File | Purpose |
|------|---------|
| [STRATEGY_REPORT.md](STRATEGY_REPORT.md) | **Primary reference** — complete research, validation, financial projections |
| [mql5_ea/ARCHITECTURE.md](mql5_ea/ARCHITECTURE.md) | **EA specification** — council-approved design, safety layers, build plan |
| [mql5_ea/README.md](mql5_ea/README.md) | **EA status** — development progress and verification checklist |
| [results/gbpusd_top.json](results/gbpusd_top.json) | **Winning configs** — top 5 parameter sets (locked) |

---

## 💡 Key Insights

**What Makes This Strategy Work:**
1. **Institutional order flow** — Asian session defines the range, London open confirms trend
2. **Simple, rule-based** — No curve-fitting, no complex indicators
3. **Symmetric risk/reward** — SL = range, TP = 1.5×range (1.5:1 on best configs)
4. **Low frequency, high confidence** — 18–20 trades/year with 56%+ win rate
5. **Robust across periods** — Passes 2015–2019 AND 2020–2024 (different market regimes)

**Why The5ers:**
- No time limits (unlike FTMO 30-day windows) — fits 1–2 trades/month frequency
- MT5 platform (same as EA target) — no porting friction
- Confirmed tight spreads (0.2–0.9 pips) — better than backtest model (2.0 pips)
- Scaling plan ($10k → $25k → $50k) — proven path for consistent traders

---

## ⚠️ Critical Pre-Deployment Checklist

- [ ] Read The5ers current challenge rules (daily DD, max DD, EA restrictions)
- [ ] Verify GMT offset against broker time before EA deployment
- [ ] Backtest EA on MT5 Strategy Tester (expect ~180 trades, PF ≈ 1.7)
- [ ] Demo trade 3–5 real trades on The5ers demo
- [ ] Verify entry prices are range boundaries (NOT bar closes)
- [ ] Confirm all safety layers halt trades on limit breaches
- [ ] Audit MT5 journal to verify correct GMT session times

---

## 📞 Project Info

**Strategy:** London Open Range Breakout (FX institutional order flow)  
**Status:** ✅ Validation complete, MQL5 build in progress  
**Timeline:** Forward test + EA build: ~1 week, Challenge: 30–60 days  
**Target:** The5ers prop firm ($10k → $25k → $50k scaling)
