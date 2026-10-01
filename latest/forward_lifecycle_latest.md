# Forward Lifecycle Paper (sleeve vs DCA-QQQ)

- Generated: `2026-10-01T00:52:22.580709+00:00`
- Price source: `live Toss`
- Real orders: none. Virtual fills using current prices + cost model.
- Plan start: `2026-09` · months remaining: `299`
- ⚠️ Lifecycle is beta, not alpha; leveraged; adoption undecided. See README.

## Prices

| Symbol | Price |
|---|---:|
| QQQ | $741.82 |
| QLD | $96.20 |

## Portfolios

| Portfolio | Equity | Contributed | Money Return | E target | Cash | Positions | Note |
|---|---:|---:|---:|---:|---:|---|---|
| lifecycle_sleeve | $132.88 | $135.00 | -1.57% | 2.000 | $0.00 | QLD:1.3813 | contribution $35.00 |
| dca_qqq | $133.78 | $135.00 | -0.91% | n/a | $0.00 | QQQ:0.1803 | contribution $35.00 |

## Notes

- `lifecycle_sleeve`: QQQ(1x)+QLD(2x) via policy_lifecycle glide target.
- `dca_qqq`: 100% QQQ dollar-cost averaging (benchmark).
- Dividends not modeled; synthetic-vs-real leverage tracking error not applied here.
