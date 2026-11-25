# 2026-11-25 장부 재생(R2 진단) — 실행 보류

- 스크립트: `experiments/2026-11-25_ledger_replay.py` (준비 완료). 2026-09-28부터 현재 캐시
  `/Users/mingh/github/toss-trader/data/_hist_cache`(읽기 전용)로 전 전략을 수정 후 코드로 메모리에서 재생하고,
  실제 장부 `/Users/mingh/github/toss-trader/data/paperlab/*/state.json`과 단위장부 누적수익을 공통 마지막 날짜 기준으로 비교한다.
  장부 덮어쓰기는 없다(save_state 호출 없음).
- **상태: 미실행.** executor 세션에서 이 레포 밖 데이터 디렉토리를 읽는 스크립트 실행이 권한 시스템에 거부됐다.
  표는 비어 있고, 아래 명령을 한 번 실행하면 이 파일이 결과 표로 덮어써진다.

```
PYTHONPATH=src .venv/bin/python experiments/2026-11-25_ledger_replay.py
```

- 한계(실행 후 해석 시): 유니버스는 현재 캐시 기준(장부 당시와 다를 수 있음), 조정종가 재계산 차이 가능,
  수정 전 코드 재생은 생략. 재생 마지막일이 장부보다 이르면 늦은 봉 대기(수정 3) 때문이다.
