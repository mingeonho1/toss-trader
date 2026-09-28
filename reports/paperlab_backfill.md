# Aggressive Forward Paper Lab — BACKFILL REPLAY (BACKTEST, NOT PAPER)

- Generated: `2026-09-28T04:44:39+00:00`
- **⚠️ BACKTEST**: historical replay from `2021-01-01` on keyless daily closes, for sanity only. This is NOT forward paper evidence and NOT a Lane A PASS. Same engine, same Toss fees, same no-look-ahead (signal t → fill t+1).
- Books: unit $1,000 and real $36. Cash 0%. Leveraged ETFs use listed history (no synthetic pre-inception here).

| Strategy | Fill | Sessions | Years | CAGR (u$1k) | Total | MaxDD | Trades | Fees(u) | Final(u) | Final($36) | QQQ B&H CAGR | TQQQ B&H CAGR |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mom_top5_ndx` | close | 1439 | 5.7 | +49.54% | +899.83% | -40.19% | 388 | $109.53 | $9998.32 | $346.77 | +17.27% | +27.82% |
| `lrr_tqqq` | close | 1439 | 5.7 | +35.10% | +459.30% | -49.74% | 7 | $16.56 | $5593.03 | $201.33 | +17.27% | +27.82% |
| `hot_rvol_swing` | open | 1439 | 5.7 | +27.45% | +300.63% | -51.82% | 811 | $1073.36 | $4006.33 | $137.44 | +17.27% | +27.82% |
| `tqqq_bh` | close | 1439 | 5.7 | +27.25% | +297.03% | -81.56% | 1 | $0.99 | $3970.34 | $142.96 | +17.27% | +27.82% |
| `voltarget_3x` | close | 1439 | 5.7 | +20.71% | +193.53% | -35.11% | 30 | $10.23 | $2935.27 | $105.44 | +17.27% | +27.82% |
| `qqq_bh` | close | 1439 | 5.7 | +17.09% | +146.60% | -35.12% | 1 | $0.99 | $2465.95 | $88.79 | +17.27% | +27.82% |
| `rsi2_tqqq` | close | 1439 | 5.7 | +16.75% | +142.53% | -24.24% | 104 | $165.08 | $2425.32 | $86.50 | +17.27% | +27.82% |
| `lrr_tqqq_sqqq` | close | 1439 | 5.7 | +14.30% | +114.81% | -65.35% | 13 | $19.61 | $2148.11 | $77.27 | +17.27% | +27.82% |

## Reading this

- Lane A gate (design+holdout CAGR > QQQ, neighbor robustness, 2× cost) is evaluated elsewhere; this replay is a smoke test that the strategies compute and trade sanely.
- A leveraged/inverse strategy can post a large CAGR here and still be rejected — forward paper (≥3 months, live prices) is the real judge.
