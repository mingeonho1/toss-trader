# Aggressive Forward Paper Lab — Leaderboard

- Generated: `2026-09-28T07:08:53+00:00`
- **PAPER (forward, live prices going forward). Real orders: none, ever.** Signal at close t → fill at t+1 (close, or open where noted). Cash earns 0%.
- Two books per strategy: **unit $1,000** (clean performance) and **real $36** (true small-scale Toss fees). Leaderboard ranks by the unit book's total return.
- Fees: Toss exact — buys ≤$10 free, sells 0.1% + SEC/TAF minimums.
- Lane A objective (docs/gate_v2_spec.md 부록 v3): cost-adjusted CAGR; bankruptcy guard only (unit MDD > −95% ⇒ FAIL). Live conversion needs cumulative return > QQQ over ≥3 months + 0 ops errors + user approval.

> ⏳ **Awaiting first forward session.** All books initialized at `2026-09-28`; no keyless daily close ≥ start date has arrived yet (the cache ends earlier). Numbers populate once `scripts/paperlab_run.py` sees a session on/after the start date. For a historical sanity check, see `reports/paperlab_backfill.md` (BACKTEST).

## Strategies (sorted by unit-book total return)

| Strategy | Fill | Start | Days | Total (u$1k) | Today | CAGR | MaxDD | Trades | Fees(u) | $36 Total | $36 Fees | vs QQQ B&H | vs TQQQ B&H |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `lrr_tqqq` | close | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `lrr_tqqq_sqqq` | close | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `rsi2_tqqq` | close | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `voltarget_3x` | close | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `mom_top5_ndx` | close | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `hot_rvol_swing` | open | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `qqq_bh` | close | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `tqqq_bh` | close | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `overnight_tqqq` | open | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `overnight_qqq` | open | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |
| `ep_gap_swing` | open | 2026-09-28 | 0 | n/a | n/a | n/a | +0.00% | 0 | $0.000 | n/a | $0.000 | n/a | n/a |

## Daily returns (last 10 sessions)

- (no forward sessions yet.)

## Notes

- `qqq_bh` / `tqqq_bh` are benchmark strategies run through the same broker (so their small fee drag is visible); the *vs QQQ/TQQQ B&H* columns are the raw adjusted-close buy&hold over each strategy's own live window.
- Dividends are reflected via adjusted-close cache for single names/ETFs where the source provides them; leveraged ETFs (TQQQ/SQQQ) use their own listed history.
- MaxDD/CAGR are on the unit book. Sharpe/MDD are reported but are NOT a fail reason in Lane A — only the −95% bankruptcy guard is.
- New strategies plug in via `src/toss_trader/paperlab_strategies/` (hook for docs/aggressive_strategy_catalog.md candidates).
