# Forward Paper Strategy Comparison

- Generated: `2026-11-25T21:26:10.247000+00:00`
- Session date: `2026-11-26`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.64 | fresh |
| EFA | $99.15 | fresh |
| GLD | $328.21 | fresh |
| IEF | $92.04 | fresh |
| IWM | $268.85 | fresh |
| QQQ | $633.74 | fresh |
| SCHD | $35.33 | fresh |
| SPY | $717.05 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $89.04 | -10.96% | -8.96% | $0.00 | 3 | $0.2991 | -12.62% | -2.07 | GLD $12.26 (-18.00%), QQQ $50.69 (-15.26%), SCHD $26.09 (+4.66%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $88.11 | -11.89% | -10.41% | $0.00 | 3 | $0.8513 | -16.12% | -2.15 | IWM $88.11 (-3.97%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $84.49 | -15.51% | -14.09% | $0.00 | 1 | $0.2991 | -18.57% | -2.25 | QQQ $84.49 (-15.26%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -3.79 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
