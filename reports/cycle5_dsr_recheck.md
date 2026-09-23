# Cycle 5 · 프로그램 DSR 추정기 버그픽스 재검증 (DSR re-check)

작성일 2026-09-23 · 대상 `src/toss_trader/gate.py` · 사양 `docs/gate_v2_spec.md` **부록 v2.2** · 선행 `reports/cycle3_c3c_pit.md`(부록)·`reports/cycle4_c4b_fee_reeval.md`(§4) · 재현 엔진 `scripts/gate_eval.py::program_dsr_recheck` · 사이드카 `reports/trials_ledger_flags.json`

> **핵심 한 줄**: 사이클3·4가 지적한 "모든 전략의 프로그램 DSR ≈ 0.00"은 원장의 **퇴화(무낙폭 현금성) 시도 3행**(`c2b_vix_1x/2x/3x`, `mult=1.95`, 일 SR=2.28·연율 36)이 시도 SR 분산 V를 ~50배 부풀린 아티팩트였다(단위오류 아님을 확인). 시도풀 정제 + 로버스트 V(MAD 5σ 윈저) + 아이디어 레벨 N_eff로 `SR*₀`를 **일 0.43 → 0.06**으로 내렸다. 정정 후 프로그램 DSR은 0.00이라는 비판별 값에서 **판별 가능한 0.4–0.8**로 되살아나지만, **홀드아웃(게이트 판정 축)에서 DSR≥0.90에 이르는 아이디어는 0개**(최고 `c2d_resid_mom_top3` 0.81)라 **`gate.decide` 판정은 하나도 바뀌지 않는다(전부 FAIL 유지)** — 정직한 결과다. **이것은 추정기 정정이지 임계 완화가 아니며, 채택 결론을 조금도 느슨하게 하지 않는다.**

---

## 1. 진단 (정밀)

원장 `reports/trials_ledger.jsonl`: **503행 / 73 아이디어 / 8 사이클(c2a–c4b)**. 분할 design 430·holdout 73. 레인 1:406·2:91·3:6.

### 1.1 |sr_daily| 상위 10 (idea_id / 레인 / 분할)

| # | idea_id | 레인 | 분할 | sr_daily | sr_annual | T | MDD | Ulcer | 성격 |
|---|---|---:|---|---:|---:|---:|---:|---:|---|
| 1 | `c2b_vix_1x` | 1 | design | **+2.2767** | +36.14 | 5803 | **0.000** | **0.00** | **퇴화·무낙폭 현금성** |
| 2 | `c2b_vix_2x` | 1 | design | **+2.2767** | +36.14 | 5803 | **0.000** | **0.00** | 동일(exec 변형) |
| 3 | `c2b_vix_3x` | 1 | design | **+2.2767** | +36.14 | 5803 | **0.000** | **0.00** | 동일(exec 변형) |
| 4 | `c2b_gapdown_QQQ` | 3 | holdout | −0.3923 | −6.23 | 105 | −0.670 | 41.9 | 레인3 거래단위 |
| 5 | `c2b_vix_1x` | 1 | design | +0.3483 | +5.53 | 5803 | −0.030 | 0.17 | 근사-현금(mult 1.56) |
| 6 | `c2b_gapdown_QQQ` | 3 | design | −0.2800 | −4.44 | 75 | −0.386 | 21.0 | 레인3 거래단위 |
| 7 | `c2b_vix_2x` | 1 | design | +0.1752 | +2.78 | 5803 | −0.058 | 0.41 | 근사-현금 |
| 8 | `c2b_gapdown_QLD` | 3 | holdout | −0.1609 | −2.55 | 105 | −0.702 | 47.9 | 레인3 거래단위 |
| 9 | `c2d_attention_h5` | 2 | holdout | −0.1181 | n/a | 273 | n/a | n/a | 단기·모멘트 결측 |
| 10 | `c2b_vix_3x` | 1 | design | +0.1178 | +1.87 | 5803 | −0.086 | 0.74 | 근사-현금 |

