# Forward Paper Strategy Comparison

- Generated: `2026-10-15T21:26:20.352878+00:00`
- Session date: `2026-10-16`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.53 | fresh |
| EFA | $103.67 | fresh |
| GLD | $382.29 | fresh |
| IEF | $90.42 | fresh |
| IWM | $274.99 | fresh |
| QQQ | $727.00 | fresh |
| SCHD | $34.10 | fresh |
| SPY | $771.49 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.62 | -2.38% | -0.19% | $0.00 | 3 | $0.2991 | -2.37% | n/a | GLD $14.28 (-4.49%), QQQ $58.15 (-2.79%), SCHD $25.18 (+1.02%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $96.92 | -3.08% | -1.45% | $0.00 | 1 | $0.2991 | -4.73% | n/a | QQQ $96.92 (-2.79%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $96.92 | -3.08% | -1.45% | $0.00 | 1 | $0.2991 | -4.73% | n/a | QQQ $96.92 (-2.79%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $96.88 | -3.12% | -0.53% | $0.00 | 6 | $0.4893 | -3.05% | n/a | QQQ $47.90 (-2.46%), SPY $48.98 (+0.02%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
