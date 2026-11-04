# Forward Paper Strategy Comparison

- Generated: `2026-11-04T21:26:35.018198+00:00`
- Session date: `2026-11-05`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.42 | fresh |
| EFA | $105.76 | fresh |
| GLD | $372.30 | fresh |
| IEF | $91.65 | fresh |
| IWM | $284.42 | fresh |
| QQQ | $693.30 | fresh |
| SCHD | $36.14 | fresh |
| SPY | $768.10 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $96.05 | -3.95% | -1.79% | $0.00 | 3 | $0.2991 | -6.08% | -0.89 | GLD $13.91 (-6.99%), QQQ $55.46 (-7.29%), SCHD $26.68 (+7.06%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $93.21 | -6.79% | -5.22% | $0.00 | 3 | $0.8513 | -10.10% | -2.05 | IWM $93.21 (+1.59%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $92.43 | -7.57% | -6.02% | $0.00 | 1 | $0.2991 | -11.74% | -2.11 | QQQ $92.43 (-7.29%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.83 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
