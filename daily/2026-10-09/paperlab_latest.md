# Aggressive Forward Paper Lab — Leaderboard

- Generated: `2026-10-09T01:56:31+00:00`
- **PAPER (forward, live prices going forward). Real orders: none, ever.** Signal at close t → fill at t+1 (close, or open where noted). Cash earns 0%.
- Two books per strategy: **unit $1,000** (clean performance) and **real $36** (true small-scale Toss fees). Leaderboard ranks by the unit book's total return.
- Fees: Toss exact — buys ≤$10 free, sells 0.1% + SEC/TAF minimums.
- Lane A objective (docs/gate_v2_spec.md 부록 v3): cost-adjusted CAGR; bankruptcy guard only (unit MDD > −95% ⇒ FAIL). Live conversion needs cumulative return > QQQ over ≥3 months + 0 ops errors + user approval.

## Strategies — grouped by real-account executability, sorted by unit-book total return

### 실계좌 가능(비레버리지) (12)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hibeta_basket` | close | 2026-09-28 | 8 | +3.66% | +0.00% | +330.85% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.88% | +8.54% |
| `ftlt_hibeta` | close | 2026-09-28 | 8 | +3.66% | +0.00% | +330.85% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.88% | +8.54% |
| `ftlt_hibeta_psq` | close | 2026-09-28 | 8 | +3.66% | +0.00% | +330.85% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.88% | +8.54% |
| `holygrail_hibeta` | close | 2026-09-28 | 8 | +3.66% | +0.00% | +330.85% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.88% | +8.54% |
| `simple_hibeta` | close | 2026-09-28 | 8 | +3.66% | +0.00% | +330.85% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.88% | +8.54% |
| `buffer_hibeta` | close | 2026-09-28 | 8 | +3.66% | +0.00% | +330.85% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.88% | +8.54% |
| `qqq_bh` | close | 2026-09-28 | 8 | +2.58% | -0.25% | +181.33% | -0.25% | 1 | $0.990 | +2.60% | $0.030 | +2.88% | +8.54% |
| `ftlt_1x` | close | 2026-09-28 | 8 | +2.58% | -0.25% | +181.33% | -0.25% | 1 | $0.990 | +2.60% | $0.030 | +2.88% | +8.54% |
| `lrs200_qqq` | close | 2026-09-28 | 8 | +2.58% | -0.25% | +181.33% | -0.25% | 1 | $0.990 | +2.60% | $0.030 | +2.88% | +8.54% |
| `overnight_qqq` | open | 2026-09-28 | 8 | +0.89% | -0.95% | +43.10% | -0.95% | 15 | $13.810 | +1.22% | $0.350 | +2.88% | +8.54% |
| `mom_top5_ndx` | close | 2026-09-28 | 8 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +2.88% | +8.54% |
| `ep_gap_swing` | open | 2026-09-28 | 8 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +2.88% | +8.54% |

### 레버리지 ETP(예탁금 필요) (14)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ftlt_moc` | close | 2026-09-28 | 8 | +8.54% | -0.75% | +2682.95% | -0.75% | 1 | $0.990 | +8.54% | $0.030 | +2.88% | +8.54% |
| `lrr_tqqq` | close | 2026-09-28 | 8 | +7.85% | -0.75% | +2043.99% | -0.75% | 1 | $0.990 | +7.86% | $0.030 | +2.88% | +8.54% |
| `lrr_tqqq_sqqq` | close | 2026-09-28 | 8 | +7.85% | -0.75% | +2043.99% | -0.75% | 1 | $0.990 | +7.86% | $0.030 | +2.88% | +8.54% |
| `tqqq_bh` | close | 2026-09-28 | 8 | +7.85% | -0.75% | +2043.99% | -0.75% | 1 | $0.990 | +7.86% | $0.030 | +2.88% | +8.54% |
| `ftlt` | close | 2026-09-28 | 8 | +7.85% | -0.75% | +2043.99% | -0.75% | 1 | $0.990 | +7.86% | $0.030 | +2.88% | +8.54% |
| `holy_grail` | close | 2026-09-28 | 8 | +7.85% | -0.75% | +2043.99% | -0.75% | 1 | $0.990 | +7.86% | $0.030 | +2.88% | +8.54% |
| `simple_rsi_uvxy` | close | 2026-09-28 | 8 | +7.85% | -0.75% | +2043.99% | -0.75% | 1 | $0.990 | +7.86% | $0.030 | +2.88% | +8.54% |
| `lrs200_tqqq` | close | 2026-09-28 | 8 | +7.85% | -0.75% | +2043.99% | -0.75% | 1 | $0.990 | +7.86% | $0.030 | +2.88% | +8.54% |
| `sma200_buffer_tqqq` | close | 2026-09-28 | 8 | +7.85% | -0.75% | +2043.99% | -0.75% | 1 | $0.990 | +7.86% | $0.030 | +2.88% | +8.54% |
| `overnight_tqqq` | open | 2026-09-28 | 8 | +5.04% | -2.48% | +634.57% | -2.48% | 15 | $14.310 | +5.42% | $0.350 | +2.88% | +8.54% |
| `nine_sig` | close | 2026-09-28 | 8 | +4.65% | -0.51% | +532.49% | -0.51% | 4 | $1.060 | +103.86% | $0.060 | +2.88% | +8.54% |
| `voltarget_3x` | close | 2026-09-28 | 8 | +3.81% | -0.38% | +356.37% | -0.38% | 1 | $0.480 | +3.83% | $0.010 | +2.88% | +8.54% |
| `rsi2_tqqq` | close | 2026-09-28 | 8 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +2.88% | +8.54% |
| `hot_rvol_swing` | open | 2026-09-28 | 8 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +2.88% | +8.54% |

