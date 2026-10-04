# Aggressive Forward Paper Lab — Leaderboard

- Generated: `2026-10-03T19:58:58+00:00`
- **PAPER (forward, live prices going forward). Real orders: none, ever.** Signal at close t → fill at t+1 (close, or open where noted). Cash earns 0%.
- Two books per strategy: **unit $1,000** (clean performance) and **real $36** (true small-scale Toss fees). Leaderboard ranks by the unit book's total return.
- Fees: Toss exact — buys ≤$10 free, sells 0.1% + SEC/TAF minimums.
- Lane A objective (docs/gate_v2_spec.md 부록 v3): cost-adjusted CAGR; bankruptcy guard only (unit MDD > −95% ⇒ FAIL). Live conversion needs cumulative return > QQQ over ≥3 months + 0 ops errors + user approval.

## Strategies — grouped by real-account executability, sorted by unit-book total return

### 실계좌 가능(비레버리지) (12)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hibeta_basket` | close | 2026-09-28 | 5 | +3.21% | +1.39% | +1685.78% | -0.33% | 10 | $0.900 | +3.30% | $0.000 | +1.77% | +5.15% |
| `ftlt_hibeta` | close | 2026-09-28 | 5 | +3.21% | +1.39% | +1685.78% | -0.33% | 10 | $0.900 | +3.30% | $0.000 | +1.77% | +5.15% |
| `ftlt_hibeta_psq` | close | 2026-09-28 | 5 | +3.21% | +1.39% | +1685.78% | -0.33% | 10 | $0.900 | +3.30% | $0.000 | +1.77% | +5.15% |
| `holygrail_hibeta` | close | 2026-09-28 | 5 | +3.21% | +1.39% | +1685.78% | -0.33% | 10 | $0.900 | +3.30% | $0.000 | +1.77% | +5.15% |
| `simple_hibeta` | close | 2026-09-28 | 5 | +3.21% | +1.39% | +1685.78% | -0.33% | 10 | $0.900 | +3.30% | $0.000 | +1.77% | +5.15% |
| `buffer_hibeta` | close | 2026-09-28 | 5 | +3.21% | +1.39% | +1685.78% | -0.33% | 10 | $0.900 | +3.30% | $0.000 | +1.77% | +5.15% |
| `overnight_qqq` | open | 2026-09-28 | 5 | +1.69% | +1.06% | +360.17% | +0.00% | 9 | $8.220 | +1.87% | $0.200 | +1.77% | +5.15% |
| `qqq_bh` | close | 2026-09-28 | 5 | +1.48% | +1.02% | +281.85% | -0.10% | 1 | $0.990 | +1.49% | $0.030 | +1.77% | +5.15% |
| `ftlt_1x` | close | 2026-09-28 | 5 | +1.48% | +1.02% | +281.85% | -0.10% | 1 | $0.990 | +1.49% | $0.030 | +1.77% | +5.15% |
| `lrs200_qqq` | close | 2026-09-28 | 5 | +1.48% | +1.02% | +281.85% | -0.10% | 1 | $0.990 | +1.49% | $0.030 | +1.77% | +5.15% |
| `mom_top5_ndx` | close | 2026-09-28 | 5 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +1.77% | +5.15% |
| `ep_gap_swing` | open | 2026-09-28 | 5 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +1.77% | +5.15% |