(참고: 11위 `c2b_vix_1x` +0.1067, 12위 `c2d_resid_mom_top3` +0.1065 — 여기부터 실전략 영역.)

### 1.2 가설별 판정

- **단위오류(연율 SR을 일 SR로 오기)? → 아니오.** 두 값이 모두 저장된 499행 전부에서 `sr_annual == sr_daily·√252`가 정확히 성립한다(편차 0). 상위 3행의 2.2767은 진짜 **일** SR이고, 연율값을 잘못 넣은 게 아니다.
- **퇴화 near-zero-vol(현금 슬리브의 미소 변동성 → 거대 SR)? → 예, 이게 단일 원인.** `c2b_vix_1x/2x/3x @ mult=1.95` 3행이 **MDD=0·Ulcer=0**로 자본곡선이 단조증가(상시 현금 + 희소 VIX 트리거) → 실현변동성≈0 → |SR| 폭증. 1x/2x/3x는 같은 all-cash 계열로 collapse해 값이 동일. 이 3행만으로 전 원장 V가 **0.00063 → 0.0317**(~50배)로 부풀었다.
- **벤치마크/진단 행? → 없음.** `..._QQQ`류 idea_id는 티커명일 뿐 실전략이며, 별도 벤치·진단 행은 원장에 없다.
- **거래단위(per-trade) 행? → 예(레인3 6행).** `c2b_gapdown_*`의 T=75/105는 거래수이며 sr_daily가 거래단위 척도라 **일 SR과 단위가 불일치**(사양 §3.5, 연단위 SR 부적용) → 시도풀 제외.
- **홀드아웃 행을 DSR 시도집합에 넣어야 하나? → 아니오.** Bailey–López de Prado에서 DSR의 시도집합 = **같은 데이터에 시도한 구성(설계분할)**. 홀드아웃(73행)은 동결 후 1회 사후평가이지 탐색 시도가 아니므로 기본 제외.
- **최소 관측/최소 변동성.** 레인2 `c2d_attention`(T=273/578, 모멘트 결측)·퇴화 저변동성 행은 SR 분산 추정을 왜곡 → `min_obs=252` + 무낙폭 제외로 처리.

**V에 대한 영향**: 퇴화 3행이 `SR*₀ = sqrt(V)·[(1−γ)Φ⁻¹(1−1/N)+γΦ⁻¹(1−1/(N·e))]`의 V를 지배해 문턱을 **일 0.43(연율 ~6.8)**로 올렸고, 실전략(일 SR 0.05–0.09)이 이를 못 넘어 DSR이 0.00으로 붕괴했다.

---

## 2. 정정 (요약)

`gate.py`에 하위호환 선택적 인자로 추가(기본 동작 보존, `dsr_program(robust=True)`/`gate_eval.run_splits(program_dsr_robust=True)`로 opt-in). 상세·공식은 사양 **부록 v2.2**.

1. **`filter_trial_records`** — 설계분할만·레인3 제외·`min_obs=252`·퇴화(`|MDD|<1e-9` 또는 `MDD=Ulcer=0`) 제외·사이드카 플래그(`degenerate`·`trade_lane`·`benchmark`·`diagnostic`·`cash_like`) 제외.
2. **`robust_sr_variance`** — `σ_MAD=1.4826·median(|SRᵢ−median|)`로 `median±5σ` 윈저화 후 표본분산. `V_raw`·`n_winsorized` 병기.
3. **`idea_cluster_n_eff`** — `idea_id`당 config SR 평균 대표 1개 → `N_eff = 대표 수`(그리드 이웃 흡수).

원장은 **되쓰지 않았다**. 퇴화·레인3 행은 사이드카 `reports/trials_ledger_flags.json`(6 config_hash)에만 표기했다. 구조적 필터가 이미 이들을 제거하므로 사이드카는 이중 방어·감사 흔적이다.

---

