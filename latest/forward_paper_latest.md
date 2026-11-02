# Forward Paper Strategy Comparison

- Generated: `2026-11-02T21:26:26.501760+00:00`
- Session date: `2026-11-03`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.41 | fresh |
| EFA | $103.80 | fresh |
| GLD | $361.64 | fresh |
| IEF | $91.44 | fresh |
| IWM | $279.47 | fresh |
| QQQ | $683.66 | fresh |
| SCHD | $35.89 | fresh |
| SPY | $753.49 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $94.70 | -5.30% | -3.18% | $0.00 | 3 | $0.2991 | -4.57% | -2.41 | GLD $13.51 (-9.65%), QQQ $54.69 (-8.58%), SCHD $26.50 (+6.32%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $91.59 | -8.41% | -6.87% | $0.00 | 3 | $0.8513 | -9.44% | -3.12 | IWM $91.59 (-0.17%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $91.15 | -8.85% | -7.32% | $0.00 | 1 | $0.2991 | -9.88% | -3.29 | QQQ $91.15 (-8.58%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.98 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
