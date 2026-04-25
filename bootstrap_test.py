"""
Bootstrap Edge Validator
========================
Tests whether Report 11 PF 1.08 is a real edge or random noise.
Reads MT5 trade history CSV exported from Account History tab.

HOW TO EXPORT FROM MT5:
  Terminal -> Account History tab -> right-click -> Save as Report
  Or: Strategy Tester -> Report tab -> right-click -> Export to spreadsheet
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os
import sys

# Fix Windows console encoding so print() works with any character
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf-16'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass  # Python < 3.7 fallback

# ── CONFIG ──────────────────────────────────────────────────────────────────
OBSERVED_PF   = 1.08      # Your Report 11 result
N_RESAMPLES   = 10000     # Bootstrap iterations
CONFIDENCE_CI = 80        # % confidence interval (80 = 10th to 90th percentile)

# ── LOAD DATA ────────────────────────────────────────────────────────────────
def find_csv():
    """Look for trade CSV/XLSX in common locations."""
    search_dirs = [
        r"C:\Users\hamza\Downloads\Ai projects\CLAUDE MT5\Backtest Report's\Backtest report 11",
        r"C:\Users\hamza\Downloads\Ai projects\CLAUDE MT5\Backtest Report's",
        r"C:\Users\hamza\Downloads",
        r"C:\Users\hamza\Desktop",
        os.getcwd(),
    ]
    for d in search_dirs:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            fl = f.lower()
            if fl.endswith(('.csv', '.xlsx', '.xls')) and any(
                    k in fl for k in ['report', 'history', 'trade', 'backtest', 'ldn', 'deals']):
                full = os.path.join(d, f)
                print(f"Found: {full}")
                return full
    return None


def load_trades(filepath=None):
    """Load trade P&L from CSV or XLSX. Handles MT5 Strategy Tester exports."""
    if filepath is None:
        filepath = find_csv()
    if filepath is None:
        print("\n❌ No file found automatically.")
        print("Please drag your MT5 trade export file here and press Enter,")
        print("or type the full path to the file:")
        filepath = input("> ").strip().strip('"')

    print(f"\nLoading: {filepath}")
    ext = os.path.splitext(filepath)[1].lower()

    # ── XLSX: MT5 Strategy Tester multi-section report ───────────────────────
    if ext in ['.xlsx', '.xls']:
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = pd.read_excel(filepath, header=None)

            # Find the Deals section header row
            deals_header_row = None
            for i, row in raw.iterrows():
                vals = [str(v) for v in row.values if str(v) != 'nan']
                joined = ' '.join(vals)
                if 'Time' in joined and 'Profit' in joined and 'Direction' in joined:
                    deals_header_row = i
                    break

            if deals_header_row is None:
                print("❌ Could not find Deals table in Excel file.")
                sys.exit(1)

            print(f"   Found Deals header at row {deals_header_row}")

            # Read from that row onward, using it as the header
            df = raw.iloc[deals_header_row:].copy()
            df.columns = raw.iloc[deals_header_row].values
            df = df.iloc[1:].reset_index(drop=True)

            # Keep only 'out' (closing) deals — these carry the P&L
            if 'Direction' in df.columns:
                df = df[df['Direction'].astype(str).str.strip().str.lower() == 'out']

            profit_col = 'Profit'

        except ImportError:
            print("❌ openpyxl not installed. Run: pip install openpyxl")
            sys.exit(1)

    # ── CSV: plain export ─────────────────────────────────────────────────────
    else:
        for enc in ['utf-8', 'utf-16', 'latin-1', 'cp1252']:
            try:
                df = pd.read_csv(filepath, encoding=enc, sep=None, engine='python')
                break
            except Exception:
                continue
        else:
            print("❌ Could not read file.")
            sys.exit(1)

        print(f"Columns found: {list(df.columns)}")

        # Find the profit column (MT5 exports vary)
        profit_col = None
        for col in df.columns:
            if str(col).strip().lower() in ['profit', 'p&l', 'pnl', 'net profit', 'gain']:
                profit_col = col
                break

        if profit_col is None:
            print("\nColumn names:", list(df.columns))
            print("Which column contains trade profit/loss? (type the column name exactly)")
            profit_col = input("> ").strip()

    profits = pd.to_numeric(df[profit_col], errors='coerce').dropna()

    # Remove deposit/withdrawal rows (zero or NaN)
    profits = profits[profits != 0]

    print(f"\n[OK] Loaded {len(profits)} closed trades")
    print(f"   Total P&L: ${profits.sum():.2f}")
    print(f"   Winners:   {(profits > 0).sum()}")
    print(f"   Losers:    {(profits < 0).sum()}")
    win_rate = (profits > 0).sum() / len(profits) * 100
    print(f"   Win rate:  {win_rate:.1f}%")

    gross_profit = profits[profits > 0].sum()
    gross_loss   = abs(profits[profits < 0].sum())
    actual_pf    = gross_profit / gross_loss if gross_loss > 0 else 0
    print(f"   Actual PF: {actual_pf:.3f}")

    return profits.values


# ── BOOTSTRAP ───────────────────────────────────────────────────────────────
def bootstrap_pf(profits, n_resamples=10000):
    """Resample trades with replacement, compute PF each time."""
    n = len(profits)
    pf_dist = np.empty(n_resamples)

    for i in range(n_resamples):
        sample    = np.random.choice(profits, size=n, replace=True)
        gp        = sample[sample > 0].sum()
        gl        = abs(sample[sample < 0].sum())
        pf_dist[i] = gp / gl if gl > 0 else 0

    return pf_dist


def null_model_pf(profits, n_resamples=10000):
    """
    Zero-edge null model: shuffle signs of profits, keeping magnitudes.
    This asks: 'What PF do we get from random +/- assignment?'
    """
    abs_profits = np.abs(profits)
    n = len(profits)
    pf_null = np.empty(n_resamples)

    for i in range(n_resamples):
        signs  = np.random.choice([-1, 1], size=n)
        sample = abs_profits * signs
        gp     = sample[sample > 0].sum()
        gl     = abs(sample[sample < 0].sum())
        pf_null[i] = gp / gl if gl > 0 else 0

    return pf_null


# ── ANALYSIS ─────────────────────────────────────────────────────────────────
def run_analysis(profits):
    n = len(profits)
    gross_profit = profits[profits > 0].sum()
    gross_loss   = abs(profits[profits < 0].sum())
    observed_pf  = gross_profit / gross_loss if gross_loss > 0 else 0

    print(f"\n{'='*55}")
    print(f"  BOOTSTRAP EDGE VALIDATION")
    print(f"{'='*55}")
    print(f"  Trades: {n}  |  Observed PF: {observed_pf:.3f}")
    print(f"  Running {N_RESAMPLES:,} bootstrap resamples...")

    pf_boot = bootstrap_pf(profits, N_RESAMPLES)
    pf_null = null_model_pf(profits, N_RESAMPLES)

    lo = (100 - CONFIDENCE_CI) / 2
    hi = 100 - lo
    ci_low,  ci_high  = np.percentile(pf_boot, [lo, hi])
    nlo, nhi          = np.percentile(pf_null, [lo, hi])

    print(f"\n  Bootstrap resampling ({CONFIDENCE_CI}% CI):")
    print(f"    {ci_low:.3f} — {ci_high:.3f}")
    print(f"\n  Zero-edge null model ({CONFIDENCE_CI}% CI):")
    print(f"    {nlo:.3f} — {nhi:.3f}")

    pct_rank = (pf_null < observed_pf).mean() * 100
    print(f"\n  Observed PF beats {pct_rank:.1f}% of zero-edge simulations")

    print(f"\n{'='*55}")
    if pct_rank < 80:
        verdict = "❌ NO EDGE DETECTED"
        detail  = (f"PF {observed_pf:.3f} is routine noise. "
                   f"A coin-flip strategy produces this result {100-pct_rank:.0f}% of the time. "
                   f"Abandon London Breakout.")
    elif pct_rank < 90:
        verdict = "⚠️  WEAK / MARGINAL SIGNAL"
        detail  = (f"PF {observed_pf:.3f} is above median noise but NOT statistically robust. "
                   f"Not enough edge to rely on. Fix the ComputeLondonRange bug and retest.")
    else:
        verdict = "✅ POSSIBLE EDGE EXISTS"
        detail  = (f"PF {observed_pf:.3f} beats {pct_rank:.1f}% of zero-edge simulations. "
                   f"Worth investigating further. Fix the H4 range bug (rates[1] → proper London window) "
                   f"and run a clean backtest.")

    print(f"  VERDICT: {verdict}")
    print(f"  {detail}")
    print(f"{'='*55}")

    return pf_boot, pf_null, observed_pf


# ── PLOT ──────────────────────────────────────────────────────────────────────
def plot_results(pf_boot, pf_null, observed_pf):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("Bootstrap Edge Validation — London Breakout v2.3 (Report 11)", fontsize=13)

    # Left: bootstrap distribution of your actual trades
    ax = axes[0]
    ax.hist(pf_boot, bins=60, color='steelblue', alpha=0.7, label='Bootstrap (your trades)')
    ax.axvline(observed_pf, color='red',    lw=2, label=f'Observed PF {observed_pf:.3f}')
    ax.axvline(1.0,         color='black',  lw=1, linestyle='--', label='PF = 1.0 (breakeven)')
    ci_low, ci_high = np.percentile(pf_boot, [10, 90])
    ax.axvspan(ci_low, ci_high, alpha=0.2, color='orange', label=f'80% CI: {ci_low:.2f}–{ci_high:.2f}')
    ax.set_xlabel('Profit Factor')
    ax.set_ylabel('Count')
    ax.set_title('Your Trade Distribution (Resampled)')
    ax.legend(fontsize=8)

    # Right: zero-edge null model
    ax = axes[1]
    ax.hist(pf_null, bins=60, color='salmon', alpha=0.7, label='Zero-edge null model')
    ax.axvline(observed_pf, color='red',    lw=2, label=f'Observed PF {observed_pf:.3f}')
    ax.axvline(1.0,         color='black',  lw=1, linestyle='--', label='PF = 1.0 (breakeven)')
    nlo, nhi = np.percentile(pf_null, [10, 90])
    ax.axvspan(nlo, nhi, alpha=0.2, color='orange', label=f'80% CI: {nlo:.2f}–{nhi:.2f}')
    pct_rank = (pf_null < observed_pf).mean() * 100
    ax.set_xlabel('Profit Factor')
    ax.set_title(f'Zero-Edge Null — Your PF beats {pct_rank:.1f}% of random results')
    ax.legend(fontsize=8)

    plt.tight_layout()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bootstrap_result.png')
    plt.savefig(out, dpi=150)
    print(f"\n  Chart saved: {out}")
    plt.show()


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  London Breakout Bootstrap Edge Validator")
    print("=" * 55)

    # Accept file path as command-line argument OR auto-detect
    filepath = sys.argv[1] if len(sys.argv) > 1 else None

    profits         = load_trades(filepath)
    pf_boot, pf_null, obs = run_analysis(profits)
    plot_results(pf_boot, pf_null, obs)
