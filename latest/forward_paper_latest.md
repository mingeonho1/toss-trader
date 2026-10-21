# Forward Paper Strategy Comparison

- Generated: `2026-10-21T22:53:53.478878+00:00`
- Session date: `2026-10-22`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.58 | fresh |
| EFA | $103.69 | fresh |
| GLD | $377.65 | fresh |
| IEF | $90.23 | fresh |
| IWM | $275.78 | fresh |
| QQQ | $717.58 | fresh |
| SCHD | $34.69 | fresh |
| SPY | $769.05 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.12 | -2.88% | -0.70% | $0.00 | 3 | $0.2991 | -3.01% | -0.79 | GLD $14.11 (-5.65%), QQQ $57.40 (-4.05%), SCHD $25.61 (+2.76%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $95.67 | -4.33% | -2.72% | $0.00 | 1 | $0.2991 | -6.69% | -1.82 | QQQ $95.67 (-4.05%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $95.67 | -4.33% | -2.72% | $0.00 | 1 | $0.2991 | -6.69% | -1.82 | QQQ $95.67 (-4.05%) |  |
| SMA 20/60 trend top3 | rebalance | SPY:100% | $94.55 | -5.45% | -2.92% | $0.00 | 9 | $1.0571 | -5.07% | -2.70 | SPY $94.55 (+0.22%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
