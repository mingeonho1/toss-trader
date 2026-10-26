# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-10-25T23:15:04.421810+00:00`
- Price source: `live Toss`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `299`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $704.95 |
| QLD | $86.43 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $119.38 | $135.00 | -11.57% | 2.000 | $0.00 | QLD:1.3813 |  |
| dca_qqq | $127.13 | $135.00 | -5.83% | n/a | $0.00 | QQQ:0.1803 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
