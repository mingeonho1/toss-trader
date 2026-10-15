# Forward Paper Strategy Comparison

- Generated: `2026-10-15T00:16:24.487073+00:00`
- Session date: `2026-10-15`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.53 | fresh |
| EFA | $102.75 | fresh |
| GLD | $378.54 | fresh |
| IEF | $89.93 | fresh |
| IWM | $271.02 | fresh |
| QQQ | $722.70 | fresh |
| SCHD | $33.76 | fresh |
| SPY | $766.95 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $96.88 | -3.12% | -0.95% | $0.00 | 3 | $0.2991 | -2.37% | n/a | GLD $14.14 (-5.43%), QQQ $57.81 (-3.36%), SCHD $24.93 (+0.01%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $96.35 | -3.65% | -2.03% | $0.00 | 1 | $0.2991 | -4.73% | n/a | QQQ $96.35 (-3.36%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $96.35 | -3.65% | -2.03% | $0.00 | 1 | $0.2991 | -4.73% | n/a | QQQ $96.35 (-3.36%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $96.31 | -3.69% | -1.11% | $0.00 | 6 | $0.4893 | -3.05% | n/a | QQQ $47.61 (-3.03%), SPY $48.70 (-0.56%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
