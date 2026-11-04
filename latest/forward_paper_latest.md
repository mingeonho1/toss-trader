# Forward Paper Strategy Comparison

- Generated: `2026-11-04T01:34:33.238607+00:00`
- Session date: `2026-11-04`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.42 | fresh |
| EFA | $103.08 | fresh |
| GLD | $362.33 | fresh |
| IEF | $91.50 | fresh |
| IWM | $278.80 | fresh |
| QQQ | $673.32 | fresh |
| SCHD | $35.53 | fresh |
| SPY | $748.49 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $93.63 | -6.37% | -4.27% | $0.00 | 3 | $0.2991 | -5.65% | -3.09 | GLD $13.54 (-9.48%), QQQ $53.86 (-9.96%), SCHD $26.23 (+5.25%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $91.37 | -8.63% | -7.09% | $0.00 | 3 | $0.8513 | -9.66% | -3.19 | IWM $91.37 (-0.41%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $89.77 | -10.23% | -8.72% | $0.00 | 1 | $0.2991 | -11.24% | -3.83 | QQQ $89.77 (-9.96%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.91 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
