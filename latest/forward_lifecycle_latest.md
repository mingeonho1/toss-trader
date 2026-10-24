# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-10-24T21:36:02.299312+00:00`
- Price source: `cached Nasdaq daily close`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `299`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $704.40 |
| QLD | $86.15 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $119.00 | $135.00 | -11.85% | 2.000 | $0.00 | QLD:1.3813 |  |
| dca_qqq | $127.03 | $135.00 | -5.90% | n/a | $0.00 | QQQ:0.1803 |  |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
