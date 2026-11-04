# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-11-04T21:26:36.791551+00:00`
- Price source: `live Toss`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `298`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $693.30 |
| QLD | $83.18 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $149.69 | $170.00 | -11.95% | 2.000 | $0.00 | QLD:1.7996 |  |
| dca_qqq | $159.94 | $170.00 | -5.92% | n/a | $0.00 | QQQ:0.2307 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
