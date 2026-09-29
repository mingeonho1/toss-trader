# Aggressive Forward Paper Lab — Leaderboard

- Generated: `2026-09-29T02:12:09+00:00`
- **PAPER (forward, live prices going forward). Real orders: none, ever.** Signal at close t → fill at t+1 (close, or open where noted). Cash earns 0%.
- Two books per strategy: **unit $1,000** (clean performance) and **real $36** (true small-scale Toss fees). Leaderboard ranks by the unit book's total return.
- Fees: Toss exact — buys ≤$10 free, sells 0.1% + SEC/TAF minimums.
- Lane A objective (docs/gate_v2_spec.md 부록 v3): cost-adjusted CAGR; bankruptcy guard only (unit MDD > −95% ⇒ FAIL). Live conversion needs cumulative return > QQQ over ≥3 months + 0 ops errors + user approval.

## Strategies — grouped by real-account executability, sorted by unit-book total return

### 실계좌 가능(비레버리지) (12)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mom_top5_ndx` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `qqq_bh` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `overnight_qqq` | open | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 1 | $0.800 | +0.00% | $0.000 | +0.00% | +0.00% |
| `ep_gap_swing` | open | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `ftlt_1x` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `lrs200_qqq` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `hibeta_basket` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `ftlt_hibeta` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `ftlt_hibeta_psq` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `holygrail_hibeta` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `simple_hibeta` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `buffer_hibeta` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |

### 레버리지 ETP(예탁금 필요) (14)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `lrr_tqqq` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `lrr_tqqq_sqqq` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `rsi2_tqqq` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `voltarget_3x` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `hot_rvol_swing` | open | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `tqqq_bh` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `overnight_tqqq` | open | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 1 | $0.800 | +0.00% | $0.000 | +0.00% | +0.00% |
| `ftlt` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `ftlt_moc` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 1 | $0.990 | +0.00% | $0.030 | +0.00% | +0.00% |
| `holy_grail` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `simple_rsi_uvxy` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `lrs200_tqqq` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `sma200_buffer_tqqq` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |
| `nine_sig` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |

### 탐색용 (1)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_proxy_mstr_coin` | close | 2026-09-28 | 1 | +0.00% | n/a | n/a | +0.00% | 0 | $0.000 | +0.00% | $0.000 | +0.00% | +0.00% |


## Daily returns (last 10 sessions)

- (no forward sessions yet.)

## Notes

- `qqq_bh` / `tqqq_bh` are benchmark strategies run through the same broker (so their small fee drag is visible); the *vs QQQ/TQQQ B&H* columns are the raw adjusted-close buy&hold over each strategy's own live window.
- Dividends are reflected via adjusted-close cache for single names/ETFs where the source provides them; leveraged ETFs (TQQQ/SQQQ) use their own listed history.
- MaxDD/CAGR are on the unit book. Sharpe/MDD are reported but are NOT a fail reason in Lane A — only the −95% bankruptcy guard is.
- New strategies plug in via `src/toss_trader/paperlab_strategies/` (hook for docs/aggressive_strategy_catalog.md candidates).
