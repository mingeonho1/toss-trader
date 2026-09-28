# Forward Paper Strategy Comparison

- Generated: `2026-09-28T09:58:35.119794+00:00`
- Session date: `2026-09-28`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.62 | fresh |
| EFA | $105.56 | fresh |
| GLD | $393.41 | fresh |
| IEF | $90.00 | fresh |
| IWM | $281.97 | fresh |
| QQQ | $744.50 | fresh |
| SCHD | $33.21 | fresh |
| SPY | $771.35 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $98.77 | -1.23% | n/a | $0.00 | 3 | $0.2991 | n/a | n/a | GLD $14.70 (-1.71%), QQQ $59.55 (-0.45%), SCHD $24.52 (-1.62%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $99.26 | -0.74% | n/a | $0.00 | 1 | $0.2991 | n/a | n/a | QQQ $99.26 (-0.45%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $99.26 | -0.74% | n/a | $0.00 | 1 | $0.2991 | n/a | n/a | QQQ $99.26 (-0.45%) |  |
| SMA 20/60 trend top3 | rebalance | GLD:33%, QQQ:33%, SPY:33% | $98.88 | -1.12% | n/a | $0.00 | 3 | $0.2991 | n/a | n/a | GLD $32.66 (-1.71%), QQQ $33.09 (-0.45%), SPY $33.13 (-0.31%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
