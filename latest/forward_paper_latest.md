# Forward Paper Strategy Comparison

- Generated: `2026-10-06T23:31:13.671546+00:00`
- Session date: `2026-10-07`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.44 | fresh |
| EFA | $104.03 | fresh |
| GLD | $379.55 | fresh |
| IEF | $88.92 | fresh |
| IWM | $283.38 | fresh |
| QQQ | $756.20 | fresh |
| SCHD | $32.72 | fresh |
| SPY | $774.83 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $98.83 | -1.17% | +1.05% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.18 (-5.18%), QQQ $60.49 (+1.12%), SCHD $24.16 (-3.07%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $100.82 | +0.82% | +2.51% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $100.82 (+1.12%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $100.82 | +0.82% | +2.51% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $100.82 (+1.12%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $99.02 | -0.98% | +1.67% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $49.82 (+1.46%), SPY $49.20 (+0.46%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
