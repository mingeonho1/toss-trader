"""스테일 가드 + 토스 1차 일봉 소스 회귀 테스트.

2026-09-30 사고: Nasdaq /historical 일봉이 최신 완료 세션(09-29)을 며칠 늦게 실어 캐시가
09-28 에 정체 → 페이퍼 랩이 첫 세션에서 못 벗어남. 고친 내용을 오프라인(네트워크·자격증명 없이)
으로 검증한다: (1) 토스 일봉을 1차 소스로 병합(진행중 당일 캔들 제외·adjclose 규약),
(2) 최신 완료 세션 도출, (3) 스테일이면 단계 ❌·요청 방출·배너.

실행: PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_scoreboard_staleness.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import daily_scoreboard as sb  # noqa: E402
from toss_trader import histdata  # noqa: E402

KST = timezone(timedelta(hours=9))


def _kst_utc(y: int, mo: int, d: int, h: int = 14) -> datetime:
    return datetime(y, mo, d, h, tzinfo=KST).astimezone(timezone.utc)


class _FakeToss:
    """get_candles 만 흉내(최신→과거 내림차순, 진행중 당일 저거래량 캔들 포함)."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def get_candles(self, symbol, interval="1d", count=100, adjusted=True):  # noqa: D401
        return {"candles": self._rows[:count], "nextBefore": None}


_TOSS_ROWS = [
    {"timestamp": "2026-09-30T13:00:00.000+09:00", "openPrice": "740.34", "highPrice": "740.91",
     "lowPrice": "739.36", "closePrice": "739.42", "volume": "25541"},          # 진행중(미완료)
    {"timestamp": "2026-09-29T13:00:00.000+09:00", "openPrice": "740.145", "highPrice": "740.58",
     "lowPrice": "735.34", "closePrice": "737.93", "volume": "27052269"},        # 완료 09-29
    {"timestamp": "2026-09-28T13:00:00.000+09:00", "openPrice": "740.455", "highPrice": "741.42",
     "lowPrice": "731.63", "closePrice": "736.53", "volume": "41775024"},        # 완료 09-28
]


# ── 토스 1차 일봉 소스 ────────────────────────────────────────────────────────
def test_fetch_toss_recent_excludes_in_progress_and_sets_adjclose():
    rows = histdata.fetch_toss_recent("QQQ", days=14, client=_FakeToss(_TOSS_ROWS),
                                      cutoff=date(2026, 9, 29))
    assert [r["d"] for r in rows] == ["2026-09-28", "2026-09-29"]   # 09-30(진행중) 제외·오름차순
    last = rows[-1]
    assert last["c"] == last["a"] == 737.93                          # adjusted → a=c 규약
    assert last["v"] == 27052269.0
    assert last["src"] == "toss"                                     # 소스 기록


def test_fetch_toss_recent_no_cutoff_keeps_all():
    rows = histdata.fetch_toss_recent("QQQ", days=14, client=_FakeToss(_TOSS_ROWS), cutoff=None)
    assert [r["d"] for r in rows][-1] == "2026-09-30"                # cutoff 없으면 전부


def test_fetch_nasdaq_recent_tags_source(monkeypatch):
    # _parse_nasdaq_historical 를 우회해 소스 태깅만 검증.
    monkeypatch.setattr(histdata, "_http_get", lambda *a, **k: b"{}")
    monkeypatch.setattr(histdata, "_parse_nasdaq_historical",
                        lambda data: [{"d": "2026-09-25", "o": 1, "h": 1, "l": 1, "c": 5.0, "v": 1}])
    rows = histdata.fetch_nasdaq_recent("QQQ", days=5, assetclass="etf")
    assert rows[0]["a"] == rows[0]["c"] == 5.0 and rows[0]["src"] == "nasdaq"


# ── 페처 팩토리(라이브=토스 1차 + Nasdaq 폴백) ────────────────────────────────
def test_make_fetcher_prefers_toss_when_client(monkeypatch):
    monkeypatch.setattr(histdata, "fetch_nasdaq_recent",
                        lambda *a, **k: pytest.fail("Nasdaq 호출되면 안 됨(토스 성공)"))
    fetch = sb._make_recent_fetcher(_FakeToss(_TOSS_ROWS), date(2026, 9, 29))
    rows = fetch("QQQ", 14)
    assert rows and rows[-1]["src"] == "toss"


