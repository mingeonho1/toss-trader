# Forward Paper Strategy Comparison

- Generated: `2026-11-26T21:25:52.306806+00:00`
- Session date: `2026-11-27`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.64 | fresh |
| EFA | $99.18 | fresh |
| GLD | $326.42 | fresh |
| IEF | $92.01 | fresh |
| IWM | $270.27 | fresh |
| QQQ | $636.57 | fresh |
| SCHD | $35.39 | fresh |
| SPY | $720.44 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $89.25 | -10.75% | -8.75% | $0.00 | 3 | $0.2991 | -12.62% | -2.00 | GLD $12.20 (-18.45%), QQQ $50.92 (-14.88%), SCHD $26.13 (+4.84%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $88.58 | -11.42% | -9.94% | $0.00 | 3 | $0.8513 | -16.12% | -2.02 | IWM $88.58 (-3.46%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $84.87 | -15.13% | -13.71% | $0.00 | 1 | $0.2991 | -18.57% | -2.16 | QQQ $84.87 (-14.88%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -3.76 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
