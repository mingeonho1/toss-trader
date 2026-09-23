"""intraday_sources 오프라인 단위테스트 — 네트워크 없이 fixture로 파싱/집계/병합 검증.

실행: PYTHONPATH=src python -m unittest tests.test_intraday_sources
"""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from toss_trader import histdata, intraday_sources as isrc
from toss_trader.intraday_sources import (
    IntradayBar, RateLimited, aggregate, fetch_nasdaq, fetch_yahoo, interval_minutes,
    merge_rows, merge_write, parse_movers, parse_nasdaq_chart, parse_yahoo_intraday,
    rows_to_bars,
)


def _utc(y, mo, d, h, mi) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def _nasdaq_millis(y, mo, d, h, mi) -> int:
    # Nasdaq chart 관례: ET 벽시계 시각을 UTC 필드로 인코딩(12:00 ET → 12:00Z).
    return _utc(y, mo, d, h, mi) * 1000


def _nasdaq_chart_fixture() -> dict:
    """2026-09-22(화): 장전 4:00, 정규장 9:30/9:31/9:32, 장후 19:59. 중복 9:31 하나 포함."""
    def pt(h, mi, price):
        return {"z": {"dateTime": f"{h}:{mi:02d} ET", "value": str(price)},
                "x": _nasdaq_millis(2026, 9, 22, h, mi), "y": price}
    return {"data": {"symbol": "AAPL", "chart": [
        pt(4, 0, 339.44),      # 장전
        pt(9, 30, 340.10),     # 정규장 시작
        pt(9, 31, 340.55),
        pt(9, 31, 340.55),     # 중복 ts → 하나만
        pt(9, 32, 339.90),
        pt(19, 59, 339.89),    # 장후
    ]}}


def _yahoo_intraday_fixture() -> dict:
    pre = _utc(2021, 6, 14, 11, 0)   # 07:00 ET (장전)
    reg = _utc(2021, 6, 14, 18, 0)   # 14:00 ET (정규장)
    return {"chart": {"error": None, "result": [{
        "meta": {"gmtoffset": -4 * 3600},
        "timestamp": [pre, reg],
        "indicators": {"quote": [{
            "open": [10.0, 11.0], "high": [10.5, 11.5],
            "low": [9.5, 10.5], "close": [10.2, 11.2], "volume": [500, 800],
        }]},
    }]}}


def _movers_fixture() -> dict:
    return {"data": {
        "STOCKS": {
            "MostAdvanced": {"table": {"rows": [
                {"symbol": "JAGX", "lastSalePrice": "$34.46", "change": "+1190.6%"},
                {"symbol": "PENY", "lastSalePrice": "$0.49", "change": "+120%"},   # 저가
                {"symbol": "jagx", "lastSalePrice": "$34.46", "change": "+1190.6%"},  # 중복(대소문자)
            ]}},
            "MostActiveByShareVolume": {"table": {"rows": [
                {"symbol": "NVDA", "lastSalePrice": "$228.87", "change": "5,000,000"},
                {"symbol": "TSLA", "lastSalePrice": "440.10", "change": "3,000,000"},
            ]}},
        },
        "ETF": {"MostAdvanced": {"table": {"rows": [
            {"symbol": "TQQQ", "lastSalePrice": "$92.00", "change": "+6%"},
        ]}}},
    }}


class NasdaqChartParseTest(unittest.TestCase):
    def test_session_flags_and_price_only_bars(self) -> None:
        rows = parse_nasdaq_chart(_nasdaq_chart_fixture())
        self.assertEqual(len(rows), 5)                    # 중복 9:31 제거
        self.assertFalse(rows[0]["regular"])              # 4:00 ET 장전
        self.assertTrue(rows[1]["regular"])               # 9:30 ET 정규장
        self.assertTrue(rows[3]["regular"])               # 9:32 ET 정규장
        self.assertFalse(rows[-1]["regular"])             # 19:59 ET 장후
        # price-only → o=h=l=c, 거래량 없음(0), src 태그
        r = rows[1]
        self.assertEqual((r["o"], r["h"], r["l"], r["c"]), (340.10, 340.10, 340.10, 340.10))
        self.assertEqual(r["v"], 0.0)
        self.assertEqual(r["src"], "nasdaq")

    def test_ts_monotonic_and_et_roundtrip(self) -> None:
        rows = parse_nasdaq_chart(_nasdaq_chart_fixture())
        ts = [r["ts"] for r in rows]
        self.assertEqual(ts, sorted(ts))
        # et ISO는 그 ts로 되읽을 수 있어야(정규장 9:30 봉).
        dt = datetime.fromisoformat(rows[1]["et"])
        self.assertEqual(int(dt.timestamp()), rows[1]["ts"])
        self.assertEqual((dt.hour, dt.minute), (9, 30))   # ET 벽시계 9:30

    def test_empty_chart(self) -> None:
        self.assertEqual(parse_nasdaq_chart({"data": {"chart": []}}), [])
        self.assertEqual(parse_nasdaq_chart({}), [])


