# Forward Paper Strategy Comparison

- Generated: `2026-09-29T01:07:01.456896+00:00`
- Session date: `2026-09-29`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.63 | stale cache |
| EFA | $105.31 | stale cache |
| GLD | $381.09 | stale cache |
| IEF | $89.56 | stale cache |
| IWM | $280.25 | stale cache |
| QQQ | $737.68 | stale cache |
| SCHD | $33.26 | stale cache |
| SPY | $767.65 | stale cache |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.80 | -2.20% | -0.00% | $0.00 | 3 | $0.2991 | -0.00% | n/a | GLD $14.24 (-4.79%), QQQ $59.01 (-1.36%), SCHD $24.56 (-1.47%) | stale price; trades skipped for GLD,QQQ,SCHD |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $98.35 | -1.65% | +0.00% | $0.00 | 1 | $0.2991 | +0.00% | n/a | QQQ $98.35 (-1.36%) | stale price; trades skipped for QQQ |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $98.35 | -1.65% | +0.00% | $0.00 | 1 | $0.2991 | +0.00% | n/a | QQQ $98.35 (-1.36%) | stale price; trades skipped for QQQ |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $97.39 | -2.61% | +0.00% | $0.00 | 3 | $0.2991 | +0.00% | n/a | GLD $31.64 (-4.79%), QQQ $32.78 (-1.36%), SPY $32.97 (-0.79%) | stale price; trades skipped for GLD,QQQ,SPY |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
