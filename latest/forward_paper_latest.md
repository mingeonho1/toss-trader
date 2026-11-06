# Forward Paper Strategy Comparison

- Generated: `2026-11-06T22:11:40.039429+00:00`
- Session date: `2026-11-07`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.47 | fresh |
| EFA | $101.89 | fresh |
| GLD | $373.20 | fresh |
| IEF | $91.84 | fresh |
| IWM | $273.68 | fresh |
| QQQ | $656.82 | fresh |
| SCHD | $35.20 | fresh |
| SPY | $735.86 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $92.47 | -7.53% | -5.45% | $0.00 | 3 | $0.2991 | -7.27% | -2.12 | GLD $13.94 (-6.76%), QQQ $52.54 (-12.17%), SCHD $25.99 (+4.28%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $89.69 | -10.31% | -8.80% | $0.00 | 3 | $0.8513 | -12.19% | -2.80 | IWM $89.69 (-2.24%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $87.57 | -12.43% | -10.96% | $0.00 | 1 | $0.2991 | -14.57% | -2.88 | QQQ $87.57 (-12.17%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.70 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
