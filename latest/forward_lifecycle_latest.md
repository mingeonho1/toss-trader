# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-11-10T01:55:39.722332+00:00`
- Price source: `cached Nasdaq daily close`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `298`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $679.35 |
| QLD | $79.15 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $142.44 | $170.00 | -16.21% | 2.000 | $0.00 | QLD:1.7996 |  |
| dca_qqq | $156.72 | $170.00 | -7.81% | n/a | $0.00 | QQQ:0.2307 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
