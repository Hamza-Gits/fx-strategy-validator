# Phase C — Deep Validation Workflow

## Status
Battery (Phase B) in progress. This document is prepared for Phase C execution once battery completes.

## Expected Inputs from Phase B
- `diagnostic/strategy_battery_results.csv` with 12 strategies
- Ranked by cost-adjusted OOS PF (descending)
- Expected survivors (PASS): strategies with:
  - OOS N ≥ 100
  - OOS cost-adjusted PF ≥ 1.3
  - Robustness ≥ 0.6
  - Max DD ≤ 15%

## Phase C Execution Flow

### Step 1: Identify Top 3 Survivors
```bash
cd validation_harness
# Extract top 3 non-rejected strategies
python strategy_deep_validate.py --auto-top 3
```

### Step 2: Four Deep Validation Gates
Each of the top 3 strategies undergoes:

1. **Multi-Instrument Cross-Check**
   - Test parameters on: XAUUSD, EURUSD, GBPUSD, USDJPY
   - Gate: ≥2 of 4 instruments with OOS cost-adjusted PF > 1.2
   - Rationale: True edge generalizes across instruments

2. **Regime Split Test**
   - Segment XAUUSD OOS trades by D1-200MA trend (bull/bear/range)
   - Gate: ≥2 of 3 regimes with PF > 1.0
   - Rationale: Edge should not be regime-specific

3. **Monte Carlo Trade Resampling**
   - Bootstrap 1000 trade sequences (with replacement)
   - Gate: 5th percentile final equity > $50k starting equity
   - Rationale: Confirms edge is not just luck

4. **News-Event Filter**
   - Remove trades on NFP Fridays (1-7) and FOMC Tue/Wed (weeks 3-4)
   - Gate: <15% of trades during news windows
   - Rationale: Edge should not depend on news drift

### Step 3: Promotion Decision
Strategies passing ALL 4 gates → Phase D (MQL5 build)
Strategies failing any gate → Archive for future research

## Output Files
- `diagnostic/deep_validation_results.json` — full 4-test results for each candidate
- Console table showing pass/fail summary

## Success Criteria
- ≥1 strategy survives all 4 gates (sufficient for Phase D MQL5 build)
- 0 survivors → return to strategy_lib.py, add 6-12 new candidate strategies, re-run battery

## Data Dependencies
All Phase C tests use existing data:
- `data/XAUUSD_H1_2013-2025.csv` — primary
- `data/EURUSD_H1_2013-2020.csv` + `2021-2025.csv`
- `data/GBPUSD_H1_2013-2020.csv` + `2021-2025.csv`
- `data/USDJPY_H1_2013-2020.csv` + `2021-2025.csv`
(Auto-loaded via load_mt5_csv_pair in strategy_deep_validate.py)

## Estimated Time
- 30-40 minutes for top 3 strategies × 4 tests each
- Sequential execution (each test ~5-10 min)

## Key Implementation Notes
- All tests run OOS data only (not IS) — true out-of-sample robustness
- Cost model applied to all PF calculations
- Tie-break on PF if multiple strategies survive: higher avg_r wins
- Verdict for Phase D: #1 survivor only (highest cost-adj OOS PF + all gate passes)
