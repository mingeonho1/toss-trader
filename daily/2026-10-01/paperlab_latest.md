# Aggressive Forward Paper Lab — Leaderboard

- Generated: `2026-10-01T00:53:44+00:00`
- **PAPER (forward, live prices going forward). Real orders: none, ever.** Signal at close t → fill at t+1 (close, or open where noted). Cash earns 0%.
- Two books per strategy: **unit $1,000** (clean performance) and **real $36** (true small-scale Toss fees). Leaderboard ranks by the unit book's total return.
- Fees: Toss exact — buys ≤$10 free, sells 0.1% + SEC/TAF minimums.
- Lane A objective (docs/gate_v2_spec.md 부록 v3): cost-adjusted CAGR; bankruptcy guard only (unit MDD > −95% ⇒ FAIL). Live conversion needs cumulative return > QQQ over ≥3 months + 0 ops errors + user approval.

## Strategies — grouped by real-account executability, sorted by unit-book total return

### 실계좌 가능(비레버리지) (12)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `overnight_qqq` | open | 2026-09-28 | 3 | +0.43% | +0.12% | +118.03% | +0.00% | 5 | $4.500 | +0.52% | $0.100 | +0.44% | +1.29% |
| `qqq_bh` | close | 2026-09-28 | 3 | +0.15% | +0.25% | +31.51% | -0.10% | 1 | $0.990 | +0.17% | $0.030 | +0.44% | +1.29% |
| `ftlt_1x` | close | 2026-09-28 | 3 | +0.15% | +0.25% | +31.51% | -0.10% | 1 | $0.990 | +0.17% | $0.030 | +0.44% | +1.29% |
| `lrs200_qqq` | close | 2026-09-28 | 3 | +0.15% | +0.25% | +31.51% | -0.10% | 1 | $0.990 | +0.17% | $0.030 | +0.44% | +1.29% |
| `mom_top5_ndx` | close | 2026-09-28 | 3 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.44% | +1.29% |
| `ep_gap_swing` | open | 2026-09-28 | 3 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.44% | +1.29% |
| `hibeta_basket` | close | 2026-09-28 | 3 | -0.33% | -0.24% | -44.82% | -0.33% | 10 | $0.900 | -0.24% | $0.000 | +0.44% | +1.29% |
| `ftlt_hibeta` | close | 2026-09-28 | 3 | -0.33% | -0.24% | -44.82% | -0.33% | 10 | $0.900 | -0.24% | $0.000 | +0.44% | +1.29% |
| `ftlt_hibeta_psq` | close | 2026-09-28 | 3 | -0.33% | -0.24% | -44.82% | -0.33% | 10 | $0.900 | -0.24% | $0.000 | +0.44% | +1.29% |
| `holygrail_hibeta` | close | 2026-09-28 | 3 | -0.33% | -0.24% | -44.82% | -0.33% | 10 | $0.900 | -0.24% | $0.000 | +0.44% | +1.29% |
| `simple_hibeta` | close | 2026-09-28 | 3 | -0.33% | -0.24% | -44.82% | -0.33% | 10 | $0.900 | -0.24% | $0.000 | +0.44% | +1.29% |
| `buffer_hibeta` | close | 2026-09-28 | 3 | -0.33% | -0.24% | -44.82% | -0.33% | 10 | $0.900 | -0.24% | $0.000 | +0.44% | +1.29% |

