# Forward Paper Strategy Comparison

- Generated: `2026-10-22T21:26:20.267322+00:00`
- Session date: `2026-10-23`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.61 | fresh |
| EFA | $103.15 | fresh |
| GLD | $368.90 | fresh |
| IEF | $91.27 | fresh |
| IWM | $276.33 | fresh |
| QQQ | $703.97 | fresh |
| SCHD | $35.37 | fresh |
| SPY | $764.06 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $96.21 | -3.79% | -1.63% | $0.00 | 3 | $0.2991 | -3.05% | -1.79 | GLD $13.78 (-7.84%), QQQ $56.31 (-5.87%), SCHD $26.12 (+4.78%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $93.85 | -6.15% | -4.57% | $0.00 | 1 | $0.2991 | -7.20% | -2.88 | QQQ $93.85 (-5.87%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $93.85 | -6.15% | -4.57% | $0.00 | 1 | $0.2991 | -7.20% | -2.88 | QQQ $93.85 (-5.87%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $93.61 | -6.39% | -3.89% | $93.61 | 10 | $1.3388 | -5.77% | -3.45 | cash |  |

## Latest Virtual Trades

- SMA 20/60 trend top3: SELL SPY 0.122944 @ $763.68 cost $0.2817

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
