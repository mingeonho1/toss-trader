# Forward Paper Strategy Comparison

- Generated: `2026-09-30T01:08:36.065601+00:00`
- Session date: `2026-09-30`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.64 | fresh |
| EFA | $104.54 | fresh |
| GLD | $382.70 | fresh |
| IEF | $89.53 | fresh |
| IWM | $279.56 | fresh |
| QQQ | $738.67 | fresh |
| SCHD | $32.91 | fresh |
| SPY | $765.45 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.69 | -2.31% | -0.12% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.30 (-4.39%), QQQ $59.09 (-1.23%), SCHD $24.30 (-2.51%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $98.48 | -1.52% | +0.13% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $98.48 (-1.23%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $98.48 | -1.52% | +0.13% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $98.48 (-1.23%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $97.27 | -2.73% | -0.13% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $48.66 (-0.89%), SPY $48.60 (-0.76%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
