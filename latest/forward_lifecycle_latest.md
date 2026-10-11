# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-10-11T00:55:34.355505+00:00`
- Price source: `live Toss`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `299`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $751.38 |
| QLD | $98.49 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $136.04 | $135.00 | +0.77% | 2.000 | $0.00 | QLD:1.3813 |  |
| dca_qqq | $135.50 | $135.00 | +0.37% | n/a | $0.00 | QQQ:0.1803 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