def test_make_fetcher_falls_back_to_nasdaq(monkeypatch):
    called = {}

    def fake_nasdaq(s, days, assetclass="etf"):
        called["hit"] = (s, days)
        return [{"d": "2026-09-25", "o": 1, "h": 1, "l": 1, "c": 5.0, "v": 1, "a": 5.0, "src": "nasdaq"}]

    monkeypatch.setattr(histdata, "fetch_nasdaq_recent", fake_nasdaq)
    fetch = sb._make_recent_fetcher(None, None)                      # client 없음 → Nasdaq
    rows = fetch("QQQ", 14)
    assert called["hit"] == ("QQQ", 14) and rows[0]["src"] == "nasdaq"


def test_make_fetcher_nasdaq_when_toss_empty(monkeypatch):
    monkeypatch.setattr(histdata, "fetch_nasdaq_recent",
                        lambda *a, **k: [{"d": "2026-09-25", "o": 1, "h": 1, "l": 1,
                                          "c": 5.0, "v": 1, "a": 5.0, "src": "nasdaq"}])
    fetch = sb._make_recent_fetcher(_FakeToss([]), date(2026, 9, 29))  # 토스 빈 응답
    assert fetch("QQQ", 14)[0]["src"] == "nasdaq"


# ── 최신 완료 세션 도출 ───────────────────────────────────────────────────────
def test_computed_latest_session_weekday_after_close():
    # 2026-09-30 14:00 KST → ET 09-30 01:00(마감 전) → 최신 완료 = 09-29(화).
    assert sb._computed_latest_session(_kst_utc(2026, 9, 30)) == date(2026, 9, 29)


def test_computed_latest_session_walks_over_weekend_and_holiday():
    # 월요일 이른 아침 → 직전 완료는 금요일.
    assert sb._computed_latest_session(_kst_utc(2026, 9, 28)) == date(2026, 9, 25)
    # 신정(01-01 목) 다음날 → 최신 완료는 12-31(수).
    assert sb._computed_latest_session(_kst_utc(2026, 1, 2)) == date(2025, 12, 31)


def test_latest_completed_no_client_uses_computed():
    assert sb.latest_completed_us_session(_kst_utc(2026, 9, 30), client=None) == date(2026, 9, 29)


class _FakeCal:
    def __init__(self, payload):
        self._p = payload

    def get_market_calendar(self, market="US", date=None):
        return self._p


def test_latest_completed_uses_toss_calendar_when_parseable():
    payload = {"prev": {"date": "2026-09-29",
                        "regularMarket": {"endTime": "2026-09-29T16:00:00-04:00"}},
               "today": {"date": "2026-09-30", "regularMarket": None}}       # 오늘 휴장 가정
    got = sb.latest_completed_us_session(_kst_utc(2026, 9, 30), client=_FakeCal(payload))
    assert got == date(2026, 9, 29)


def test_latest_completed_falls_back_on_garbage_calendar():
    got = sb.latest_completed_us_session(_kst_utc(2026, 9, 30), client=_FakeCal({"junk": 1}))
    assert got == date(2026, 9, 29)                                          # 계산 캘린더 폴백


# ── paperlab_last_date ────────────────────────────────────────────────────────
def test_paperlab_last_date_max_over_states(tmp_path):
    for name, ld in [("a", "2026-09-28"), ("b", "2026-09-29"), ("_backtest", "2030-01-01")]:
        d = tmp_path / name
        d.mkdir()
        (d / "state.json").write_text(json.dumps({"last_date": ld}))
    assert sb.paperlab_last_date(tmp_path) == date(2026, 9, 29)              # _backtest 제외


# ── 요청/배너 헬퍼(멱등) ──────────────────────────────────────────────────────
def test_stale_request_upsert_idempotent(tmp_path):
    req = tmp_path / "requests.jsonl"
    req.write_text(json.dumps({"type": "audit", "session_date": "2026-09-28"}) + "\n")
    sb._append_stale_request(date(2026, 9, 29), date(2026, 9, 28), path=req)
    sb._append_stale_request(date(2026, 9, 29), date(2026, 9, 28), path=req)
    rows = [json.loads(x) for x in req.read_text().splitlines() if x.strip()]
    stale = [r for r in rows if r.get("type") == "ops_stale_paper"]
    assert len(stale) == 1 and stale[0]["priority"] == "high"
    assert any(r.get("type") == "audit" for r in rows)                       # 기존 요청 보존
    sb._clear_stale_request(req)
    rows2 = [json.loads(x) for x in req.read_text().splitlines() if x.strip()]
    assert not any(r.get("type") == "ops_stale_paper" for r in rows2)