## 3. 원장 전체 재계산 (구 vs 신 추정기)

| 추정기 | 시도풀 | N_eff | V | SR*₀(일) | 비고 |
|---|---:|---:|---:|---:|---|
| **구(버그)** | 503행 전부 | 73 | 0.03172 | **0.4306** | idea_id 폴백, 퇴화행 포함 |
| **신(v2.2)** | **424** 설계시도 | **70** | 0.000631 | **0.0603** | 퇴화·레인3·단기 제외, 11점 윈저, median 0.0280·σ_MAD 0.0137 |

### 3.1 아이디어별 DSR (홀드아웃 = 게이트 판정 축) — old → new

후보 SR_hat은 각 아이디어의 홀드아웃 행(레인3·퇴화 행은 후보에서 제외; 그래서 레인3 3아이디어는 일-DSR 산정 불가로 표에서 빠져 70행). 판정변화 = DSR 결정구간(≥0.95 PASS·0.90–0.95 경계·else FAIL)이 바뀌는가.

| idea_id | 축 | sr_hat(일) | T | DSR(old) | DSR(new) | 판정변화 |
|---|---|---:|---:|---:|---:|---|
| c2d_resid_mom_top3 | H | +0.0855 | 1184 | 0.0000 | **0.8080** | 아니오 |
| c2d_mom_top5 | H | +0.0794 | 1184 | 0.0000 | 0.7433 | 아니오 |
| c2d_mom_top3 | H | +0.0735 | 1184 | 0.0000 | 0.6761 | 아니오 |
| c2d_mom_top3_trend | H | +0.0666 | 1184 | 0.0000 | 0.5854 | 아니오 |
| c3c_pit_residmom5 | H | +0.0643 | 1184 | 0.0000 | 0.5540 | 아니오 |
| c3c_pit_mom5 | H | +0.0638 | 1184 | 0.0000 | 0.5474 | 아니오 |
| c3b_core_boost | H | +0.0618 | 4458 | 0.0000 | 0.5380 | 아니오 |
| c2a_const15x | H | +0.0598 | 4458 | 0.0000 | 0.4863 | 아니오 |
| c4b_c2a_voltarget25_fee10 | H | +0.0590 | 4458 | 0.0000 | 0.4653 | 아니오 |
| c4b_c2a_voltarget25_micro | H | +0.0590 | 4458 | 0.0000 | 0.4653 | 아니오 |
| c2a_const2x | H | +0.0589 | 4458 | 0.0000 | 0.4608 | 아니오 |
| c3a_vt_cashflow | H | +0.0570 | 4458 | 0.0000 | 0.4131 | 아니오 |
| c3b_core_sat | H | +0.0565 | 4458 | 0.0000 | 0.3999 | 아니오 |
| c4b_c3b_core_boost_fee10 | H | +0.0561 | 4458 | 0.0000 | 0.3885 | 아니오 |
| c4b_c3b_core_boost_micro | H | +0.0561 | 4458 | 0.0000 | 0.3885 | 아니오 |
| c3a_vt_volofvol | H | +0.0548 | 4458 | 0.0000 | 0.3569 | 아니오 |
| c3a_vt_ewma_3x | H | +0.0545 | 4458 | 0.0000 | 0.3493 | 아니오 |
| c4b_c3a_vt_ewma_fee10 | H | +0.0543 | 4458 | 0.0000 | 0.3466 | 아니오 |
| c4b_c3a_vt_ewma_micro | H | +0.0543 | 4458 | 0.0000 | 0.3466 | 아니오 |
| c3a_vt_ewma | H | +0.0542 | 4458 | 0.0000 | 0.3443 | 아니오 |
| c2a_voltarget | H | +0.0540 | 4458 | 0.0000 | 0.3379 | 아니오 |
| c2a_voltarget · … · c2c_preholiday (나머지 49 아이디어) | H | ≤ +0.047 | — | 0.0000 | **< 0.32** | 아니오 |