### 레버리지 ETP(예탁금 필요) (14)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `overnight_tqqq` | open | 2026-09-28 | 5 | +6.37% | +3.43% | +27969.74% | +0.00% | 9 | $8.410 | +6.58% | $0.200 | +1.77% | +5.15% |
| `ftlt_moc` | close | 2026-09-28 | 5 | +5.15% | +2.90% | +9731.84% | +0.00% | 1 | $0.990 | +5.15% | $0.030 | +1.77% | +5.15% |
| `lrr_tqqq` | close | 2026-09-28 | 5 | +4.48% | +2.90% | +5366.99% | -0.10% | 1 | $0.990 | +4.50% | $0.030 | +1.77% | +5.15% |
| `lrr_tqqq_sqqq` | close | 2026-09-28 | 5 | +4.48% | +2.90% | +5366.99% | -0.10% | 1 | $0.990 | +4.50% | $0.030 | +1.77% | +5.15% |
| `tqqq_bh` | close | 2026-09-28 | 5 | +4.48% | +2.90% | +5366.99% | -0.10% | 1 | $0.990 | +4.50% | $0.030 | +1.77% | +5.15% |
| `ftlt` | close | 2026-09-28 | 5 | +4.48% | +2.90% | +5366.99% | -0.10% | 1 | $0.990 | +4.50% | $0.030 | +1.77% | +5.15% |
| `holy_grail` | close | 2026-09-28 | 5 | +4.48% | +2.90% | +5366.99% | -0.10% | 1 | $0.990 | +4.50% | $0.030 | +1.77% | +5.15% |
| `simple_rsi_uvxy` | close | 2026-09-28 | 5 | +4.48% | +2.90% | +5366.99% | -0.10% | 1 | $0.990 | +4.50% | $0.030 | +1.77% | +5.15% |
| `lrs200_tqqq` | close | 2026-09-28 | 5 | +4.48% | +2.90% | +5366.99% | -0.10% | 1 | $0.990 | +4.50% | $0.030 | +1.77% | +5.15% |
| `sma200_buffer_tqqq` | close | 2026-09-28 | 5 | +4.48% | +2.90% | +5366.99% | -0.10% | 1 | $0.990 | +4.50% | $0.030 | +1.77% | +5.15% |
| `nine_sig` | close | 2026-09-28 | 5 | +2.49% | +1.67% | +847.54% | -0.10% | 4 | $1.060 | +99.65% | $0.060 | +1.77% | +5.15% |
| `voltarget_3x` | close | 2026-09-28 | 5 | +2.18% | +1.42% | +614.14% | -0.05% | 1 | $0.480 | +2.20% | $0.010 | +1.77% | +5.15% |
| `rsi2_tqqq` | close | 2026-09-28 | 5 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +1.77% | +5.15% |
| `hot_rvol_swing` | open | 2026-09-28 | 5 | +0.00% | +0.00% | +0.00% | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +1.77% | +5.15% |

### 탐색용 (1)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | close | 2026-09-28 | 5 | -0.22% | -1.78% | -18.13% | -1.78% | 2 | $0.980 | -0.18% | $0.020 | +1.77% | +5.15% |


## Daily returns (last 10 sessions)

| Date | `overnight_tqqq` | `ftlt_moc` | `lrr_tqqq` | `lrr_tqqq_sqqq` | `tqqq_bh` | `ftlt` | `holy_grail` | `simple_rsi_uvxy` | `lrs200_tqqq` | `sma200_buffer_tqqq` | `hibeta_basket` | `ftlt_hibeta` | `ftlt_hibeta_psq` | `holygrail_hibeta` | `simple_hibeta` | `buffer_hibeta` | `nine_sig` | `voltarget_3x` | `overnight_qqq` | `qqq_bh` | `ftlt_1x` | `lrs200_qqq` | `rsi2_tqqq` | `mom_top5_ndx` | `hot_rvol_swing` | `ep_gap_swing` | `btc_proxy_mstr_coin` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-29 | +1.23% | +0.55% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.10% | -0.09% | -0.09% | -0.09% | -0.09% | -0.09% | -0.09% | -0.10% | -0.05% | +0.31% | -0.10% | -0.10% | -0.10% | +0.00% | +0.00% | +0.00% | +0.00% | -0.10% |
| 2026-09-30 | +0.68% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | +0.74% | -0.24% | -0.24% | -0.24% | -0.24% | -0.24% | -0.24% | +0.42% | +0.36% | +0.12% | +0.25% | +0.25% | +0.25% | +0.00% | +0.00% | +0.00% | +0.00% | -1.46% |
| 2026-10-01 | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +0.90% | +2.13% | +2.13% | +2.13% | +2.13% | +2.13% | +2.13% | +0.49% | +0.44% | +0.19% | +0.31% | +0.31% | +0.31% | +0.00% | +0.00% | +0.00% | +0.00% | +3.20% |
| 2026-10-02 | +3.43% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +2.90% | +1.39% | +1.39% | +1.39% | +1.39% | +1.39% | +1.39% | +1.67% | +1.42% | +1.06% | +1.02% | +1.02% | +1.02% | +0.00% | +0.00% | +0.00% | +0.00% | -1.78% |

## Notes

- `qqq_bh` / `tqqq_bh` are benchmark strategies run through the same broker (so their small fee drag is visible); the *vs QQQ/TQQQ B&H* columns are the raw adjusted-close buy&hold over each strategy's own live window.
- Dividends are reflected via adjusted-close cache for single names/ETFs where the source provides them; leveraged ETFs (TQQQ/SQQQ) use their own listed history.
- MaxDD/CAGR are on the unit book. Sharpe/MDD are reported but are NOT a fail reason in Lane A — only the −95% bankruptcy guard is.
- New strategies plug in via `src/toss_trader/paperlab_strategies/` (hook for docs/aggressive_strategy_catalog.md candidates).

- **Contributions**: `nine_sig` +$35 added to the **$36 book only** (monthly, first session of each month; the $1k book stays contribution-free). The $36 *Total* is therefore money-weighted (deposits inflate it) and is not directly comparable to the $1k *Total*.
