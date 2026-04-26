# London Breakout Iteration Log

Started: 2026-04-26 10:15 UTC


------------------------------------------------------------
## Iteration 1  --  2026-04-26 10:18 UTC
**Params:** TP=1.0x  TrendFilter=OFF  W1_EMA=20  MinRange=5pips

**Period A (2015-2019):**
  IS PF=1.745  OOS PF=1.345  N=569  Bootstrap=100.0%  p=0.0  -> PASS

**Period B (2020-2024):**
  IS PF=1.508  OOS PF=1.683  N=451  Bootstrap=100.0%  p=0.0  -> PASS

============================================================
CONVERGED AT ITERATION 1
Both Period A and Period B PASSED.

Final parameters:
  TP=1.0x  TrendFilter=OFF
  W1_EMA=20  MinRange=5pips

Period A: OOS PF=1.345  N=569  Bootstrap=100.0%  p=0.0
Period B: OOS PF=1.683  N=451  Bootstrap=100.0%  p=0.0

Next step: Forward test on 2025+ data, then port to MQL5 EA.
============================================================

------------------------------------------------------------
## Iteration 1  --  2026-04-26 10:19 UTC
**Params:** TP=1.0x  TrendFilter=OFF  W1_EMA=20  MinRange=5pips

**Period A (2015-2019):**
  IS PF=1.745  OOS PF=1.345  N=569  Bootstrap=99.9%  p=0.0  -> PASS

**Period B (2020-2024):**
  IS PF=1.508  OOS PF=1.683  N=451  Bootstrap=100.0%  p=0.0  -> PASS

============================================================
CONVERGED AT ITERATION 1
Both Period A and Period B PASSED.

Final parameters:
  TP=1.0x  TrendFilter=OFF
  W1_EMA=20  MinRange=5pips

Period A: OOS PF=1.345  N=569  Bootstrap=99.9%  p=0.0
Period B: OOS PF=1.683  N=451  Bootstrap=100.0%  p=0.0

Next step: Forward test on 2025+ data, then port to MQL5 EA.
============================================================
