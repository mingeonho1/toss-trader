# Forward Paper Strategy Comparison

- Generated: `2026-11-05T23:03:35.824437+00:00`
- Session date: `2026-11-06`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.43 | fresh |
| EFA | $101.59 | fresh |
| GLD | $374.41 | fresh |
| IEF | $92.05 | fresh |
| IWM | $270.01 | fresh |
| QQQ | $645.16 | fresh |
| SCHD | $35.34 | fresh |
| SPY | $730.63 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $91.69 | -8.31% | -6.25% | $0.00 | 3 | $0.2991 | -7.60% | -2.41 | GLD $13.99 (-6.46%), QQQ $51.61 (-13.73%), SCHD $26.09 (+4.69%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $88.49 | -11.51% | -10.02% | $0.00 | 3 | $0.8513 | -12.51% | -3.22 | IWM $88.49 (-3.55%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $86.01 | -13.99% | -12.54% | $0.00 | 1 | $0.2991 | -14.96% | -3.34 | QQQ $86.01 (-13.73%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.76 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