### 탐색용 (1)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | close | 2026-09-28 | 8 | +2.58% | +0.00% | +181.25% | -1.78% | 2 | $0.980 | +2.62% | $0.020 | +2.88% | +8.54% |


## Daily returns (last 10 sessions)

| Date | `ftlt_moc` | `lrr_tqqq` | `lrr_tqqq_sqqq` | `tqqq_bh` | `ftlt` | `holy_grail` | `simple_rsi_uvxy` | `lrs200_tqqq` | `sma200_buffer_tqqq` | `overnight_tqqq` | `nine_sig` | `voltarget_3x` | `hibeta_basket` | `ftlt_hibeta` | `ftlt_hibeta_psq` | `holygrail_hibeta` | `simple_hibeta` | `buffer_hibeta` | `qqq_bh` | `ftlt_1x` | `lrs200_qqq` | `btc_proxy_mstr_coin` | `overnight_qqq` | `rsi2_tqqq` | `mom_top5_ndx` | `hot_rvol_swing` | `ep_gap_swing` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-29 | +0.55% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | +1.23% | -0.10% | -0.05% | -0.09% | -0.09% | -0.09% | -0.09% | -0.09% | -0.09% | -0.10% | -0.10% | -0.10% | -0.10% | +0.31% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-09-30 | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.68% | +0.42% | +0.36% | -0.24% | -0.24% | -0.24% | -0.24% | -0.24% | -0.24% | +0.25% | +0.25% | +0.25% | -1.46% | +0.12% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-10-01 | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.49% | +0.44% | +2.13% | +2.13% | +2.13% | +2.13% | +2.13% | +2.13% | +0.31% | +0.31% | +0.31% | +3.20% | +0.19% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-10-02 | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +3.43% | +1.67% | +1.42% | +1.39% | +1.39% | +1.39% | +1.39% | +1.39% | +1.39% | +1.02% | +1.02% | +1.02% | -1.78% | +1.06% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-10-05 | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | -0.26% | +1.64% | +1.29% | +0.44% | +0.44% | +0.44% | +0.44% | +0.44% | +0.44% | +0.88% | +0.88% | +0.88% | +2.81% | -0.21% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-10-06 | +1.36% | +1.36% | +1.36% | +1.36% | +1.36% | +1.36% | +1.36% | +1.36% | +1.36% | +1.52% | +0.97% | +0.68% | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% | +0.46% | +0.46% | +0.46% | +0.00% | +0.39% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-10-07 | -0.75% | -0.75% | -0.75% | -0.75% | -0.75% | -0.75% | -0.75% | -0.75% | -0.75% | -2.48% | -0.51% | -0.38% | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% | +0.00% | -0.25% | -0.25% | -0.25% | +0.00% | -0.95% | +0.00% | +0.00% | +0.00% | +0.00% |

## Notes

- `qqq_bh` / `tqqq_bh` are benchmark strategies run through the same broker (so their small fee drag is visible); the *vs QQQ/TQQQ B&H* columns are the raw adjusted-close buy&hold over each strategy's own live window.
- Dividends are reflected via adjusted-close cache for single names/ETFs where the source provides them; leveraged ETFs (TQQQ/SQQQ) use their own listed history.
- MaxDD/CAGR are on the unit book. Sharpe/MDD are reported but are NOT a fail reason in Lane A — only the −95% bankruptcy guard is.
- New strategies plug in via `src/toss_trader/paperlab_strategies/` (hook for docs/aggressive_strategy_catalog.md candidates).

- **Contributions**: `nine_sig` +$35 added to the **$36 book only** (monthly, first session of each month; the $1k book stays contribution-free). The $36 *Total* is therefore money-weighted (deposits inflate it) and is not directly comparable to the $1k *Total*.
