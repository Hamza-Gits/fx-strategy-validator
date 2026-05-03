#!/usr/bin/env python3
import pandas as pd
import numpy as np

# Load the recent data (tab-separated)
df = pd.read_csv(r'C:\Users\hamza\Downloads\Ai projects\Pine script signal bot\data\GBPUSD_H1_2021-2025.csv', sep='\t')

# Debug: print actual column names
print("Columns:", df.columns.tolist())

# The column names are stripped of angle brackets when loaded
time_col = 'TIME' if 'TIME' in df.columns else '<TIME>'
spread_col = 'SPREAD' if 'SPREAD' in df.columns else '<SPREAD>'

# Parse TIME column (format: HH:MM:SS)
df[time_col] = pd.to_datetime(df[time_col], format='%H:%M:%S').dt.time

# Filter for 07:00:00 GMT (London open)
london_open = df[df[time_col].astype(str) == '07:00:00']

# Calculate spread statistics
spread_mean = london_open[spread_col].mean()
spread_min = london_open[spread_col].min()
spread_max = london_open[spread_col].max()
spread_std = london_open[spread_col].std()
spread_count = len(london_open)

print("=" * 60)
print("ECN GATE ANALYSIS: LONDON OPEN (07:00 GMT) SPREAD")
print("=" * 60)
print(f"Data period: 2021-2025")
print(f"Observations at 07:00 GMT: {spread_count}")
print()
print(f"Mean Spread:   {spread_mean:.2f} pip")
print(f"Min Spread:    {spread_min:.2f} pip")
print(f"Max Spread:    {spread_max:.2f} pip")
print(f"Std Dev:       {spread_std:.2f} pip")
print()

# RTT calculation: spread + slippage + commission
# Assume: 2 pip slippage + 0.5 pip commission (typical ECN)
slippage = 2.0
commission = 0.5
estimated_rtt = spread_mean + slippage + commission

print(f"Estimated RTT (spread + {slippage} pip slippage + {commission} pip commission): {estimated_rtt:.2f} pip")
print()

# Decision gate
if estimated_rtt <= 2.0:
    verdict = "VIABLE - RTT <= 2.0 pip. Proceed to Phase 4."
    action = "Run EURUSD parity test. Lock OOS holdout. Prepare for The5ers eval."
elif estimated_rtt <= 2.5:
    verdict = "MARGINAL - RTT 2.0-2.5 pip. Proceed with caution."
    action = "EURUSD parity test mandatory. If viable, fix DST + logger before prop eval."
else:
    verdict = "DEAD - RTT > 2.5 pip. Kill GBPUSD strategy."
    action = "Evaluate 5-minute delayed entry (post-spread normalization) or switch pair."

print(f"VERDICT: {verdict}")
print(f"ACTION: {action}")
print("=" * 60)
