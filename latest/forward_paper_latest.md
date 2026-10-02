# Forward Paper Strategy Comparison

- Generated: `2026-10-02T04:06:27.726275+00:00`
- Session date: `2026-10-02`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.43 | fresh |
| EFA | $103.09 | fresh |
| GLD | $383.43 | fresh |
| IEF | $89.30 | fresh |
| IWM | $279.92 | fresh |
| QQQ | $745.14 | fresh |
| SCHD | $32.75 | fresh |
| SPY | $766.24 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $98.11 | -1.89% | +0.31% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.33 (-4.21%), QQQ $59.61 (-0.36%), SCHD $24.18 (-2.98%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $99.34 | -0.66% | +1.01% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $99.34 (-0.36%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $99.34 | -0.66% | +1.01% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $99.34 (-0.36%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $97.74 | -2.26% | +0.36% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $49.09 (-0.02%), SPY $48.65 (-0.66%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
