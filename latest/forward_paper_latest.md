# Forward Paper Strategy Comparison

- Generated: `2026-11-04T04:36:20.113614+00:00`
- Session date: `2026-11-04`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.42 | fresh |
| EFA | $102.56 | fresh |
| GLD | $360.98 | fresh |
| IEF | $91.50 | fresh |
| IWM | $277.44 | fresh |
| QQQ | $669.58 | fresh |
| SCHD | $35.42 | fresh |
| SPY | $744.42 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $93.20 | -6.80% | -4.71% | $0.00 | 3 | $0.2991 | -6.08% | -3.29 | GLD $13.49 (-9.82%), QQQ $53.56 (-10.46%), SCHD $26.15 (+4.93%) |  |
| Dual momentum top1 -> IEF | rebalance | IWM:100% | $90.92 | -9.08% | -7.55% | $0.00 | 3 | $0.8513 | -10.10% | -3.39 | IWM $90.92 (-0.90%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $89.27 | -10.73% | -9.23% | $0.00 | 1 | $0.2991 | -11.74% | -3.97 | QQQ $89.27 (-10.46%) |  |
| SMA 20/60 trend top3 | rebalance | cash | $90.19 | -9.81% | -7.40% | $90.19 | 16 | $2.4400 | -9.21% | -4.91 | cash |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
