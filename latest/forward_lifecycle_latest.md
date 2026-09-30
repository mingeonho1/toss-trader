# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-09-30T00:42:53.098255+00:00`
- Price source: `live Toss`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `300`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $739.69 |
| QLD | $95.66 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $97.45 | $100.00 | -2.55% | 2.000 | $0.00 | QLD:1.0187 |  |
| dca_qqq | $98.62 | $100.00 | -1.38% | n/a | $0.00 | QQQ:0.1333 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
