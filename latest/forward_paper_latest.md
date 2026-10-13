# Forward Paper Strategy Comparison

- Generated: `2026-10-13T01:13:02.836417+00:00`
- Session date: `2026-10-13`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.50 | stale cache |
| EFA | $103.05 | stale cache |
| GLD | $382.15 | stale cache |
| IEF | $89.40 | stale cache |
| IWM | $277.90 | stale cache |
| QQQ | $751.27 | stale cache |
| SCHD | $33.26 | stale cache |
| SPY | $778.57 | stale cache |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $98.93 | -1.07% | +1.15% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.28 (-4.53%), QQQ $60.10 (+0.46%), SCHD $24.56 (-1.47%) | stale price; trades skipped for GLD,QQQ,SCHD |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $100.16 | +0.16% | +1.84% | $0.00 | 1 | $0.2991 | -0.97% | n/a | QQQ $100.16 (+0.46%) | stale price; trades skipped for QQQ |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $100.16 | +0.16% | +1.84% | $0.00 | 1 | $0.2991 | -0.97% | n/a | QQQ $100.16 (+0.46%) | stale price; trades skipped for QQQ |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $98.93 | -1.07% | +1.58% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $49.50 (+0.80%), SPY $49.43 (+0.94%) | stale price; trades skipped for QQQ,SPY |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
