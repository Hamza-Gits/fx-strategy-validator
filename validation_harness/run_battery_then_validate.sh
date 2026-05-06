#!/bin/bash
# Run battery and then automatically run Phase C deep validation on survivors

set -e

cd "$(dirname "$0")/.."

echo "=== Phase B: Strategy Battery ==="
python validation_harness/strategy_battery.py
echo ""

echo "=== Phase C: Deep Validation (Auto-run top 3) ==="
python validation_harness/strategy_deep_validate.py --auto-top 3
echo ""

echo "=== Results ==="
echo "Battery results: diagnostic/strategy_battery_results.csv"
echo "Deep validation: diagnostic/deep_validation_results.json"
wc -l diagnostic/strategy_battery_results.csv 2>/dev/null || echo "(battery CSV not created)"
test -f diagnostic/deep_validation_results.json && echo "Phase C complete" || echo "Phase C not run"
