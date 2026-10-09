# Forward Paper Strategy Comparison

- Generated: `2026-10-09T23:38:31.755201+00:00`
- Session date: `2026-10-10`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.51 | fresh |
| EFA | $103.44 | fresh |
| GLD | $384.50 | fresh |
| IEF | $89.40 | fresh |
| IWM | $278.94 | fresh |
| QQQ | $751.36 | fresh |
| SCHD | $33.03 | fresh |
| SPY | $778.73 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $98.86 | -1.14% | +1.08% | $0.00 | 3 | $0.2991 | -0.40% | n/a | GLD $14.37 (-3.94%), QQQ $60.10 (+0.47%), SCHD $24.39 (-2.14%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $100.17 | +0.17% | +1.85% | $0.00 | 1 | $0.2991 | -0.96% | n/a | QQQ $100.17 (+0.47%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $100.17 | +0.17% | +1.85% | $0.00 | 1 | $0.2991 | -0.96% | n/a | QQQ $100.17 (+0.47%) |  |
| SMA 20/60 trend top3 | rebalance | QQQ:50%, SPY:50% | $98.95 | -1.05% | +1.59% | $0.00 | 6 | $0.4893 | -0.41% | n/a | QQQ $49.50 (+0.81%), SPY $49.44 (+0.96%) |  |

## Latest Virtual Trades

- No virtual trades on this run.

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
