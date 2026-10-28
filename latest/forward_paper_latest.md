# Forward Paper Strategy Comparison

- Generated: `2026-10-27T21:58:03.710550+00:00`
- Session date: `2026-10-28`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.65 | fresh |
| EFA | $103.52 | fresh |
| GLD | $372.29 | fresh |
| IEF | $91.08 | fresh |
| IWM | $280.24 | fresh |
| QQQ | $721.66 | fresh |
| SCHD | $35.12 | fresh |
| SPY | $770.24 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.57 | -2.43% | -0.24% | $0.00 | 3 | $0.2991 | -3.17% | -0.18 | GLD $13.91 (-6.99%), QQQ $57.73 (-3.50%), SCHD $25.93 (+4.04%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $96.21 | -3.79% | -2.17% | $0.00 | 1 | $0.2991 | -7.20% | -1.10 | QQQ $96.21 (-3.50%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $96.21 | -3.79% | -2.17% | $0.00 | 1 | $0.2991 | -7.20% | -1.10 | QQQ $96.21 (-3.50%) |  |
| SMA 20/60 trend top3 | rebalance | SPY:100% | $93.28 | -6.72% | -4.22% | $0.00 | 11 | $1.6187 | -6.09% | -3.41 | SPY $93.28 (-0.05%) |  |

## Latest Virtual Trades

- SMA 20/60 trend top3: BUY SPY 0.121107 @ $770.63 cost $0.2800

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
