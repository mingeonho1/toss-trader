# Forward Paper Strategy Comparison

- Generated: `2026-10-05T21:26:22.611055+00:00`
- Session date: `2026-10-06`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.44 | fresh |
| EFA | $104.20 | fresh |
| GLD | $379.57 | fresh |
| IEF | $88.91 | fresh |
| IWM | $283.29 | fresh |
| QQQ | $756.34 | fresh |
| SCHD | $32.73 | fresh |
| SPY | $774.97 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $98.85 | -1.15% | +1.07% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.18 (-5.17%), QQQ $60.50 (+1.14%), SCHD $24.17 (-3.04%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $100.83 | +0.83% | +2.53% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $100.83 (+1.14%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $100.83 | +0.83% | +2.53% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $100.83 (+1.14%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $99.03 | -0.97% | +1.68% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $49.83 (+1.48%), SPY $49.21 (+0.47%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
