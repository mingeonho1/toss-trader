# Forward Paper Strategy Comparison

- Generated: `2026-10-17T05:40:11.832649+00:00`
- Session date: `2026-10-17`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.56 | fresh |
| EFA | $103.70 | fresh |
| GLD | $381.40 | fresh |
| IEF | $90.60 | fresh |
| IWM | $269.98 | fresh |
| QQQ | $707.87 | fresh |
| SCHD | $34.37 | fresh |
| SPY | $761.10 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $96.25 | -3.75% | -1.59% | $0.00 | 3 | $0.2991 | -3.01% | n/a | GLD $14.25 (-4.71%), QQQ $56.62 (-5.34%), SCHD $25.38 (+1.82%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $94.37 | -5.63% | -4.04% | $0.00 | 1 | $0.2991 | -6.69% | n/a | QQQ $94.37 (-5.34%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $94.37 | -5.63% | -4.04% | $0.00 | 1 | $0.2991 | -6.69% | n/a | QQQ $94.37 (-5.34%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $94.63 | -5.37% | -2.84% | $94.63 | 8 | $0.7741 | -4.74% | n/a | cash |  |

## Latest Virtual Trades

- SMA 20/60 trend top3: SELL SPY 0.063493 @ $760.72 cost $0.1449
- SMA 20/60 trend top3: SELL QQQ 0.065882 @ $707.52 cost $0.1398

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
