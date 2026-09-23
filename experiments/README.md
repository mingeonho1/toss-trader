# experiments/ — 루프 엔지니어링 실험 규약

모든 전략 실험은 이 규약을 따른다. 상세 기준은 `docs/gate_v2_spec.md`.

## 도구
- 데이터: `toss_trader.histdata`
  - `load_panel(symbols)` — 캐시(`data/_hist_cache/`) 우선. 현재 캐시는 대부분 2016-09~2026-09(Nasdaq 폴백, 10년).
  - `load_fred("NASDAQ100")` 1986~, `NASDAQCOM` 1971~, `DTB3`(T-bill 수익률, 현금/조달금리), `VIXCLS` 1990~, `DGS10`.
    FRED는 종가 전용(배당 없음) → `index_total_return(c, div_yield_annual)`로 근사. NDX 배당수익률은 보수적으로 0.7%/yr.
  - `synthetic_leveraged(base, L, rf_candles=..., rf_kind="yield")` — 합성 레버리지(QQQ 3x vs TQQQ 상관 0.999).
  - Yahoo는 이 네트워크에서 429 스로틀이 잦다. 요청 간격 ≥1.5s, 429면 중단(우회 금지).
- 백테스트: `toss_trader.research` (`run_weights`, `run_trades`, `run_dca_overlay`, 지표 헬퍼, `lookahead_guard`).
- 판정: `toss_trader.gate` + `scripts/gate_eval.py` (원장 `reports/trials_ledger.jsonl`, 홀드아웃 peek-once).

## 규칙
1. **사전등록**: 실행 전 가설·규칙·파라미터를 리포트 상단에 적는다. 그리드 서치 금지 — 아이디어당 사전등록 설정 1개 + 평탄성 검사용 이웃(±20~50%)만.
2. **모든 시도는 원장에 기록**(이웃·실패 포함). 원장을 우회한 결과는 인정하지 않는다.
3. **분할**: 장기(FRED/합성) 설계구간 1986–2008, 홀드아웃 2009–2026. 10년 데이터만 있는 실험은 설계 2016-09–2021-12, 홀드아웃 2022–2026. 홀드아웃은 아이디어당 1회만.
4. **체결**: 신호는 종가 t, 체결은 t+1 종가(또는 t+1 시가). 모든 신호 함수는 `lookahead_guard` 테스트 필수.
5. **비용**: 기본 수수료 25bp/side(토스 표준, 0.1% 프로모 6/30 종료) + 티어별 반호가·슬리피지. 10bp(프로모 what-if)와 2x 스트레스도 보고.
   FX(~20bp)는 입금(환전) 시에만 — USD 상주 매매에는 미부과.
6. **벤치마크**: 단위자본 QQQ B&H(시간가중), DCA는 B0=QQQ60/SCHD25/GLD15, B1=QQQ 100% (XIRR·최종자산).
7. **레버리지**: 단위자본 MDD −50% 하드캡(닷컴·GFC 포함 구간에서).
8. 산출물: `experiments/<id>.py`(재현 스크립트), `reports/<cycle>_<id>.md`(사전등록 + 결과표 + 판정 PASS/CONDITIONAL/FAIL + 솔직한 해석).
   불합격 아이디어도 결과를 남긴다(파라미터 튜닝으로 구제 금지).