전 70 아이디어 완전표·수치는 재현 엔진 출력(아래 §5)으로 재생성된다. 상위 21행 외 나머지 49행은 전부 DSR(new) < 0.32이며 판정변화 없음.

### 3.2 판정 변화 여부 (`gate.decide`) — 정직한 보고

- **홀드아웃 축: 판정 변화 0건.** 신 추정기에서도 **어떤 아이디어도 DSR ≥ 0.90에 도달하지 못한다**(최고 0.808). 유의성 축은 `DSR_pass(≥0.95) AND RC_pass`이고 경계는 `0.90–0.95`인데, 신 DSR이 그 아래이므로 유의성 축의 DSR 기여는 구(0.00)와 **완전히 동일**(둘 다 False). 따라서 `gate.decide` 종합 판정은 사이클3·4의 보고와 **한 건도 다르지 않다(전부 FAIL 유지).** 구 DSR이 0.00이라 통과가 없었으므로, 픽스는 판정을 **악화시킬 수도 없다**(잃을 PASS가 없음).
- **설계(튜닝) 축의 정직한 각주.** 설계 축에서는 DSR(new)이 4개 아이디어에서 0.95를 넘긴다: `c2b_vix_1x` 0.999·`c2b_vix_2x` 0.994·`c2b_vix_3x` 0.977·`c2d_resid_mom_top3` 0.953(구 DSR은 모두 ~0.00). 그러나 (a) **설계는 파라미터 확정용 튜닝 축이지 게이트 판정 축이 아니다**(판정은 홀드아웃), (b) vix 3개는 mult=1.56의 **근사-현금** config로 홀드아웃 SR이 ~0.003–0.006(홀드 DSR≈0)이라 실엣지가 아니며, (c) `c2d_resid_mom_top3`는 설계구간에서 QQQ에 열위·평탄성 이웃 다수 열위로 경제·평탄성 축이 독립적으로 FAIL(사이클3 §4·§6에 기록). 따라서 설계 축을 게이트에 넣어도 종합 판정은 여전히 FAIL이다.

**결론**: 픽스는 0.00이라는 **비판별 오염값을 판별 가능한 값으로 되살릴 뿐**, 채택 결론(이번까지 채택 0)을 조금도 완화하지 않는다.

---

## 4. 산출물 · 불변식

- `gate.py`: `filter_trial_records`·`robust_sr_variance`·`idea_cluster_n_eff`·`ledger_trial_records`·`load_ledger_flags` 추가, `dsr_program(robust=…)` 확장. **기본 동작·기존 판정 문턱 불변**(기존 45 테스트 그대로 통과, 신규 7 테스트 추가).
- `scripts/gate_eval.py`: `run_splits(program_dsr_robust=…, trial_filter=…)` + `program_dsr_recheck` 재현 엔진.
- `docs/gate_v2_spec.md`: **부록 v2.2** 추가(append-only).
- `reports/trials_ledger_flags.json`: 사이드카(퇴화 3 + 레인3 3 = 6 config_hash). **원장 이력 미변경.**

## 5. 재현

```
PYTHONPATH=src .venv/bin/python -m pytest -q                     # 전체 378 pass
PYTHONPATH=src .venv/bin/python - <<'PY'
import sys; sys.path.insert(0,'scripts')
import gate_eval as ge, json
res = ge.program_dsr_recheck('reports/trials_ledger.jsonl')      # 홀드아웃 축
print('old SR*0=%.4f  new SR*0=%.4f  판정변화=%d' % (
      res['old']['star'], res['new']['star'], res['n_verdict_changes']))
for r in res['rows']:
    print('%-28s old=%.4f new=%.4f %s' % (
          r['idea_id'], r['dsr_old'], r['dsr_new'],
          'CHANGED' if r['changed'] else ''))
PY
```

`program_dsr_recheck(..., period='design')`로 설계 축 재계산, `trial_filter={...}`로 필터 인자 조정 가능.
