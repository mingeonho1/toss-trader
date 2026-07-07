# Microstructure Data Collector Plan

작성일: 2026-07-07 · 상태: 구현 완료

## 목적

토스 OpenAPI의 `orderbook`/`trades` 실시간 응답을 장중에 저장해, 훗날 단기 신호가 실제로 예측력을 갖는지 검증할 데이터셋을 만든다. 이 작업은 **매매 봇이 아니다**. 주문, 페이퍼 체결, 전략 편입은 하지 않는다.

## 원칙

- 순서: 데이터 수집 → 통계 검증 → 페이퍼 → 실거래 검토.
- 과거 호가/틱 데이터가 없으므로, 자체 JSONL 아카이브가 생기기 전에는 단기 전략을 평가하지 않는다.
- 왕복 비용 70bps를 넘는 신호가 관측되지 않으면 매매 전략으로 승격하지 않는다.
- 수집기는 실패도 기록한다. API 오류, 레이트리밋, 지연이 데이터 품질 그 자체다.
- 실주문 경로와 완전히 분리한다.

## 저장 포맷

경로:

```text
data/microstructure/YYYY-MM-DD/SYMBOL.jsonl
```

각 라인은 독립 JSON 객체다.

```json
{
  "schema": 1,
  "ts": "2026-07-07T14:00:00.000000+00:00",
  "session_date": "2026-07-07",
  "regular_market": true,
  "symbol": "QQQ",
  "ok": true,
  "latency_ms": 123.4,
  "latency": {
    "orderbook_ms": 60.1,
    "trades_ms": 58.9
  },
  "errors": {},
  "orderbook": { "...": "raw Toss response" },
  "trades": [ "... raw Toss response ..." ]
}
```

응답 스키마는 토스가 바꿀 수 있으므로 원문을 보존한다. 분석기는 가능한 경우에만 best bid/ask, mid, imbalance를 파싱한다.

## 실행

1회 수집:

```bash
PYTHONPATH=src python scripts/collect_microstructure.py --symbols QQQ,SPY --once
```

장중 반복 수집:

```bash
PYTHONPATH=src nohup caffeinate -dimsu python scripts/collect_microstructure.py \
  --symbols QQQ,SPY --interval-sec 30 --regular-only \
  > data/microstructure.nohup.out 2>&1 &
echo $! > data/microstructure.pid
```

중지:

```bash
kill "$(cat data/microstructure.pid)"
```

요약 분석:

```bash
PYTHONPATH=src python scripts/analyze_microstructure.py --symbol QQQ --date 2026-07-07
```

## 검증 기준

최소 몇 주~몇 달 데이터가 쌓인 뒤 다음을 본다.

- 수집 성공률, 오류율, p95 지연.
- best bid/ask 및 mid를 안정적으로 파싱할 수 있는 비율.
- order book imbalance와 1분/5분 후 mid 수익률의 상관.
- 위 상관이 비용 70bps를 고려해도 의미 있는지.

`scripts/analyze_microstructure.py`는 이 중 기초 통계만 제공한다. 결과가 약하면 전략으로 승격하지 않는다.
