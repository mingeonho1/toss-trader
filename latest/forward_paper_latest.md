# Forward Paper Strategy Comparison

- Generated: `2026-10-23T21:42:41.513260+00:00`
- Session date: `2026-10-24`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.63 | fresh |
| EFA | $102.83 | fresh |
| GLD | $366.37 | fresh |
| IEF | $91.10 | fresh |
| IWM | $275.96 | fresh |
| QQQ | $705.48 | fresh |
| SCHD | $35.28 | fresh |
| SPY | $761.05 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $96.17 | -3.83% | -1.67% | $0.00 | 3 | $0.2991 | -3.09% | -1.80 | GLD $13.69 (-8.47%), QQQ $56.43 (-5.66%), SCHD $26.05 (+4.51%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $94.05 | -5.95% | -4.36% | $0.00 | 1 | $0.2991 | -7.20% | -2.68 | QQQ $94.05 (-5.66%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $94.05 | -5.95% | -4.36% | $0.00 | 1 | $0.2991 | -7.20% | -2.68 | QQQ $94.05 (-5.66%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $93.61 | -6.39% | -3.89% | $93.61 | 10 | $1.3388 | -5.77% | -3.38 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
