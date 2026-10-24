# Forward Paper Strategy Comparison

- Generated: `2026-10-24T21:36:02.047124+00:00`
- Session date: `2026-10-25`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.63 | fresh |
| EFA | $102.63 | fresh |
| GLD | $366.61 | fresh |
| IEF | $91.05 | fresh |
| IWM | $275.75 | fresh |
| QQQ | $704.40 | fresh |
| SCHD | $35.27 | fresh |
| SPY | $759.97 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $96.09 | -3.91% | -1.76% | $0.00 | 3 | $0.2991 | -3.17% | -1.86 | GLD $13.70 (-8.41%), QQQ $56.35 (-5.81%), SCHD $26.04 (+4.48%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $93.91 | -6.09% | -4.51% | $0.00 | 1 | $0.2991 | -7.20% | -2.72 | QQQ $93.91 (-5.81%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $93.91 | -6.09% | -4.51% | $0.00 | 1 | $0.2991 | -7.20% | -2.72 | QQQ $93.91 (-5.81%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $93.61 | -6.39% | -3.89% | $93.61 | 10 | $1.3388 | -5.77% | -3.31 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
