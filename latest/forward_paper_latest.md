# Forward Paper Strategy Comparison

- Generated: `2026-10-31T05:14:55.255472+00:00`
- Session date: `2026-10-31`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.69 | fresh |
| EFA | $103.48 | fresh |
| GLD | $356.10 | fresh |
| IEF | $91.72 | fresh |
| IWM | $279.82 | fresh |
| QQQ | $692.69 | fresh |
| SCHD | $35.79 | fresh |
| SPY | $757.06 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $95.14 | -4.86% | -2.72% | $0.00 | 3 | $0.2991 | -4.13% | -2.16 | GLD $13.30 (-11.04%), QQQ $55.41 (-7.37%), SCHD $26.43 (+6.02%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $92.35 | -7.65% | -6.10% | $0.00 | 1 | $0.2991 | -8.69% | -2.89 | QQQ $92.35 (-7.37%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $92.35 | -7.65% | -6.10% | $0.00 | 1 | $0.2991 | -8.69% | -2.89 | QQQ $92.35 (-7.37%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -5.24 | cash |  |

## Latest Virtual Trades

- SMA 20/60 trend top3: SELL SPY 0.119548 @ $756.68 cost $0.2714

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
