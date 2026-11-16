# Forward Paper Strategy Comparison

- Generated: `2026-11-15T23:30:30.687957+00:00`
- Session date: `2026-11-16`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.53 | fresh |
| EFA | $103.82 | fresh |
| GLD | $355.66 | fresh |
| IEF | $90.38 | fresh |
| IWM | $275.55 | fresh |
| QQQ | $674.26 | fresh |
| SCHD | $36.07 | fresh |
| SPY | $749.60 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $93.86 | -6.14% | -4.04% | $0.00 | 3 | $0.2991 | -7.27% | -1.25 | GLD $13.29 (-11.14%), QQQ $53.94 (-9.84%), SCHD $26.63 (+6.85%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $90.31 | -9.69% | -8.18% | $0.00 | 3 | $0.8513 | -12.19% | -2.03 | IWM $90.31 (-1.58%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $89.89 | -10.11% | -8.60% | $0.00 | 1 | $0.2991 | -14.57% | -1.77 | QQQ $89.89 (-9.84%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.20 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
