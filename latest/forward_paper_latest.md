# Forward Paper Strategy Comparison

- Generated: `2026-10-28T22:30:48.432495+00:00`
- Session date: `2026-10-29`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.66 | fresh |
| EFA | $103.50 | fresh |
| GLD | $371.54 | fresh |
| IEF | $91.00 | fresh |
| IWM | $282.07 | fresh |
| QQQ | $717.63 | fresh |
| SCHD | $35.23 | fresh |
| SPY | $768.89 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.30 | -2.70% | -0.52% | $0.00 | 3 | $0.2991 | -3.17% | -0.44 | GLD $13.88 (-7.18%), QQQ $57.40 (-4.04%), SCHD $26.01 (+4.36%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $95.67 | -4.33% | -2.72% | $0.00 | 1 | $0.2991 | -7.20% | -1.38 | QQQ $95.67 (-4.04%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $95.67 | -4.33% | -2.72% | $0.00 | 1 | $0.2991 | -7.20% | -1.38 | QQQ $95.67 (-4.04%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $92.79 | -7.21% | -4.72% | $0.00 | 13 | $1.8971 | -6.59% | -3.74 | QQQ $46.23 (-0.05%), SPY $46.56 (-0.23%) |  |

## Latest Virtual Trades

- SMA 20/60 trend top3: SELL SPY 0.060553 @ $768.51 cost $0.1396
- SMA 20/60 trend top3: BUY QQQ 0.064426 @ $717.99 cost $0.1388

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
