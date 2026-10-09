# Forward Paper Strategy Comparison

- Generated: `2026-10-09T01:55:00.692699+00:00`
- Session date: `2026-10-09`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.47 | fresh |
| EFA | $102.70 | fresh |
| GLD | $378.62 | fresh |
| IEF | $89.45 | fresh |
| IWM | $277.70 | fresh |
| QQQ | $757.73 | fresh |
| SCHD | $33.15 | fresh |
| SPY | $777.22 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $99.24 | -0.76% | +1.46% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.15 (-5.41%), QQQ $60.61 (+1.32%), SCHD $24.48 (-1.80%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $101.02 | +1.02% | +2.72% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $101.02 (+1.32%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $101.02 | +1.02% | +2.72% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $101.02 (+1.32%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $99.27 | -0.73% | +1.92% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $49.92 (+1.67%), SPY $49.35 (+0.77%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
