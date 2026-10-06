# Aggressive Forward Paper Lab — Leaderboard

- Generated: `2026-10-06T23:31:20+00:00`
- **PAPER (forward, live prices going forward). Real orders: none, ever.** Signal at close t → fill at t+1 (close, or open where noted). Cash earns 0%.
- Two books per strategy: **unit $1,000** (clean performance) and **real $36** (true small-scale Toss fees). Leaderboard ranks by the unit book's total return.
- Fees: Toss exact — buys ≤$10 free, sells 0.1% + SEC/TAF minimums.
- Lane A objective (docs/gate_v2_spec.md 부록 v3): cost-adjusted CAGR; bankruptcy guard only (unit MDD > −95% ⇒ FAIL). Live conversion needs cumulative return > QQQ over ≥3 months + 0 ops errors + user approval.

## Strategies — grouped by real-account executability, sorted by unit-book total return

### 실계좌 가능(비레버리지) (12)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hibeta_basket` | close | 2026-09-28 | 6 | +3.66% | +0.44% | +553.98% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.67% | +7.89% |
| `ftlt_hibeta` | close | 2026-09-28 | 6 | +3.66% | +0.44% | +553.98% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.67% | +7.89% |
| `ftlt_hibeta_psq` | close | 2026-09-28 | 6 | +3.66% | +0.44% | +553.98% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.67% | +7.89% |
| `holygrail_hibeta` | close | 2026-09-28 | 6 | +3.66% | +0.44% | +553.98% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.67% | +7.89% |
| `simple_hibeta` | close | 2026-09-28 | 6 | +3.66% | +0.44% | +553.98% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.67% | +7.89% |
| `buffer_hibeta` | close | 2026-09-28 | 6 | +3.66% | +0.44% | +553.98% | -0.33% | 10 | $0.900 | +3.76% | $0.000 | +2.67% | +7.89% |
| `qqq_bh` | close | 2026-09-28 | 6 | +2.37% | +0.88% | +240.22% | -0.10% | 1 | $0.990 | +2.39% | $0.030 | +2.67% | +7.89% |
| `ftlt_1x` | close | 2026-09-28 | 6 | +2.37% | +0.88% | +240.22% | -0.10% | 1 | $0.990 | +2.39% | $0.030 | +2.67% | +7.89% |
| `lrs200_qqq` | close | 2026-09-28 | 6 | +2.37% | +0.88% | +240.22% | -0.10% | 1 | $0.990 | +2.39% | $0.030 | +2.67% | +7.89% |
| `overnight_qqq` | open | 2026-09-28 | 6 | +1.47% | -0.21% | +113.84% | -0.21% | 11 | $10.090 | +1.70% | $0.250 | +2.67% | +7.89% |
| `mom_top5_ndx` | close | 2026-09-28 | 6 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +2.67% | +7.89% |
| `ep_gap_swing` | open | 2026-09-28 | 6 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +2.67% | +7.89% |

### 레버리지 ETP(예탁금 필요) (14)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ftlt_moc` | close | 2026-09-28 | 6 | +7.89% | +2.60% | +5164.00% | +0.00% | 1 | $0.990 | +7.89% | $0.030 | +2.67% | +7.89% |
| `lrr_tqqq` | close | 2026-09-28 | 6 | +7.20% | +2.60% | +3664.15% | -0.10% | 1 | $0.990 | +7.22% | $0.030 | +2.67% | +7.89% |
| `lrr_tqqq_sqqq` | close | 2026-09-28 | 6 | +7.20% | +2.60% | +3664.15% | -0.10% | 1 | $0.990 | +7.22% | $0.030 | +2.67% | +7.89% |
| `tqqq_bh` | close | 2026-09-28 | 6 | +7.20% | +2.60% | +3664.15% | -0.10% | 1 | $0.990 | +7.22% | $0.030 | +2.67% | +7.89% |
| `ftlt` | close | 2026-09-28 | 6 | +7.20% | +2.60% | +3664.15% | -0.10% | 1 | $0.990 | +7.22% | $0.030 | +2.67% | +7.89% |
| `holy_grail` | close | 2026-09-28 | 6 | +7.20% | +2.60% | +3664.15% | -0.10% | 1 | $0.990 | +7.22% | $0.030 | +2.67% | +7.89% |
| `simple_rsi_uvxy` | close | 2026-09-28 | 6 | +7.20% | +2.60% | +3664.15% | -0.10% | 1 | $0.990 | +7.22% | $0.030 | +2.67% | +7.89% |
| `lrs200_tqqq` | close | 2026-09-28 | 6 | +7.20% | +2.60% | +3664.15% | -0.10% | 1 | $0.990 | +7.22% | $0.030 | +2.67% | +7.89% |
| `sma200_buffer_tqqq` | close | 2026-09-28 | 6 | +7.20% | +2.60% | +3664.15% | -0.10% | 1 | $0.990 | +7.22% | $0.030 | +2.67% | +7.89% |
| `overnight_tqqq` | open | 2026-09-28 | 6 | +6.09% | -0.26% | +2087.01% | -0.26% | 11 | $10.380 | +6.36% | $0.250 | +2.67% | +7.89% |
| `nine_sig` | close | 2026-09-28 | 6 | +4.17% | +1.64% | +743.69% | -0.10% | 4 | $1.060 | +102.92% | $0.060 | +2.67% | +7.89% |
| `voltarget_3x` | close | 2026-09-28 | 6 | +3.50% | +1.29% | +501.45% | -0.05% | 1 | $0.480 | +3.52% | $0.010 | +2.67% | +7.89% |
| `rsi2_tqqq` | close | 2026-09-28 | 6 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +2.67% | +7.89% |
| `hot_rvol_swing` | open | 2026-09-28 | 6 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +2.67% | +7.89% |

