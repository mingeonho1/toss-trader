# Forward Paper Strategy Comparison

- Generated: `2026-11-10T01:55:39.064374+00:00`
- Session date: `2026-11-10`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.48 | stale cache |
| EFA | $104.96 | stale cache |
| GLD | $379.53 | stale cache |
| IEF | $91.80 | stale cache |
| IWM | $282.51 | stale cache |
| QQQ | $679.35 | stale cache |
| SCHD | $35.17 | stale cache |
| SPY | $735.29 | stale cache |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $94.49 | -5.51% | -3.39% | $0.00 | 3 | $0.2991 | -7.27% | -1.15 | GLD $14.18 (-5.18%), QQQ $54.34 (-9.16%), SCHD $25.97 (+4.19%) | stale price; trades skipped for GLD,QQQ,SCHD |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $92.59 | -7.41% | -5.86% | $0.00 | 3 | $0.8513 | -12.19% | -1.58 | IWM $92.59 (+0.91%) | stale price; trades skipped for IWM |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $90.57 | -9.43% | -7.91% | $0.00 | 1 | $0.2991 | -14.57% | -1.80 | QQQ $90.57 (-9.16%) | stale price; trades skipped for QQQ |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.51 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
