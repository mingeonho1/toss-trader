# Forward Paper Strategy Comparison

- Generated: `2026-11-23T21:26:15.037132+00:00`
- Session date: `2026-11-24`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.61 | fresh |
| EFA | $98.90 | fresh |
| GLD | $333.19 | fresh |
| IEF | $91.58 | fresh |
| IWM | $265.59 | fresh |
| QQQ | $650.32 | fresh |
| SCHD | $34.41 | fresh |
| SPY | $722.95 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $89.88 | -10.12% | -8.11% | $0.00 | 3 | $0.2991 | -12.62% | -1.90 | GLD $12.45 (-16.76%), QQQ $52.02 (-13.04%), SCHD $25.41 (+1.93%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $87.04 | -12.96% | -11.50% | $0.00 | 3 | $0.8513 | -16.12% | -2.47 | IWM $87.04 (-5.13%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $86.70 | -13.30% | -11.84% | $0.00 | 1 | $0.2991 | -18.57% | -1.90 | QQQ $86.70 (-13.04%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -3.87 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
