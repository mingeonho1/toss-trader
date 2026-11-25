#!/usr/bin/env python3
"""c16b — Faber GTAA 5자산 10개월 SMA(금 포함), 월 1회. Cycle 16, 레인 1.

사전등록: reports/cycle16_c16b_gtaa.md 상단(결과 보기 전). 카드: docs/pipeline/candidates/2026-11-25_faber-gtaa-gold.md
규칙(동결): 유니버스 SPY·EFA·IEF·GLD·VNQ 각 20%. 월말 종가 t 에서 TR > SMA_210(TR) 이면 보유, 아니면 그 20% 는 BIL.
체결 t+1 종가. 온/오프 집합이 직전 체결과 같으면 거래 없음, 바뀌면 목표비중으로 전체 리밸런스(버퍼 없음).
이웃: sma ×{0.5,0.7,0.8,1.2,1.5}.
비용(주): fees.TossFeeSchedule(0.1%=10bp, ≤$10 매수 무료) + 반호가 1bp + 슬리피지 5bp. 25bp 는 스트레스 전용.
데이터 프록시(사전등록): SPY→VONE, BIL→DTB3 합성, EFA/VNQ→가격+상수배당(2.9%/3.9%).
원장: gate_eval(run_splits/log_evaluation) → reports/trials_ledger.jsonl, 홀드아웃 peek-once.

재현: PYTHONPATH=src python3 experiments/c16b_gtaa.py [--no-ledger] [--no-paperlab]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "src"))

import c16_alloc_engine as E  # noqa: E402
from toss_trader import research as R  # noqa: E402

IDEA_ID = "c16b_gtaa"
REPORT = ROOT / "reports" / "cycle16_c16b_gtaa.md"
RESULTS_JSON = ROOT / "reports" / "c16b_gtaa_results.json"
ASSETS = ["SPY", "EFA", "IEF", "GLD", "VNQ"]
NAMES = ASSETS + ["BIL"]
BASE_PARAMS = {"sma_win": 210}
SLEEVE = 0.20


def sig_gtaa(panel, dates, sma_win: int = 210):
    """GTAA 목표비중(dates 정렬). 월말에만 갱신. SMA 워밍업 전엔 {}(현금)."""
    smas = {a: R.sma(panel[a], sma_win) for a in ASSETS}

    def decide_at(t):
        if any(smas[a][t] is None for a in ASSETS):
            return None
        on = [a for a in ASSETS if panel[a][t] > smas[a][t]]
        tgt = {a: SLEEVE for a in on}
        n_off = len(ASSETS) - len(on)
        if n_off:
            tgt["BIL"] = round(SLEEVE * n_off, 10)
        return tgt

    return E.carry_month_end(dates, decide_at)


EQUAL_WEIGHT = {a: SLEEVE for a in ASSETS}   # 정적 대조 = Faber 자체 벤치(상시 20%, 월간 리밸)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-paperlab", action="store_true")
    args = ap.parse_args(argv)
    out = E.run_card(IDEA_ID, sig_gtaa, BASE_PARAMS, "sma_win", NAMES, control_weights=EQUAL_WEIGHT,
                     do_log=not args.no_ledger, with_paperlab=not args.no_paperlab)
    res = out.pop("_res")
    n_on = [sum(1 for a in ASSETS if a in ap_) for ap_ in res.applied]
    out["avg_sleeves_on"] = sum(n_on) / max(len(n_on), 1)
    out["avg_bil_weight"] = sum(ap_.get("BIL", 0.0) for ap_ in res.applied) / max(len(res.applied), 1)
    extra = [f"\n평균 보유 슬리브 {out['avg_sleeves_on']:.2f}/5, 평균 BIL 비중 {out['avg_bil_weight']:.0%}. "
             f"리밸런스(집합 변경) {out['switches']}회."]
    RESULTS_JSON.write_text(json.dumps(E.jsonable(out), ensure_ascii=False, indent=1), encoding="utf-8")
    E.write_report(REPORT, E.render_results(out, extra))
    print(f"{IDEA_ID}: verdict={out['verdict']} switches={out['switches']} "
          f"hold term={out['holdout']['metrics']['terminal_vs_b0']:.3f}")


if __name__ == "__main__":
    main()
