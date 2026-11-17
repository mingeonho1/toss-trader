# Forward Paper Strategy Comparison

- Generated: `2026-11-17T21:25:38.640362+00:00`
- Session date: `2026-11-18`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.55 | fresh |
| EFA | $102.64 | fresh |
| GLD | $352.93 | fresh |
| IEF | $90.24 | fresh |
| IWM | $272.94 | fresh |
| QQQ | $661.92 | fresh |
| SCHD | $35.86 | fresh |
| SPY | $739.95 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $92.61 | -7.39% | -5.31% | $0.00 | 3 | $0.2991 | -7.27% | -1.62 | GLD $13.19 (-11.83%), QQQ $52.95 (-11.49%), SCHD $26.48 (+6.23%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $89.45 | -10.55% | -9.05% | $0.00 | 3 | $0.8513 | -12.19% | -2.21 | IWM $89.45 (-2.51%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $88.25 | -11.75% | -10.27% | $0.00 | 1 | $0.2991 | -14.57% | -2.09 | QQQ $88.25 (-11.49%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.11 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
