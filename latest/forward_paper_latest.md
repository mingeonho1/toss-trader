# Forward Paper Strategy Comparison

- Generated: `2026-11-11T21:26:12.944697+00:00`
- Session date: `2026-11-12`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.50 | fresh |
| EFA | $104.66 | fresh |
| GLD | $365.15 | fresh |
| IEF | $90.94 | fresh |
| IWM | $283.09 | fresh |
| QQQ | $689.94 | fresh |
| SCHD | $36.01 | fresh |
| SPY | $760.04 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $95.42 | -4.58% | -2.44% | $0.00 | 3 | $0.2991 | -7.27% | -0.77 | GLD $13.64 (-8.77%), QQQ $55.19 (-7.74%), SCHD $26.59 (+6.67%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $92.78 | -7.22% | -5.66% | $0.00 | 3 | $0.8513 | -12.19% | -1.48 | IWM $92.78 (+1.12%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $91.98 | -8.02% | -6.47% | $0.00 | 1 | $0.2991 | -14.57% | -1.38 | QQQ $91.98 (-7.74%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.40 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
