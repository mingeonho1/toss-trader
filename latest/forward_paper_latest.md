# Forward Paper Strategy Comparison

- Generated: `2026-10-20T23:59:52.625858+00:00`
- Session date: `2026-10-21`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.58 | fresh |
| EFA | $103.70 | fresh |
| GLD | $377.28 | fresh |
| IEF | $90.30 | fresh |
| IWM | $276.17 | fresh |
| QQQ | $722.85 | fresh |
| SCHD | $34.65 | fresh |
| SPY | $770.69 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.50 | -2.50% | -0.31% | $0.00 | 3 | $0.2991 | -3.01% | -0.34 | GLD $14.10 (-5.74%), QQQ $57.82 (-3.34%), SCHD $25.58 (+2.64%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $96.37 | -3.63% | -2.01% | $0.00 | 1 | $0.2991 | -6.69% | -1.36 | QQQ $96.37 (-3.34%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $96.37 | -3.63% | -2.01% | $0.00 | 1 | $0.2991 | -6.69% | -1.36 | QQQ $96.37 (-3.34%) |  |
| SMA 20/60 trend top3 | rebalance | SPY:100% | $94.75 | -5.25% | -2.71% | $0.00 | 9 | $1.0571 | -5.07% | -2.56 | SPY $94.75 (+0.43%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
