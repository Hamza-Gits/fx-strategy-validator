import re
path = r"C:\Users\hamza\Downloads\Ai projects\CLAUDE MT5\Backtest Report's\Backtest report 17\ReportTester-5049476835.html"
with open(path, 'rb') as f:
    raw = f.read()
text = raw.decode('utf-16', errors='ignore')
text_clean = re.sub(r'<[^>]+>', ' ', text)
text_clean = re.sub(r'&nbsp;|&amp;', ' ', text_clean)
text_clean = re.sub(r'\s+', ' ', text_clean)
keywords = ['Total Net Profit', 'Profit Factor', 'Total Trades', 'Equity Drawdown',
            'Balance Drawdown', 'Recovery Factor', 'Sharpe', 'Win Rate', 'Average Profit',
            'Initial Deposit', 'Period:', 'Symbol:', 'Maximal', 'Short Trades',
            'Long Trades', 'Profit Trades', 'Loss Trades', 'Largest profit', 'Largest loss',
            'Bars:', 'Ticks:', 'Expert:', 'Inputs', 'InpRiskPercent', 'InpUseProgressiveRisk',
            'InpTrailingDDPct', 'InpHardHalt', 'Modeling']
for kw in keywords:
    idx = text_clean.find(kw)
    if idx >= 0:
        print(f'{kw}: {text_clean[idx:idx+180]}')
        print()
