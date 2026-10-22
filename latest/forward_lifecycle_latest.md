# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-10-22T21:26:22.017972+00:00`
- Price source: `live Toss`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `299`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $703.97 |
| QLD | $86.12 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $118.96 | $135.00 | -11.88% | 2.000 | $0.00 | QLD:1.3813 |  |
| dca_qqq | $126.95 | $135.00 | -5.96% | n/a | $0.00 | QQQ:0.1803 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
