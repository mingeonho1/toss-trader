# Forward Paper Strategy Comparison

- Generated: `2026-11-20T22:45:10.804385+00:00`
- Session date: `2026-11-21`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.57 | fresh |
| EFA | $98.78 | fresh |
| GLD | $335.18 | fresh |
| IEF | $90.83 | fresh |
| IWM | $264.22 | fresh |
| QQQ | $645.04 | fresh |
| SCHD | $34.20 | fresh |
| SPY | $715.87 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $89.37 | -10.63% | -8.62% | $0.00 | 3 | $0.2991 | -10.15% | -2.49 | GLD $12.52 (-16.26%), QQQ $51.60 (-13.75%), SCHD $25.25 (+1.31%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $86.59 | -13.41% | -11.95% | $0.00 | 3 | $0.8513 | -14.44% | -2.82 | IWM $86.59 (-5.62%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $86.00 | -14.00% | -12.56% | $0.00 | 1 | $0.2991 | -15.08% | -2.48 | QQQ $86.00 (-13.75%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -3.98 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
