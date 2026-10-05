# Forward Paper Strategy Comparison

- Generated: `2026-10-04T21:25:03.607586+00:00`
- Session date: `2026-10-05`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.43 | fresh |
| EFA | $103.96 | fresh |
| GLD | $380.14 | fresh |
| IEF | $89.05 | fresh |
| IWM | $281.52 | fresh |
| QQQ | $749.58 | fresh |
| SCHD | $32.72 | fresh |
| SPY | $769.64 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $98.32 | -1.68% | +0.53% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.20 (-5.03%), QQQ $59.96 (+0.23%), SCHD $24.16 (-3.07%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $99.93 | -0.07% | +1.61% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $99.93 (+0.23%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $99.93 | -0.07% | +1.61% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $99.93 (+0.23%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $98.25 | -1.75% | +0.88% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $49.38 (+0.57%), SPY $48.87 (-0.22%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
