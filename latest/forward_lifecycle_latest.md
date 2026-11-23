# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-11-23T21:26:16.762193+00:00`
- Price source: `live Toss`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `298`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $650.46 |
| QLD | $71.78 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $129.18 | $170.00 | -24.01% | 2.000 | $0.00 | QLD:1.7996 |  |
| dca_qqq | $150.05 | $170.00 | -11.73% | n/a | $0.00 | QQQ:0.2307 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
