# Forward Paper Strategy Comparison

- Generated: `2026-11-06T06:56:31.891979+00:00`
- Session date: `2026-11-06`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.43 | stale cache |
| EFA | $102.70 | stale cache |
| GLD | $375.49 | stale cache |
| IEF | $91.99 | stale cache |
| IWM | $270.98 | stale cache |
| QQQ | $648.06 | stale cache |
| SCHD | $35.42 | stale cache |
| SPY | $733.00 | stale cache |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $92.02 | -7.98% | -5.91% | $0.00 | 3 | $0.2991 | -7.27% | -2.36 | GLD $14.03 (-6.19%), QQQ $51.84 (-13.34%), SCHD $26.15 (+4.93%) | stale price; trades skipped for GLD,QQQ,SCHD |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $88.81 | -11.19% | -9.70% | $0.00 | 3 | $0.8513 | -12.19% | -3.19 | IWM $88.81 (-3.21%) | stale price; trades skipped for IWM |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $86.40 | -13.60% | -12.15% | $0.00 | 1 | $0.2991 | -14.57% | -3.32 | QQQ $86.40 (-13.34%) | stale price; trades skipped for QQQ |
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
