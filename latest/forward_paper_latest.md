# Forward Paper Strategy Comparison

- Generated: `2026-11-24T22:02:19.504637+00:00`
- Session date: `2026-11-25`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.62 | fresh |
| EFA | $98.80 | fresh |
| GLD | $325.94 | fresh |
| IEF | $91.62 | fresh |
| IWM | $267.14 | fresh |
| QQQ | $637.90 | fresh |
| SCHD | $34.85 | fresh |
| SPY | $718.03 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $88.94 | -11.06% | -9.07% | $0.00 | 3 | $0.2991 | -12.62% | -2.12 | GLD $12.18 (-18.57%), QQQ $51.03 (-14.70%), SCHD $25.73 (+3.24%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $87.55 | -12.45% | -10.98% | $0.00 | 3 | $0.8513 | -16.12% | -2.31 | IWM $87.55 (-4.58%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $85.04 | -14.96% | -13.53% | $0.00 | 1 | $0.2991 | -18.57% | -2.17 | QQQ $85.04 (-14.70%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -3.83 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
