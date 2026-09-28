# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-09-28T09:58:35.174825+00:00`
- Price source: `cached Nasdaq daily close`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `300`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $744.50 |
| QLD | $96.95 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $98.76 | $100.00 | -1.24% | 2.000 | $0.00 | QLD:1.0187 |  |
| dca_qqq | $99.26 | $100.00 | -0.74% | n/a | $0.00 | QQQ:0.1333 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
