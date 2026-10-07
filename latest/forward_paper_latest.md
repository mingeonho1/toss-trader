# Forward Paper Strategy Comparison

- Generated: `2026-10-07T22:26:01.596640+00:00`
- Session date: `2026-10-08`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.46 | fresh |
| EFA | $102.90 | fresh |
| GLD | $376.24 | fresh |
| IEF | $89.06 | fresh |
| IWM | $277.71 | fresh |
| QQQ | $758.61 | fresh |
| SCHD | $32.70 | fresh |
| SPY | $777.35 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $98.88 | -1.12% | +1.10% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.06 (-6.00%), QQQ $60.68 (+1.44%), SCHD $24.14 (-3.14%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $101.14 | +1.14% | +2.84% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $101.14 (+1.44%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $101.14 | +1.14% | +2.84% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $101.14 (+1.44%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $99.34 | -0.66% | +1.99% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $49.98 (+1.79%), SPY $49.36 (+0.78%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
