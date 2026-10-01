# Forward Paper Strategy Comparison

- Generated: `2026-10-01T21:43:49.163520+00:00`
- Session date: `2026-10-02`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.40 | fresh |
| EFA | $102.82 | fresh |
| GLD | $382.71 | fresh |
| IEF | $89.11 | fresh |
| IWM | $279.33 | fresh |
| QQQ | $741.83 | fresh |
| SCHD | $32.72 | fresh |
| SPY | $763.80 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.80 | -2.20% | -0.01% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.30 (-4.39%), QQQ $59.34 (-0.80%), SCHD $24.16 (-3.07%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $98.90 | -1.10% | +0.56% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $98.90 (-0.80%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $98.90 | -1.10% | +0.56% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $98.90 (-0.80%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $97.37 | -2.63% | -0.03% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $48.87 (-0.47%), SPY $48.50 (-0.97%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
