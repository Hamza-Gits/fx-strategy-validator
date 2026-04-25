import matplotlib
matplotlib.use('Agg')
import sys, os, warnings
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

FILEPATH = r"C:\Users\hamza\Downloads\Ai projects\CLAUDE MT5\Backtest Report's\Backtest report 11\Deals only .xlsx"

with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    raw = pd.read_excel(FILEPATH, header=None)

# Find Deals header row
deals_header_row = None
for i, row in raw.iterrows():
    vals = [str(v) for v in row.values if str(v) != 'nan']
    joined = ' '.join(vals)
    if 'Time' in joined and 'Profit' in joined and 'Direction' in joined:
        deals_header_row = i
        break

print(f"Deals header found at row: {deals_header_row}")
df = raw.iloc[deals_header_row:].copy()
df.columns = raw.iloc[deals_header_row].values
df = df.iloc[1:].reset_index(drop=True)

# Keep only closing deals (these carry the P&L)
df = df[df['Direction'].astype(str).str.strip().str.lower() == 'out']
profits = pd.to_numeric(df['Profit'], errors='coerce').dropna()
profits = profits[profits != 0].values

print(f"Trades loaded:  {len(profits)}")
print(f"Winners:        {(profits > 0).sum()}")
print(f"Losers:         {(profits < 0).sum()}")
win_rate = (profits > 0).sum() / len(profits) * 100
print(f"Win rate:       {win_rate:.1f}%")
gp = profits[profits > 0].sum()
gl = abs(profits[profits < 0].sum())
pf = gp / gl
print(f"Actual PF:      {pf:.4f}")

# Bootstrap resampling
N = 10000
n = len(profits)
pf_boot = np.empty(N)
for i in range(N):
    s = np.random.choice(profits, size=n, replace=True)
    g = s[s > 0].sum()
    l = abs(s[s < 0].sum())
    pf_boot[i] = g / l if l > 0 else 0

# Zero-edge null model
abs_p = np.abs(profits)
pf_null = np.empty(N)
for i in range(N):
    signs = np.random.choice([-1, 1], size=n)
    s = abs_p * signs
    g = s[s > 0].sum()
    l = abs(s[s < 0].sum())
    pf_null[i] = g / l if l > 0 else 0

ci_low, ci_high = np.percentile(pf_boot, [10, 90])
nlo, nhi = np.percentile(pf_null, [10, 90])
pct_rank = (pf_null < pf).mean() * 100

print()
print("=" * 55)
print("  BOOTSTRAP EDGE VALIDATION")
print("=" * 55)
print(f"  Trades: {n}  |  Observed PF: {pf:.4f}")
print(f"  Bootstrap 80% CI:      {ci_low:.3f} -- {ci_high:.3f}")
print(f"  Zero-edge null 80% CI: {nlo:.3f} -- {nhi:.3f}")
print(f"  Observed PF beats {pct_rank:.1f}% of zero-edge simulations")
print()
if pct_rank < 80:
    print("  VERDICT: NO EDGE DETECTED")
    print(f"  PF {pf:.3f} is routine noise.")
    print(f"  A coin-flip strategy produces this result {100 - pct_rank:.0f}% of the time.")
    print("  Recommendation: Abandon London Breakout.")
elif pct_rank < 90:
    print("  VERDICT: WEAK / MARGINAL SIGNAL")
    print(f"  PF {pf:.3f} is above median noise but NOT statistically robust.")
    print("  Fix the code bugs first, then retest before going live.")
else:
    print("  VERDICT: POSSIBLE EDGE EXISTS")
    print(f"  PF {pf:.3f} beats {pct_rank:.1f}% of zero-edge simulations.")
    print("  Worth investigating further with out-of-sample data.")
print("=" * 55)

# Save chart
fig, axes = plt.subplots(1, 2, figsize=(14, 5))
fig.suptitle("Bootstrap Edge Validation -- London Breakout v2.3 (Report 11)", fontsize=13)

ax = axes[0]
ax.hist(pf_boot, bins=60, color='steelblue', alpha=0.7, label='Bootstrap (your trades)')
ax.axvline(pf, color='red', lw=2, label=f'Observed PF {pf:.3f}')
ax.axvline(1.0, color='black', lw=1, linestyle='--', label='PF = 1.0 (breakeven)')
ax.axvspan(ci_low, ci_high, alpha=0.2, color='orange', label=f'80% CI: {ci_low:.2f}-{ci_high:.2f}')
ax.set_xlabel('Profit Factor')
ax.set_ylabel('Count')
ax.set_title('Your Trade Distribution (Resampled)')
ax.legend(fontsize=8)

ax = axes[1]
ax.hist(pf_null, bins=60, color='salmon', alpha=0.7, label='Zero-edge null model')
ax.axvline(pf, color='red', lw=2, label=f'Observed PF {pf:.3f}')
ax.axvline(1.0, color='black', lw=1, linestyle='--', label='PF = 1.0 (breakeven)')
ax.axvspan(nlo, nhi, alpha=0.2, color='orange', label=f'80% CI: {nlo:.2f}-{nhi:.2f}')
ax.set_xlabel('Profit Factor')
ax.set_title(f'Zero-Edge Null -- Your PF beats {pct_rank:.1f}% of random results')
ax.legend(fontsize=8)

plt.tight_layout()
out = r"C:\Users\hamza\Downloads\Ai projects\Pine script signal bot\bootstrap_result.png"
plt.savefig(out, dpi=150)
print(f"Chart saved: {out}")
