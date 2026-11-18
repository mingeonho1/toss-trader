# Forward Paper Strategy Comparison

- Generated: `2026-11-18T21:26:21.710027+00:00`
- Session date: `2026-11-19`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.56 | fresh |
| EFA | $100.30 | fresh |
| GLD | $341.50 | fresh |
| IEF | $90.65 | fresh |
| IWM | $270.34 | fresh |
| QQQ | $660.56 | fresh |
| SCHD | $34.72 | fresh |
| SPY | $730.84 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $91.23 | -8.77% | -6.72% | $0.00 | 3 | $0.2991 | -8.06% | -2.03 | GLD $12.76 (-14.68%), QQQ $52.84 (-11.67%), SCHD $25.63 (+2.85%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $88.60 | -11.40% | -9.91% | $0.00 | 3 | $0.8513 | -12.40% | -2.41 | IWM $88.60 (-3.44%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $88.07 | -11.93% | -10.45% | $0.00 | 1 | $0.2991 | -14.57% | -2.11 | QQQ $88.07 (-11.67%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.06 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
