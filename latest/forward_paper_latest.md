# Forward Paper Strategy Comparison

- Generated: `2026-10-29T21:26:22.959865+00:00`
- Session date: `2026-10-30`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.67 | fresh |
| EFA | $104.15 | fresh |
| GLD | $368.21 | fresh |
| IEF | $91.47 | fresh |
| IWM | $283.81 | fresh |
| QQQ | $704.82 | fresh |
| SCHD | $35.77 | fresh |
| SPY | $764.34 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $96.55 | -3.45% | -1.28% | $0.00 | 3 | $0.2991 | -3.17% | -1.10 | GLD $13.76 (-8.01%), QQQ $56.38 (-5.75%), SCHD $26.41 (+5.96%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $93.97 | -6.03% | -4.45% | $0.00 | 1 | $0.2991 | -7.20% | -2.18 | QQQ $93.97 (-5.75%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $93.97 | -6.03% | -4.45% | $0.00 | 1 | $0.2991 | -7.20% | -2.18 | QQQ $93.97 (-5.75%) |  |
| SMA 20/60 trend top3 | rebalance | SPY:100% | $91.38 | -8.62% | -6.18% | $0.00 | 15 | $2.1686 | -8.01% | -4.57 | SPY $91.38 (-0.44%) |  |

## Latest Virtual Trades

- SMA 20/60 trend top3: SELL QQQ 0.064426 @ $704.47 cost $0.1362
- SMA 20/60 trend top3: BUY SPY 0.058995 @ $764.72 cost $0.1353

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
