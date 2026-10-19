# Forward Paper Strategy Comparison

- Generated: `2026-10-19T21:26:14.587688+00:00`
- Session date: `2026-10-20`
- Real orders: none. These are virtual fills using current prices plus the cost model.
- Cost model: roundtrip `70bps`
- Purpose: operational validation and cost/turnover observation, not statistical ranking.

## Current Prices

| Symbol | Price | Freshness |
|---|---:|---|
| BIL | $91.57 | fresh |
| EFA | $102.89 | fresh |
| GLD | $379.72 | fresh |
| IEF | $90.11 | fresh |
| IWM | $271.56 | fresh |
| QQQ | $719.09 | fresh |
| SCHD | $34.27 | fresh |
| SPY | $767.00 | fresh |

## Portfolios

| Strategy | Mode | Target | Equity | Money Return | TWR | Cash | Trades | Cost | MDD | Sharpe | Positions | Note |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lump-sum ETF baseline QQQ60/SCHD25/GLD15 | buy_once | GLD:15%, QQQ:60%, SCHD:25% | $97.01 | -2.99% | -0.81% | $0.00 | 3 | $0.2991 | -3.01% | -1.00 | GLD $14.19 (-5.13%), QQQ $57.52 (-3.84%), SCHD $25.30 (+1.52%) |  |
| Dual momentum top1 -> IEF | rebalance | QQQ:100% | $95.87 | -4.13% | -2.52% | $0.00 | 1 | $0.2991 | -6.69% | -1.79 | QQQ $95.87 (-3.84%) |  |
| QQQ 200d regime filter -> IEF | rebalance | QQQ:100% | $95.87 | -4.13% | -2.52% | $0.00 | 1 | $0.2991 | -6.69% | -1.79 | QQQ $95.87 (-3.84%) |  |
| SMA 20/60 trend top3 | rebalance | SPY:100% | $94.30 | -5.70% | -3.18% | $0.00 | 9 | $1.0571 | -5.07% | -3.13 | SPY $94.30 (-0.05%) |  |

## Latest Virtual Trades

- SMA 20/60 trend top3: BUY SPY 0.122944 @ $767.38 cost $0.2830

## Notes

- `lumpsum_etf` is a one-time ETF buy-and-hold baseline, not DCA.
- Rejected strategy candidates are not included in the default forward ledger; see the latest strategy gate report.
- Monthly/daily signal periods are stored in the state file, so watch-mode runs do not recompute monthly signals every 5 minutes.
- Stale cache prices are used for mark-to-market only; strategies with stale required symbols skip trading.
- Sharpe is shown only after at least 20 daily observations; intraday snapshots are not annualized.
- Dividends are not modeled, so SCHD/IEF-heavy strategies are structurally understated over longer windows.
