#!/usr/bin/env python3
"""c16a — GEM 듀얼 모멘텀(Antonacci 2014): SPY / ACWX / AGG, 월 1회. Cycle 16, 레인 1.

사전등록: reports/cycle16_c16a_gem.md 상단(결과 보기 전). 카드: docs/pipeline/candidates/2026-11-25_gem-dual-momentum.md
규칙(동결): 월말 종가 t 에서 12개월(252거래일) 총수익률. R(SPY) > R(BIL) 이면 위험자산(SPY vs ACWX 중 큰 쪽 100%),
아니면 AGG 100%. 체결 t+1 종가, 목표 불변이면 주문 없음. 이웃: lookback ×{0.5,0.7,0.8,1.2,1.5}.
비용(주): fees.TossFeeSchedule(0.1%=10bp, ≤$10 매수 무료) + 반호가 1bp + 슬리피지 5bp. 25bp 는 스트레스 전용.
데이터 프록시(사전등록): SPY→VONE, AGG→BND, BIL→FRED DTB3 합성 (Nasdaq 은 NYSE 상장 ETF 배당 미제공).
원장: gate_eval(run_splits/log_evaluation) → reports/trials_ledger.jsonl, 홀드아웃 peek-once.

재현: PYTHONPATH=src python3 experiments/c16a_gem.py [--no-ledger] [--no-paperlab]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments"))

import c16_alloc_engine as E  # noqa: E402

IDEA_ID = "c16a_gem"
REPORT = ROOT / "reports" / "cycle16_c16a_gem.md"
RESULTS_JSON = ROOT / "reports" / "c16a_gem_results.json"
NAMES = ["SPY", "ACWX", "AGG", "BIL"]
BASE_PARAMS = {"lookback": 252}


def sig_gem(panel, dates, lookback: int = 252):
    """GEM 목표비중(dates 정렬). 월말에만 갱신, 그 외엔 직전 목표 유지. 워밍업 전엔 {}(현금)."""
    spy, acwx, bil = panel["SPY"], panel["ACWX"], panel["BIL"]

    def decide_at(t):
        if t < lookback:
            return None
        r = lambda x: x[t] / x[t - lookback] - 1.0  # noqa: E731
        if r(spy) > r(bil):
            return {"SPY": 1.0} if r(spy) >= r(acwx) else {"ACWX": 1.0}
        return {"AGG": 1.0}

    return E.carry_month_end(dates, decide_at)


def avg_weights(res) -> dict:
    """정적 대조용: 평가창 평균 보유비중(SPY/ACWX/AGG) — 같은 평균 노출, 타이밍 없음."""
    tot = {"SPY": 0.0, "ACWX": 0.0, "AGG": 0.0}
    for a in res.applied:
        for k in tot:
            tot[k] += a.get(k, 0.0)
    n = max(len(res.applied), 1)
    return {k: round(v / n, 4) for k, v in tot.items() if v > 0}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-paperlab", action="store_true")
    args = ap.parse_args(argv)
    out = E.run_card(IDEA_ID, sig_gem, BASE_PARAMS, "lookback", NAMES, control_weights=avg_weights,
                     do_log=not args.no_ledger, with_paperlab=not args.no_paperlab)
    res = out.pop("_res")
    held = {}
    for a in res.applied:
        k = next(iter(a), "cash")
        held[k] = held.get(k, 0) + 1
    out["holding_share"] = {k: v / len(res.applied) for k, v in held.items()}
    extra = [f"\n보유 비중(일수 기준): " + ", ".join(f"{k} {v:.0%}" for k, v in out["holding_share"].items())
             + f". 전환일: {', '.join(out['switch_dates'])}."]
    RESULTS_JSON.write_text(json.dumps(E.jsonable(out), ensure_ascii=False, indent=1), encoding="utf-8")
    E.write_report(REPORT, E.render_results(out, extra))
    print(f"{IDEA_ID}: verdict={out['verdict']} switches={out['switches']} "
          f"hold term={out['holdout']['metrics']['terminal_vs_b0']:.3f}")


if __name__ == "__main__":
    main()
