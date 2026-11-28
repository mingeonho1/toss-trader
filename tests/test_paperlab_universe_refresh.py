"""F15(결정 2026-11-28 D6) — 페이퍼 랩 유니버스 갱신 분리 · 스윙 ETF 제외.

(a) 유니버스 갱신 목록: hibeta → mom → swing 순, 중복 없음, 코어(_refresh_pairs) 40개와 비중복, 상한.
(b) --refresh-universe-only: 갱신만 하고 장부(state.json)·리더보드는 건드리지 않음. offline 이면 네트워크 없음.
(d) SWING_EXCLUDE(R4 ETF 누출 25종)가 스윙 유니버스에서 빠짐.
네트워크 없음(_polite_refresh·토스 클라이언트는 목으로 대체). 표준 라이브러리(unittest)만.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import paperlab_run as pr  # noqa: E402
from toss_trader.errors import RateLimitError, TossAPIError  # noqa: E402


class _TmpCache(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.cache = self.tmp / "hist"
        self.cache.mkdir()
        p = mock.patch.object(pr, "HIST_CACHE", self.cache)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def touch(self, *syms: str) -> None:
        for s in syms:
            (self.cache / f"{s}.json").write_text('{"rows": []}', encoding="utf-8")


class UniverseRefreshPairsTest(_TmpCache):
    def test_order_dedupe_and_core_exclusion(self) -> None:
        hib = [f"H{i:02d}" for i in range(50)]
        self.touch("QQQ", "TQQQ", "MSTR", "COIN", *hib)
        core = {s for s, _ in pr._refresh_pairs(hib)}
        self.assertEqual(len(core), pr.MAX_REFRESH)                # 코어 40 은 그대로(H00..H35)
        pairs = pr._universe_refresh_pairs(hib, ["AAPL", "H40", "MSFT"], ["MSFT", "ZZZ", "H01"])
        syms = [s for s, _ in pairs]
        self.assertEqual(syms, [f"H{i:02d}" for i in range(36, 50)] + ["AAPL", "MSFT", "ZZZ"])
        self.assertEqual(len(syms), len(set(syms)))                # 중복 없음
        self.assertFalse(core & set(syms))                         # 코어와 비중복
        self.assertEqual({a for _, a in pairs}, {"stocks"})

    def test_cap(self) -> None:
        with mock.patch.object(pr, "MAX_UNIVERSE_REFRESH", 5):
            pairs = pr._universe_refresh_pairs([], [f"M{i}" for i in range(10)], [])
        self.assertEqual([s for s, _ in pairs], [f"M{i}" for i in range(5)])
        self.assertEqual(pr.MAX_REFRESH, 40)                       # 코어 상한 불변


class RefreshUniverseOnlyTest(_TmpCache):
    def setUp(self) -> None:
        super().setUp()
        self.books = self.tmp / "paperlab"
        (self.books / "hot_rvol_swing").mkdir(parents=True)
        self.state = self.books / "hot_rvol_swing" / "state.json"
        self.state.write_text('{"last_date": "2026-11-27", "equity": [["2026-11-27", 1000.0, 36.0]]}',
                              encoding="utf-8")
        self.board = self.tmp / "paperlab_latest.md"
        for name, val in (("PAPERLAB_DIR", self.books), ("LEADERBOARD", self.board)):
            p = mock.patch.object(pr, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.touch("QQQ", "TQQQ", "AAPL", "ZZZZ")

    def _run(self, *argv: str) -> mock.MagicMock:
        before = self.state.read_bytes()
        with mock.patch.object(pr, "_polite_refresh", return_value=None) as refresh, \
                mock.patch.object(pr, "_live_client_cutoff", return_value=(None, None)), \
                mock.patch.object(pr, "run_forward", side_effect=AssertionError("ledger advanced")), \
                mock.patch.object(pr, "run_backfill", side_effect=AssertionError("backfill ran")):
            self.assertEqual(pr.main(["--refresh-universe-only", *argv]), 0)
        self.assertEqual(self.state.read_bytes(), before)          # 장부 불변
        self.assertFalse(self.board.exists())                      # 리더보드 미작성
        return refresh

    def test_refreshes_without_advancing_ledger(self) -> None:
        refresh = self._run()
        refresh.assert_called_once()
        syms = [s for s, _ in refresh.call_args.args[0]]
        self.assertIn("ZZZZ", syms)                                # 스윙 전용 종목
        self.assertNotIn("AAPL", syms)                             # hibeta 앞쪽 = 코어(paperlab 단계 몫)
        self.assertNotIn("QQQ", syms)

    def test_offline_makes_no_refresh(self) -> None:
        self._run("--offline").assert_not_called()


def _rows(*days: str) -> list[dict]:
    return [{"d": d, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0, "a": 1.0} for d in days]


class PoliteRefreshMitigationTest(_TmpCache):
    """F15 리뷰 완화: 토스 429/403 → Nasdaq 전용, 캐시 구멍 전체 재페치, 집계(빈 응답·미처리 꼬리)."""

    CUTOFF = date(2026, 11, 27)

    def _cache(self, sym: str, last: str) -> None:
        (self.cache / f"{sym}.json").write_text(json.dumps({"symbol": sym, "rows": _rows(last)}),
                                                encoding="utf-8")

    def _toss_error_switches_to_nasdaq(self, exc: Exception) -> None:
        for s in ("A", "B", "C"):
            self._cache(s, "2026-11-25")
        with mock.patch.object(pr.histdata, "fetch_toss_recent", side_effect=exc) as toss, \
                mock.patch.object(pr.histdata, "fetch_nasdaq_recent",
                                  return_value=_rows("2026-11-26")) as nas:
            stopped = pr._polite_refresh([("A", "stocks"), ("B", "stocks"), ("C", "stocks")],
                                         client=object(), cutoff=self.CUTOFF, sleep=lambda s: None)
        self.assertIsNone(stopped)
        self.assertEqual(toss.call_count, 1)                       # 첫 429/403 뒤 토스 호출 없음
        self.assertEqual(nas.call_count, 3)

    def test_toss_429_switches_to_nasdaq_only(self) -> None:
        self._toss_error_switches_to_nasdaq(RateLimitError(429, "too-many-requests", "x"))

    def test_toss_403_switches_to_nasdaq_only(self) -> None:
        self._toss_error_switches_to_nasdaq(TossAPIError(403, "access_denied", "x"))

    def test_other_toss_error_keeps_toss(self) -> None:
        for s in ("A", "B"):
            self._cache(s, "2026-11-25")
        with mock.patch.object(pr.histdata, "fetch_toss_recent",
                               side_effect=TossAPIError(500, "internal", "x")) as toss, \
                mock.patch.object(pr.histdata, "fetch_nasdaq_recent", return_value=_rows("2026-11-26")):
            pr._polite_refresh([("A", "stocks"), ("B", "stocks")], client=object(),
                               cutoff=self.CUTOFF, sleep=lambda s: None)
        self.assertEqual(toss.call_count, 2)

    def test_gap_fill_uses_full_fetch_for_old_cache(self) -> None:
        self._cache("OLD", "2026-09-22")                           # 창(14일) 밖 → 증분이면 영구 구멍
        self._cache("NEW", "2026-11-25")
        st: dict[str, int] = {}
        with mock.patch.object(pr, "_fetch_full", return_value=[object()]) as full, \
                mock.patch.object(pr.histdata, "fetch_nasdaq_recent",
                                  return_value=_rows("2026-11-26")) as nas:
            pr._polite_refresh([("OLD", "stocks"), ("NEW", "stocks")], cutoff=self.CUTOFF,
                               sleep=lambda s: None, gap_fill=True, stats=st)
        full.assert_called_once_with("OLD", force=True)
        self.assertEqual([c.args[0] for c in nas.call_args_list], ["NEW"])
        self.assertEqual((st["full"], st["full_failed"]), (1, 0))

    def test_gap_fill_failure_skips_incremental(self) -> None:
        # 2차 감사 B: 실패 시 증분으로 메우면 영구 중간 구멍 → 증분 금지, 캐시 그대로(다음 실행 재시도).
        self._cache("OLD", "2026-09-22")
        before = (self.cache / "OLD.json").read_bytes()
        st: dict[str, int] = {}
        with mock.patch.object(pr, "_fetch_full", return_value=None), \
                mock.patch.object(pr.histdata, "fetch_nasdaq_recent",
                                  return_value=_rows("2026-11-26")) as nas:
            pr._polite_refresh([("OLD", "stocks")], cutoff=self.CUTOFF, sleep=lambda s: None,
                               gap_fill=True, stats=st)
        nas.assert_not_called()
        self.assertEqual(st["full_failed"], 1)
        self.assertEqual((self.cache / "OLD.json").read_bytes(), before)

    def test_no_gap_fill_by_default(self) -> None:
        self._cache("OLD", "2026-09-22")
        with mock.patch.object(pr, "_fetch_full") as full, \
                mock.patch.object(pr.histdata, "fetch_nasdaq_recent", return_value=_rows("2026-11-26")):
            pr._polite_refresh([("OLD", "stocks")], cutoff=self.CUTOFF, sleep=lambda s: None)
        full.assert_not_called()                                   # 코어 경로(paperlab 단계)는 불변

    def test_stats_count_empty_and_stop_tail(self) -> None:
        for s in ("A", "B", "C", "D"):
            self._cache(s, "2026-11-25")
        st: dict[str, int] = {}
        side = [[], _rows("2026-11-26"), RuntimeError("429")]
        with mock.patch.object(pr.histdata, "fetch_nasdaq_recent", side_effect=side):
            stopped = pr._polite_refresh([(s, "stocks") for s in "ABCD"], cutoff=self.CUTOFF,
                                         sleep=lambda s: None, stats=st)
        self.assertTrue(stopped and stopped.startswith("C:"))
        self.assertEqual(st["empty"], 1)
        self.assertEqual(st["done"], 3)                            # C 에서 중단 → 미처리 꼬리 C·D


class SwingExcludeTest(_TmpCache):
    LEAK = ("BITX BOXX BSV CONL CURE DBB DRN FAS HIBL LABU MSTU PSQ SCZ SGOV SH SPXL SVIX "
            "TECL TECS TIP TNA UUP VIXM VIXY VWO").split()

    def test_r4_leak_list_excluded_from_swing_universe(self) -> None:
        self.assertEqual(len(self.LEAK), 25)
        self.assertTrue(set(self.LEAK) <= pr.SWING_EXCLUDE)
        self.touch("AAPL", "MSFT", "_meta", *self.LEAK)
        self.assertEqual(pr._cached_universe(), ["AAPL", "MSFT"])


if __name__ == "__main__":
    unittest.main()