### 탐색용 (1)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | close | 2026-09-28 | 6 | +2.58% | +2.81% | +277.93% | -1.78% | 2 | $0.980 | +2.62% | $0.020 | +2.67% | +7.89% |


## Daily returns (last 10 sessions)

| Date | `ftlt_moc` | `lrr_tqqq` | `lrr_tqqq_sqqq` | `tqqq_bh` | `ftlt` | `holy_grail` | `simple_rsi_uvxy` | `lrs200_tqqq` | `sma200_buffer_tqqq` | `overnight_tqqq` | `nine_sig` | `hibeta_basket` | `ftlt_hibeta` | `ftlt_hibeta_psq` | `holygrail_hibeta` | `simple_hibeta` | `buffer_hibeta` | `voltarget_3x` | `btc_proxy_mstr_coin` | `qqq_bh` | `ftlt_1x` | `lrs200_qqq` | `overnight_qqq` | `rsi2_tqqq` | `mom_top5_ndx` | `hot_rvol_swing` | `ep_gap_swing` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-29 | +0.55% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | +1.23% | -0.10% | -0.09% | -0.09% | -0.09% | -0.09% | -0.09% | -0.09% | -0.05% | -0.10% | -0.10% | -0.10% | -0.10% | +0.31% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-09-30 | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.68% | +0.42% | -0.24% | -0.24% | -0.24% | -0.24% | -0.24% | -0.24% | +0.36% | -1.46% | +0.25% | +0.25% | +0.25% | +0.12% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-10-01 | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.49% | +2.13% | +2.13% | +2.13% | +2.13% | +2.13% | +2.13% | +0.44% | +3.20% | +0.31% | +0.31% | +0.31% | +0.19% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-10-02 | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +3.43% | +1.67% | +1.39% | +1.39% | +1.39% | +1.39% | +1.39% | +1.39% | +1.42% | -1.78% | +1.02% | +1.02% | +1.02% | +1.06% | +0.00% | +0.00% | +0.00% | +0.00% |
| 2026-10-05 | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | +2.60% | -0.26% | +1.64% | +0.44% | +0.44% | +0.44% | +0.44% | +0.44% | +0.44% | +1.29% | +2.81% | +0.88% | +0.88% | +0.88% | -0.21% | +0.00% | +0.00% | +0.00% | +0.00% |

## Notes

- `qqq_bh` / `tqqq_bh` are benchmark strategies run through the same broker (so their small fee drag is visible); the *vs QQQ/TQQQ B&H* columns are the raw adjusted-close buy&hold over each strategy's own live window.
- Dividends are reflected via adjusted-close cache for single names/ETFs where the source provides them; leveraged ETFs (TQQQ/SQQQ) use their own listed history.
- MaxDD/CAGR are on the unit book. Sharpe/MDD are reported but are NOT a fail reason in Lane A — only the −95% bankruptcy guard is.
- New strategies plug in via `src/toss_trader/paperlab_strategies/` (hook for docs/aggressive_strategy_catalog.md candidates).

- **Contributions**: `nine_sig` +$35 added to the **$36 book only** (monthly, first session of each month; the $1k book stays contribution-free). The $36 *Total* is therefore money-weighted (deposits inflate it) and is not directly comparable to the $1k *Total*.
