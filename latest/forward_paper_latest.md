# Forward Paper Strategy Comparison

- Generated: `2026-11-22T00:53:35.505252+00:00`
- Session date: `2026-11-22`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.59 | fresh |
| EFA | $97.15 | fresh |
| GLD | $324.45 | fresh |
| IEF | $91.41 | fresh |
| IWM | $258.85 | fresh |
| QQQ | $617.74 | fresh |
| SCHD | $34.10 | fresh |
| SPY | $697.49 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $86.71 | -13.29% | -11.34% | $0.00 | 3 | $0.2991 | -12.62% | -3.11 | GLD $12.12 (-18.94%), QQQ $49.41 (-17.40%), SCHD $25.18 (+1.01%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $84.83 | -15.17% | -13.74% | $0.00 | 3 | $0.8513 | -16.12% | -3.20 | IWM $84.83 (-7.54%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $82.36 | -17.64% | -16.26% | $0.00 | 1 | $0.2991 | -18.57% | -3.09 | QQQ $82.36 (-17.40%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -3.94 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
