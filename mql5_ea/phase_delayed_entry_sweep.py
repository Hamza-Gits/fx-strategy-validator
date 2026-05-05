#!/usr/bin/env python3
"""
Delayed Entry Sweep: Test London open entry at 07:00, 07:05, 07:10, 07:15 GMT
Measures spread at each bar to find optimal entry window post-spread-spike normalization.
"""
import pandas as pd
import numpy as np

df = pd.read_csv(
    r'C:\Users\hamza\Downloads\Ai projects\Pine script signal bot\data\GBPUSD_H1_2021-2025.csv',
    sep='\t'
)

# Filter London session bars 07:00-09:00 GMT
london_bars = df[df['<TIME>'].isin(['07:00:00', '08:00:00', '09:00:00'])]

print("=" * 65)
print("DELAYED ENTRY SPREAD ANALYSIS — GBPUSD H1 2021-2025")
print("=" * 65)
print(f"{'Time (GMT)':<14} {'Count':<8} {'Mean Spread':<14} {'Std':<8} {'Est RTT':<12} {'Verdict'}")
print("-" * 65)

RTT_SLIPPAGE = 0.75  # slippage on bar-close entry (lower than breakout spike)

for time_str in ['07:00:00', '08:00:00', '09:00:00']:
    subset = df[df['<TIME>'] == time_str]
    if len(subset) == 0:
        continue
    mean_spread = subset['<SPREAD>'].mean()
    std_spread = subset['<SPREAD>'].std()
    est_rtt = mean_spread + RTT_SLIPPAGE
    count = len(subset)

    if est_rtt <= 2.0:
        verdict = "VIABLE"
    elif est_rtt <= 2.5:
        verdict = "MARGINAL"
    else:
        verdict = "DEAD"

    print(f"{time_str:<14} {count:<8} {mean_spread:<14.2f} {std_spread:<8.2f} {est_rtt:<12.2f} {verdict}")

print()
print("NOTE: RTT = spread + 0.75 pip slippage (bar-close entry, calmer post-spike)")
print()

# Percentile breakdown for 07:00 bar
s0700 = df[df['<TIME>'] == '07:00:00']['<SPREAD>']
print("07:00 GMT spread distribution:")
print(f"  25th pct: {s0700.quantile(0.25):.1f} pip")
print(f"  50th pct: {s0700.quantile(0.50):.1f} pip")
print(f"  75th pct: {s0700.quantile(0.75):.1f} pip")
print(f"  90th pct: {s0700.quantile(0.90):.1f} pip")
print(f"  Zeros:    {(s0700 == 0).sum()} bars (broker didn't log spread — exclude)")
print()

# Exclude zero-spread bars (broker data gaps)
s0700_clean = s0700[s0700 > 0]
print(f"07:00 GMT CLEAN (non-zero spread): {len(s0700_clean)} bars")
print(f"  Mean: {s0700_clean.mean():.2f} pip | Std: {s0700_clean.std():.2f} pip")
est_rtt_clean = s0700_clean.mean() + RTT_SLIPPAGE
print(f"  Est RTT (clean): {est_rtt_clean:.2f} pip => {'VIABLE' if est_rtt_clean <= 2.0 else 'MARGINAL' if est_rtt_clean <= 2.5 else 'DEAD'}")
print("=" * 65)
