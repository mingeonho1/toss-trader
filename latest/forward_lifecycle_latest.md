# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-10-29T21:26:24.573758+00:00`
- Price source: `live Toss`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `299`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $704.82 |
| QLD | $86.19 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $119.05 | $135.00 | -11.81% | 2.000 | $0.00 | QLD:1.3813 |  |
| dca_qqq | $127.10 | $135.00 | -5.85% | n/a | $0.00 | QQQ:0.1803 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