def test_stale_banner_idempotent_and_clears(tmp_path):
    dec = tmp_path / "decision_latest.md"
    dec.write_text("# 오늘의 판단\n\n- 본문\n")
    sb._write_stale_banner(date(2026, 9, 29), date(2026, 9, 28), path=dec)
    sb._write_stale_banner(date(2026, 9, 29), date(2026, 9, 28), path=dec)
    text = dec.read_text()
    assert text.count(sb._STALE_BANNER_START) == 1                          # 중복 삽입 없음
    assert text.startswith("# 오늘의 판단")                                  # 제목 보존
    sb._clear_stale_banner(dec)
    assert sb._STALE_BANNER_START not in dec.read_text()


# ── 가드 end-to-end(run_scoreboard) ──────────────────────────────────────────
def _run_guard(tmp_path, monkeypatch, last_date: str, *, preseed_req=None, preseed_banner=False):
    pl = tmp_path / "paperlab" / "s1"
    pl.mkdir(parents=True)
    (pl / "state.json").write_text(json.dumps({"last_date": last_date}))
    req = tmp_path / "requests.jsonl"
    if preseed_req is not None:
        req.write_text("".join(json.dumps(r) + "\n" for r in preseed_req))
    dec = tmp_path / "decision_latest.md"
    dec.write_text("# 오늘의 판단\n\n- 본문\n")
    if preseed_banner:
        sb._write_stale_banner(date(2026, 9, 29), date(2026, 9, 28), path=dec)
    monkeypatch.setattr(sb, "PAPERLAB_STATE_DIR", tmp_path / "paperlab")
    monkeypatch.setattr(sb, "LOOP_REQUESTS", req)
    monkeypatch.setattr(sb, "DECISION_LATEST", dec)
    monkeypatch.setattr(sb, "seed_candle_cache", lambda *a, **k: [])
    steps = [sb.Step("paperlab", [sys.executable, "-c", "pass"], timeout=30.0)]
    res = sb.run_scoreboard(offline=False, creds=False, now_utc=_kst_utc(2026, 9, 30),
                            steps=steps, report_path=tmp_path / "sb.md",
                            history_path=tmp_path / "hist.jsonl", fp_state=tmp_path / "fp.json",
                            lc_state=tmp_path / "lc.json", trades_path=tmp_path / "tr.jsonl")
    return res, req, dec


def test_guard_marks_stale_emits_request_and_banner(tmp_path, monkeypatch):
    res, req, dec = _run_guard(tmp_path, monkeypatch, "2026-09-28")
    assert "❌" in res["report_text"] and "stale" in res["report_text"]      # 단계 ❌ stale
    rows = [json.loads(x) for x in req.read_text().splitlines() if x.strip()]
    stale = [r for r in rows if r.get("type") == "ops_stale_paper"]
    assert len(stale) == 1 and stale[0]["priority"] == "high"
    assert stale[0]["expected_session"] == "2026-09-29"
    assert sb._STALE_BANNER_START in dec.read_text()


def test_guard_passes_when_fresh_and_self_heals(tmp_path, monkeypatch):
    res, req, dec = _run_guard(
        tmp_path, monkeypatch, "2026-09-29",
        preseed_req=[{"type": "ops_stale_paper", "session_date": "2026-09-28"},
                     {"type": "audit", "session_date": "2026-09-28"}],
        preseed_banner=True)
    rows = [json.loads(x) for x in req.read_text().splitlines() if x.strip()]
    assert not any(r.get("type") == "ops_stale_paper" for r in rows)         # 요청 정리
    assert any(r.get("type") == "audit" for r in rows)                       # 무관 요청 보존
    assert sb._STALE_BANNER_START not in dec.read_text()                     # 배너 자가치유
    assert "stale" not in res["report_text"]