class YahooIntradayParseTest(unittest.TestCase):
    def test_src_tag_and_real_ohlcv(self) -> None:
        rows = parse_yahoo_intraday(_yahoo_intraday_fixture())
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]["regular"])
        self.assertTrue(rows[1]["regular"])
        self.assertEqual(rows[1]["v"], 800)               # 진짜 거래량 보존
        self.assertEqual(rows[0]["h"], 10.5)              # 진짜 OHLC
        self.assertTrue(all(r["src"] == "yahoo" for r in rows))


class MoversParseTest(unittest.TestCase):
    def test_gainers_prices_and_dedup(self) -> None:
        g = parse_movers(_movers_fixture(), section=isrc.MOVERS_GAINERS)
        syms = [r["symbol"] for r in g]
        self.assertEqual(syms, ["JAGX", "PENY"])          # 대소문자 중복 제거
        self.assertEqual(g[0]["price"], 34.46)
        self.assertEqual(g[1]["price"], 0.49)

    def test_active_and_etf_sections(self) -> None:
        a = parse_movers(_movers_fixture(), section=isrc.MOVERS_ACTIVE_SHARE)
        self.assertEqual([r["symbol"] for r in a], ["NVDA", "TSLA"])
        self.assertEqual(a[1]["price"], 440.10)
        etf = parse_movers(_movers_fixture(), section=isrc.MOVERS_GAINERS, assetclass="ETF")
        self.assertEqual([r["symbol"] for r in etf], ["TQQQ"])

    def test_missing_section_returns_empty(self) -> None:
        self.assertEqual(parse_movers({"data": {}}, section="Nope"), [])


class AggregateTest(unittest.TestCase):
    def test_1m_price_only_to_5m_ohlc(self) -> None:
        rows = parse_nasdaq_chart(_nasdaq_chart_fixture())
        bars = aggregate(rows, 5)
        # 9:30~9:34 버킷: o=9:30(340.10), h=340.55, l=339.90, c=9:32(339.90)
        reg = [b for b in bars if b["regular"]]
        self.assertEqual(len(reg), 1)
        b = reg[0]
        self.assertAlmostEqual(b["o"], 340.10)
        self.assertAlmostEqual(b["h"], 340.55)
        self.assertAlmostEqual(b["l"], 339.90)
        self.assertAlmostEqual(b["c"], 339.90)
        # 버킷 ts는 5분 경계 정렬(300으로 나누어떨어짐).
        self.assertEqual(b["ts"] % 300, 0)
        self.assertEqual(b["src"], "nasdaq")

    def test_volume_summed(self) -> None:
        rows = [
            {"ts": 300, "et": "x", "o": 1, "h": 2, "l": 1, "c": 2, "v": 10, "regular": True, "src": "yahoo"},
            {"ts": 360, "et": "x", "o": 2, "h": 3, "l": 1, "c": 3, "v": 5, "regular": True, "src": "yahoo"},
        ]
        bars = aggregate(rows, 5)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["v"], 15)
        self.assertEqual(bars[0]["h"], 3)
        self.assertEqual(bars[0]["l"], 1)

    def test_passthrough_for_1m(self) -> None:
        rows = parse_nasdaq_chart(_nasdaq_chart_fixture())
        self.assertEqual(len(aggregate(rows, 1)), len(rows))


class MergeRowsTest(unittest.TestCase):
    def test_dedup_sort_and_new_overwrites_same_rank(self) -> None:
        old = [{"ts": 1, "c": 10.0, "src": "nasdaq"}, {"ts": 2, "c": 20.0, "src": "nasdaq"}]
        new = [{"ts": 2, "c": 22.0, "src": "nasdaq"}, {"ts": 3, "c": 30.0, "src": "nasdaq"}]
        merged = merge_rows(old, new)
        self.assertEqual([r["ts"] for r in merged], [1, 2, 3])
        self.assertEqual(merged[1]["c"], 22.0)            # 동급 → 새 값

    def test_nasdaq_does_not_clobber_yahoo(self) -> None:
        yahoo = [{"ts": 5, "c": 1.5, "v": 999, "src": "yahoo"}]
        nasdaq = [{"ts": 5, "c": 9.9, "v": 0, "src": "nasdaq"}]  # price-only, 하위 소스
        merged = merge_rows(yahoo, nasdaq)
        self.assertEqual(merged[0]["src"], "yahoo")       # 유지
        self.assertEqual(merged[0]["v"], 999)

    def test_yahoo_upgrades_existing_nasdaq(self) -> None:
        nasdaq = [{"ts": 5, "c": 9.9, "v": 0, "src": "nasdaq"}]
        yahoo = [{"ts": 5, "c": 1.5, "v": 999, "src": "yahoo"}]  # 상위 소스 → 정정
        merged = merge_rows(nasdaq, yahoo)
        self.assertEqual(merged[0]["src"], "yahoo")


