#!/usr/bin/env python3
"""R3 보조: tqqq_bh vs qqq_bh 라이브 일간 잔차 점검(장부 읽기 전용) — 데이터 정렬/정체 봉 여부."""
import json
import math
from pathlib import Path

D = Path("/Users/mingh/github/toss-trader/data/paperlab")
C = Path("/Users/mingh/github/toss-trader/data/_hist_cache")


def curve(nm):
    return [(r[0], float(r[1])) for r in json.loads((D / nm / "state.json").read_text())["equity"]]


q, t = dict(curve("qqq_bh")), dict(curve("tqqq_bh"))
ds = sorted(set(q) & set(t))
print("dates", ds[0], ds[-1], len(ds))
for i in range(1, len(ds)):
    rq = math.log(q[ds[i]] / q[ds[i - 1]])
    rt = math.log(t[ds[i]] / t[ds[i - 1]])
    flag = " <<" if abs(rt - 3 * rq) > 0.02 else ""
    print(f"{ds[i]} rq={rq:+.4f} rt={rt:+.4f} rt-3rq={rt - 3 * rq:+.4f}{flag}")


def closes(sym):
    raw = json.loads((C / f"{sym}.json").read_text())
    rows = raw.get("candles", raw) if isinstance(raw, dict) else raw
    return rows


r = closes("QQQ")
print(type(r), (list(r.keys())[:5] if isinstance(r, dict) else r[-3:]))
