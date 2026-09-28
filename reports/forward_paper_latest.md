# Forward Paper Strategy Comparison

- Generated: `2026-09-28T03:10:07.126607+00:00`
- Session date: `2026-09-28`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.57 | fresh |
| EFA | $105.56 | fresh |
| GLD | $400.07 | fresh |
| IEF | $91.16 | fresh |
| IWM | $287.21 | fresh |
| QQQ | $747.46 | fresh |
| SCHD | $33.74 | fresh |
| SPY | $773.38 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $99.65 | -0.35% | n/a | $0.00 | 3 | $0.2991 | n/a | n/a | GLD $14.95 (-0.05%), QQQ $59.79 (-0.05%), SCHD $24.91 (-0.05%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $99.65 | -0.35% | n/a | $0.00 | 1 | $0.2991 | n/a | n/a | QQQ $99.65 (-0.05%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $99.65 | -0.35% | n/a | $0.00 | 1 | $0.2991 | n/a | n/a | QQQ $99.65 (-0.05%) |  |
| SMA 20/60 trend top3 | rebalance | GLD:33%, QQQ:33%, SPY:33% | $99.65 | -0.35% | n/a | $0.00 | 3 | $0.2991 | n/a | n/a | GLD $33.22 (-0.05%), QQQ $33.22 (-0.05%), SPY $33.22 (-0.05%) |  |

## Latest Virtual Trades

- Lump-sum ETF baseline QQQ60/SCHD25/GLD15: BUY QQQ 0.079992 @ $747.83 cost $0.1795
- Lump-sum ETF baseline QQQ60/SCHD25/GLD15: BUY SCHD 0.738375 @ $33.76 cost $0.0748
- Lump-sum ETF baseline QQQ60/SCHD25/GLD15: BUY GLD 0.037363 @ $400.27 cost $0.0449
- Dual momentum top1 -> IEF: BUY QQQ 0.133320 @ $747.83 cost $0.2991
- QQQ 200d regime filter -> IEF: BUY QQQ 0.133320 @ $747.83 cost $0.2991
- SMA 20/60 trend top3: BUY GLD 0.083028 @ $400.27 cost $0.0997
- SMA 20/60 trend top3: BUY SPY 0.042950 @ $773.77 cost $0.0997
- SMA 20/60 trend top3: BUY QQQ 0.044440 @ $747.83 cost $0.0997

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
