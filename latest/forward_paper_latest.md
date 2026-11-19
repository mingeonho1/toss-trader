# Forward Paper Strategy Comparison

- Generated: `2026-11-19T22:06:16.021357+00:00`
- Session date: `2026-11-20`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.57 | fresh |
| EFA | $98.51 | fresh |
| GLD | $332.63 | fresh |
| IEF | $90.77 | fresh |
| IWM | $264.04 | fresh |
| QQQ | $644.25 | fresh |
| SCHD | $34.13 | fresh |
| SPY | $715.04 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $89.17 | -10.83% | -8.83% | $0.00 | 3 | $0.2991 | -10.15% | -2.58 | GLD $12.43 (-16.90%), QQQ $51.53 (-13.85%), SCHD $25.20 (+1.12%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $86.53 | -13.47% | -12.01% | $0.00 | 3 | $0.8513 | -14.44% | -2.86 | IWM $86.53 (-5.69%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $85.89 | -14.11% | -12.67% | $0.00 | 1 | $0.2991 | -15.08% | -2.53 | QQQ $85.89 (-13.85%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.02 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