class RowsToBarsTest(unittest.TestCase):
    def test_regular_only_filter(self) -> None:
        rows = parse_nasdaq_chart(_nasdaq_chart_fixture())
        allbars = rows_to_bars("AAPL", rows)
        regbars = rows_to_bars("AAPL", rows, regular_only=True)
        self.assertEqual(len(allbars), 5)
        self.assertEqual(len(regbars), 3)                 # 9:30/9:31/9:32
        self.assertIsInstance(regbars[0], IntradayBar)
        self.assertTrue(all(b.regular for b in regbars))
        self.assertEqual(regbars[0].symbol, "AAPL")


class MergeWriteTest(unittest.TestCase):
    def test_layout_accumulates_and_histdata_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            rows = parse_nasdaq_chart(_nasdaq_chart_fixture())
            r1 = merge_write("AAPL", "1m", rows, cache_dir=d)
            self.assertEqual(r1["added"], 5)
            self.assertEqual(r1["total"], 5)
            path = Path(d) / "AAPL_1m.json"
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text())
            self.assertEqual(set(payload), {"symbol", "interval", "fetched", "rows"})
            self.assertEqual(payload["interval"], "1m")
            # 재실행: 같은 봉 + 새 봉 하나 → 병합 누적(중복 제거)
            extra = rows + [{"ts": rows[-1]["ts"] + 60, "et": rows[-1]["et"],
                             "o": 1, "h": 1, "l": 1, "c": 1, "v": 0.0,
                             "regular": False, "src": "nasdaq"}]
            r2 = merge_write("AAPL", "1m", extra, cache_dir=d)
            self.assertEqual(r2["total"], 6)
            self.assertEqual(r2["added"], 1)

    def test_histdata_load_intraday_reads_our_cache(self) -> None:
        # 우리가 쓴 캐시를 histdata.load_intraday 가 네트워크 없이 그대로 읽는다(양방향 호환).
        with tempfile.TemporaryDirectory() as d:
            rows = parse_nasdaq_chart(_nasdaq_chart_fixture())
            merge_write("AAPL", "1m", rows, cache_dir=d)
            orig = histdata.INTRADAY_CACHE_DIR
            histdata.INTRADAY_CACHE_DIR = Path(d)
            try:
                bars = histdata.load_intraday("AAPL", "1m")   # 캐시 히트 → 네트워크 미호출
            finally:
                histdata.INTRADAY_CACHE_DIR = orig
            self.assertEqual(len(bars), 5)
            self.assertTrue(hasattr(bars[0], "regular"))


class RateLimitedTest(unittest.TestCase):
    def test_fetch_nasdaq_maps_429(self) -> None:
        orig = isrc._http_get

        def boom(*a, **k):
            raise urllib.error.HTTPError("http://x", 429, "Too Many Requests", {}, None)
        isrc._http_get = boom
        try:
            with self.assertRaises(RateLimited):
                fetch_nasdaq("AAPL")
        finally:
            isrc._http_get = orig

    def test_fetch_yahoo_maps_429(self) -> None:
        class _FakeOpener:
            def open(self, url, timeout=None):
                raise urllib.error.HTTPError(url, 429, "rl", {}, None)

        orig = isrc._hd._opener
        isrc._hd._opener = lambda: _FakeOpener()
        try:
            with self.assertRaises(RateLimited):
                fetch_yahoo("SPY", "5m", now=1_700_000_000)
        finally:
            isrc._hd._opener = orig


class IntervalMinutesTest(unittest.TestCase):
    def test_known_and_unknown(self) -> None:
        self.assertEqual(interval_minutes("5m"), 5)
        self.assertEqual(interval_minutes("1h"), 60)
        with self.assertRaises(ValueError):
            interval_minutes("3m")


if __name__ == "__main__":
    unittest.main()