### 레버리지 ETP(예탁금 필요) (14)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `overnight_tqqq` | open | 2026-09-28 | 3 | +1.92% | +0.68% | +3095.77% | +0.00% | 5 | $4.550 | +2.01% | $0.100 | +0.44% | +1.29% |
| `ftlt_moc` | close | 2026-09-28 | 3 | +1.29% | +0.74% | +929.69% | +0.00% | 1 | $0.990 | +1.29% | $0.030 | +0.44% | +1.29% |
| `lrr_tqqq` | close | 2026-09-28 | 3 | +0.64% | +0.74% | +218.37% | -0.10% | 1 | $0.990 | +0.65% | $0.030 | +0.44% | +1.29% |
| `lrr_tqqq_sqqq` | close | 2026-09-28 | 3 | +0.64% | +0.74% | +218.37% | -0.10% | 1 | $0.990 | +0.65% | $0.030 | +0.44% | +1.29% |
| `tqqq_bh` | close | 2026-09-28 | 3 | +0.64% | +0.74% | +218.37% | -0.10% | 1 | $0.990 | +0.65% | $0.030 | +0.44% | +1.29% |
| `ftlt` | close | 2026-09-28 | 3 | +0.64% | +0.74% | +218.37% | -0.10% | 1 | $0.990 | +0.65% | $0.030 | +0.44% | +1.29% |
| `holy_grail` | close | 2026-09-28 | 3 | +0.64% | +0.74% | +218.37% | -0.10% | 1 | $0.990 | +0.65% | $0.030 | +0.44% | +1.29% |
| `simple_rsi_uvxy` | close | 2026-09-28 | 3 | +0.64% | +0.74% | +218.37% | -0.10% | 1 | $0.990 | +0.65% | $0.030 | +0.44% | +1.29% |
| `lrs200_tqqq` | close | 2026-09-28 | 3 | +0.64% | +0.74% | +218.37% | -0.10% | 1 | $0.990 | +0.65% | $0.030 | +0.44% | +1.29% |
| `sma200_buffer_tqqq` | close | 2026-09-28 | 3 | +0.64% | +0.74% | +218.37% | -0.10% | 1 | $0.990 | +0.65% | $0.030 | +0.44% | +1.29% |
| `nine_sig` | close | 2026-09-28 | 3 | +0.32% | +0.42% | +78.49% | -0.10% | 2 | $0.980 | +0.33% | $0.030 | +0.44% | +1.29% |
| `voltarget_3x` | close | 2026-09-28 | 3 | +0.31% | +0.36% | +75.72% | -0.05% | 1 | $0.480 | +0.33% | $0.010 | +0.44% | +1.29% |
| `rsi2_tqqq` | close | 2026-09-28 | 3 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.44% | +1.29% |
| `hot_rvol_swing` | open | 2026-09-28 | 3 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.44% | +1.29% |

### 탐색용 (1)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | close | 2026-09-28 | 3 | -1.56% | -1.46% | -94.31% | -1.56% | 2 | $0.980 | -1.52% | $0.020 | +0.44% | +1.29% |


## Daily returns (last 10 sessions)

| Date | `overnight_tqqq` | `ftlt_moc` | `lrr_tqqq` | `lrr_tqqq_sqqq` | `tqqq_bh` | `ftlt` | `holy_grail` | `simple_rsi_uvxy` | `lrs200_tqqq` | `sma200_buffer_tqqq` | `overnight_qqq` | `nine_sig` | `voltarget_3x` | `qqq_bh` | `ftlt_1x` | `lrs200_qqq` | `rsi2_tqqq` | `mom_top5_ndx` | `hot_rvol_swing` | `ep_gap_swing` | `hibeta_basket` | `ftlt_hibeta` | `ftlt_hibeta_psq` | `holygrail_hibeta` | `simple_hibeta` | `buffer_hibeta` | `btc_proxy_mstr_coin` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-29 | +1.23% | +0.55% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | +0.31% | -0.10% | -0.05% | -0.10% | -0.10% | -0.10% | +0.00% | +0.00% | +0.00% | +0.00% | -0.09% | -0.09% | -0.09% | -0.09% | -0.09% | -0.09% | -0.10% |
| 2026-09-30 | +0.68% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.12% | +0.42% | +0.36% | +0.25% | +0.25% | +0.25% | +0.00% | +0.00% | +0.00% | +0.00% | -0.24% | -0.24% | -0.24% | -0.24% | -0.24% | -0.24% | -1.46% |

## Notes

- `qqq_bh` / `tqqq_bh` are benchmark strategies run through the same broker (so their small fee drag is visible); the *vs QQQ/TQQQ B&H* columns are the raw adjusted-close buy&hold over each strategy's own live window.
- Dividends are reflected via adjusted-close cache for single names/ETFs where the source provides them; leveraged ETFs (TQQQ/SQQQ) use their own listed history.
- MaxDD/CAGR are on the unit book. Sharpe/MDD are reported but are NOT a fail reason in Lane A — only the −95% bankruptcy guard is.
- New strategies plug in via `src/toss_trader/paperlab_strategies/` (hook for docs/aggressive_strategy_catalog.md candidates).
