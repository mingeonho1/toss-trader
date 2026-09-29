# Forward Paper Strategy Comparison

- Generated: `2026-09-29T05:19:19.936793+00:00`
- Session date: `2026-09-29`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.63 | stale cache |
| EFA | $105.04 | stale cache |
| GLD | $377.91 | stale cache |
| IEF | $89.53 | stale cache |
| IWM | $280.02 | stale cache |
| QQQ | $736.53 | stale cache |
| SCHD | $33.01 | stale cache |
| SPY | $765.61 | stale cache |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.41 | -2.59% | -0.40% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.12 (-5.59%), QQQ $58.92 (-1.51%), SCHD $24.37 (-2.21%) | stale price; trades skipped for GLD,QQQ,SCHD |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $98.19 | -1.81% | -0.16% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $98.19 (-1.51%) | stale price; trades skipped for QQQ |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $98.19 | -1.81% | -0.16% | $0.00 | 1 | $0.2991 | -0.16% | n/a | QQQ $98.19 (-1.51%) | stale price; trades skipped for QQQ |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $96.99 | -3.01% | -0.41% | $0.00 | 3 | $0.2991 | -0.41% | n/a | GLD $31.38 (-5.59%), QQQ $32.73 (-1.51%), SPY $32.88 (-1.05%) | stale price; trades skipped for GLD,QQQ,SPY |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
