# Forward Paper Strategy Comparison

- Generated: `2026-11-27T21:26:16.917570+00:00`
- Session date: `2026-11-28`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.67 | fresh |
| EFA | $101.00 | fresh |
| GLD | $335.11 | fresh |
| IEF | $91.94 | fresh |
| IWM | $274.90 | fresh |
| QQQ | $650.60 | fresh |
| SCHD | $35.83 | fresh |
| SPY | $730.81 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $91.02 | -8.98% | -6.94% | $0.00 | 3 | $0.2991 | -12.62% | -1.49 | GLD $12.52 (-16.28%), QQQ $52.04 (-13.00%), SCHD $26.46 (+6.14%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $90.09 | -9.91% | -8.39% | $0.00 | 3 | $0.8513 | -16.12% | -1.63 | IWM $90.09 (-1.81%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $86.74 | -13.26% | -11.80% | $0.00 | 1 | $0.2991 | -18.57% | -1.77 | QQQ $86.74 (-13.00%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -3.73 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
