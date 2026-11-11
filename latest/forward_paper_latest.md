# Forward Paper Strategy Comparison

- Generated: `2026-11-11T00:22:33.618863+00:00`
- Session date: `2026-11-11`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.49 | fresh |
| EFA | $104.51 | fresh |
| GLD | $370.30 | fresh |
| IEF | $90.98 | fresh |
| IWM | $280.95 | fresh |
| QQQ | $677.22 | fresh |
| SCHD | $35.87 | fresh |
| SPY | $751.47 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $94.49 | -5.51% | -3.39% | $0.00 | 3 | $0.2991 | -7.27% | -1.14 | GLD $13.84 (-7.49%), QQQ $54.17 (-9.44%), SCHD $26.49 (+6.26%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $92.08 | -7.92% | -6.38% | $0.00 | 3 | $0.8513 | -12.19% | -1.71 | IWM $92.08 (+0.35%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $90.29 | -9.71% | -8.20% | $0.00 | 1 | $0.2991 | -14.57% | -1.85 | QQQ $90.29 (-9.44%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.45 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
