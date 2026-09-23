"""histdata 오프라인 단위테스트 — 네트워크 없이 fixture로 파싱/조정/합성 수학 검증.

실행: PYTHONPATH=src python -m unittest tests.test_histdata
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from toss_trader import histdata
from toss_trader.histdata import (
    IntradayBar, _adj_factor, _apply_dividend_adjustment, _clean_num, _merge_intraday_rows,
    _mdy, _parse_fred_csv, _parse_nasdaq_dividends, _parse_nasdaq_historical,
    _parse_stooq_csv, _parse_yahoo_chart, _parse_yahoo_intraday, _rf_daily_map, _trade_date,
    align_panel, fetch_symbol, index_total_return, load_intraday, load_symbol, rows_to_candles,
    synthetic_leveraged, validate_synthetic, HistDataError,
)
from toss_trader.models import Candle


def _utc_epoch(y, m, d, hh=16, mm=0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


def _yahoo_daily_fixture() -> dict:
    # 3봉: 가운데 봉 close=null → 건너뜀. adjclose 존재.
    ts = [_utc_epoch(2020, 6, 15), _utc_epoch(2020, 6, 16), _utc_epoch(2020, 6, 17)]
    return {"chart": {"error": None, "result": [{
        "meta": {"gmtoffset": -4 * 3600, "exchangeTimezoneName": "America/New_York"},
        "timestamp": ts,
        "indicators": {
            "quote": [{
                "open":   [100.0, None, 108.0],
                "high":   [102.0, None, 111.0],
                "low":    [ 98.0, None, 107.0],
                "close":  [100.0, None, 110.0],
                "volume": [1000,  None, 1200],
            }],
            "adjclose": [{"adjclose": [95.0, None, 110.0]}],
        },
    }]}}


class ParseYahooDailyTest(unittest.TestCase):
    def test_skips_null_close_and_reads_adjclose(self) -> None:
        rows = _parse_yahoo_chart(_yahoo_daily_fixture())
        self.assertEqual(len(rows), 2)                       # null 봉 제외
        self.assertEqual(rows[0]["d"], "2020-06-15")         # ET 거래일
        self.assertEqual(rows[1]["d"], "2020-06-17")
        self.assertEqual(rows[0]["c"], 100.0)
        self.assertEqual(rows[0]["a"], 95.0)
        self.assertEqual(rows[1]["a"], 110.0)

    def test_error_payload_raises(self) -> None:
        with self.assertRaises(HistDataError):
            _parse_yahoo_chart({"chart": {"error": {"code": "Not Found"}, "result": None}})

    def test_missing_ohl_fallback_to_close(self) -> None:
        data = _yahoo_daily_fixture()
        q = data["chart"]["result"][0]["indicators"]["quote"][0]
        q["open"][0] = None
        q["high"][0] = None
        rows = _parse_yahoo_chart(data)
        self.assertEqual(rows[0]["o"], 100.0)   # close로 대체
        self.assertEqual(rows[0]["h"], 100.0)


class AdjustmentMathTest(unittest.TestCase):
    def test_adjusted_scales_ohlc_by_adjclose_over_close(self) -> None:
        rows = [{"d": "2020-06-15", "o": 100.0, "h": 102.0, "l": 98.0,
                 "c": 100.0, "v": 1000.0, "a": 95.0}]
        adj = rows_to_candles("X", rows, adjusted=True)[0]
        self.assertAlmostEqual(adj.close, 95.0)
        self.assertAlmostEqual(adj.open, 95.0)          # 100 * 0.95
        self.assertAlmostEqual(adj.high, 96.9)          # 102 * 0.95
        self.assertAlmostEqual(adj.low, 93.1)           # 98 * 0.95
        self.assertEqual(adj.volume, 1000.0)            # 거래량은 원시 유지

    def test_raw_leaves_prices_unscaled(self) -> None:
        rows = [{"d": "2020-06-15", "o": 100.0, "h": 102.0, "l": 98.0,
                 "c": 100.0, "v": 1000.0, "a": 95.0}]
        raw = rows_to_candles("X", rows, adjusted=False)[0]
        self.assertEqual((raw.open, raw.high, raw.low, raw.close), (100.0, 102.0, 98.0, 100.0))

    def test_missing_adjclose_factor_is_one(self) -> None:
        self.assertEqual(_adj_factor({"c": 100.0, "a": None}), 1.0)
        self.assertEqual(_adj_factor({"c": 0.0, "a": 50.0}), 1.0)
        rows = [{"d": "2020-06-15", "o": 10.0, "h": 11.0, "l": 9.0,
                 "c": 10.0, "v": 5.0, "a": None}]
        c = rows_to_candles("X", rows, adjusted=True)[0]
        self.assertEqual(c.close, 10.0)                 # a=None → 조정 없음

    def test_recent_bar_factor_one_past_bar_discounted(self) -> None:
        # adjclose==close(최신)면 계수 1, 과거(배당 포함)면 <1
        recent = _adj_factor({"c": 110.0, "a": 110.0})
        past = _adj_factor({"c": 100.0, "a": 95.0})
        self.assertAlmostEqual(recent, 1.0)
        self.assertLess(past, 1.0)


class StooqParseTest(unittest.TestCase):
    def test_parses_csv_with_null_adjclose(self) -> None:
        text = ("Date,Open,High,Low,Close,Volume\n"
                "2020-01-02,10.0,11.0,9.0,10.5,1000\n"
                "2020-01-03,10.5,12.0,10.0,11.5,2000\n")
        rows = _parse_stooq_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["d"], "2020-01-02")
        self.assertEqual(rows[0]["c"], 10.5)
        self.assertIsNone(rows[0]["a"])                 # Stooq는 배당조정 없음

    def test_challenge_page_raises(self) -> None:
        with self.assertRaises(HistDataError):
            _parse_stooq_csv("<!DOCTYPE html><html>JS challenge</html>")


class SyntheticLeverageTest(unittest.TestCase):
    def _base(self, closes: list[float]) -> list[Candle]:
        return [Candle("QQQ", date(2020, 1, 1 + i), c, c, c, c, 100)
                for i, c in enumerate(closes)]

    def test_pure_3x_no_costs(self) -> None:
        base = self._base([100.0, 110.0])               # r=+10%
        syn = synthetic_leveraged(base, 3, annual_expense=0.0, borrow_spread=0.0,
                                  rf_annual=0.0)
        self.assertEqual(syn[0].close, 100.0)
        self.assertAlmostEqual(syn[1].close, 130.0)     # 100*(1+3*0.10)

    def test_downside_amplified(self) -> None:
        base = self._base([100.0, 90.0])                # r=-10%
        syn = synthetic_leveraged(base, 3, annual_expense=0.0, borrow_spread=0.0,
                                  rf_annual=0.0)
        self.assertAlmostEqual(syn[1].close, 70.0)      # 100*(1-3*0.10)

    def test_expense_drag(self) -> None:
        base = self._base([100.0, 110.0])
        syn = synthetic_leveraged(base, 3, annual_expense=0.0095, borrow_spread=0.0,
                                  rf_annual=0.0, trading_days=252)
        exp_d = 0.0095 / 252
        self.assertAlmostEqual(syn[1].close, 100.0 * (1.30 - exp_d))

    def test_financing_applies_to_borrowed_portion(self) -> None:
        base = self._base([100.0, 110.0])
        syn = synthetic_leveraged(base, 3, annual_expense=0.0, borrow_spread=0.01,
                                  rf_annual=0.02, trading_days=252)
        financing = (3 - 1) * (0.02 / 252 + 0.01 / 252)
        self.assertAlmostEqual(syn[1].close, 100.0 * (1.30 - financing))

    def test_short_input_returns_empty(self) -> None:
        self.assertEqual(synthetic_leveraged(self._base([100.0]), 3), [])

    def test_default_symbol_name(self) -> None:
        syn = synthetic_leveraged(self._base([100.0, 101.0]), 3)
        self.assertEqual(syn[0].symbol, "QQQ-3x-sim")


class RfDailyMapTest(unittest.TestCase):
    def test_price_series_daily_return(self) -> None:
        d0, d1 = date(2020, 1, 1), date(2020, 1, 2)
        rf = [Candle("BIL", d0, 100.0, 100.0, 100.0, 100.0, 0),
              Candle("BIL", d1, 100.02, 100.02, 100.02, 100.02, 0)]
        m = _rf_daily_map([d0, d1], rf, "price", 0.02, 252)
        self.assertAlmostEqual(m[d1], 0.0002)           # 100.02/100 - 1
        # 이후 날짜는 직전값 forward-fill
        m2 = _rf_daily_map([d0, d1, date(2020, 1, 3)], rf, "price", 0.02, 252)
        self.assertAlmostEqual(m2[date(2020, 1, 3)], 0.0002)

    def test_yield_series_annualized(self) -> None:
        d0 = date(2020, 1, 2)
        rf = [Candle("^IRX", d0, 5.04, 5.04, 5.04, 5.04, 0)]  # 5.04% 할인율
        m = _rf_daily_map([d0], rf, "yield", 0.02, 252)
        self.assertAlmostEqual(m[d0], (5.04 / 100.0) / 252)

    def test_constant_fallback_without_rf(self) -> None:
        d0 = date(2020, 1, 2)
        m = _rf_daily_map([d0], None, "price", 0.02, 252)
        self.assertAlmostEqual(m[d0], 0.02 / 252)


class ValidateSyntheticTest(unittest.TestCase):
    def test_self_consistency_corr_one_zero_tracking(self) -> None:
        closes = [100.0]
        rets = [0.01, -0.02, 0.03, -0.01, 0.015, -0.005, 0.02, -0.03, 0.01, 0.004]
        for r in rets:
            closes.append(closes[-1] * (1 + r))
        base = [Candle("QQQ", date(2020, 1, 1 + i), c, c, c, c, 100)
                for i, c in enumerate(closes)]
        kw = dict(annual_expense=0.0, borrow_spread=0.0, rf_annual=0.0)
        real = synthetic_leveraged(base, 3, **kw)
        rep = validate_synthetic(base, real, 3, **kw)
        self.assertEqual(rep["n"], len(base))
        self.assertGreater(rep["corr"], 0.999999)
        self.assertLess(abs(rep["ann_tracking_diff"]), 1e-9)
        self.assertLess(rep["ann_tracking_error"], 1e-9)

    def test_no_overlap_returns_zero(self) -> None:
        base = [Candle("QQQ", date(2020, 1, 1 + i), 100.0 + i, 100.0 + i,
                       100.0 + i, 100.0 + i, 1) for i in range(3)]
        real = [Candle("TQQQ", date(2021, 1, 1 + i), 100.0, 100.0, 100.0, 100.0, 1)
                for i in range(3)]
        rep = validate_synthetic(base, real, 3)
        self.assertEqual(rep["n"], 0)
        self.assertEqual(rep["corr"], 0.0)


class AlignPanelTest(unittest.TestCase):
    def test_intersection_of_common_dates(self) -> None:
        d = [date(2020, 1, i) for i in range(1, 5)]
        panel = {
            "A": [Candle("A", d[0], 1, 1, 1, 1, 1), Candle("A", d[1], 2, 2, 2, 2, 1),
                  Candle("A", d[2], 3, 3, 3, 3, 1)],
            "B": [Candle("B", d[1], 5, 5, 5, 5, 1), Candle("B", d[2], 6, 6, 6, 6, 1),
                  Candle("B", d[3], 7, 7, 7, 7, 1)],
        }
        aligned = align_panel(panel)
        self.assertEqual([c.dt for c in aligned["A"]], [d[1], d[2]])
        self.assertEqual([c.dt for c in aligned["B"]], [d[1], d[2]])

    def test_empty_panel(self) -> None:
        self.assertEqual(align_panel({}), {})


class IntradayParseTest(unittest.TestCase):
    def _fixture(self) -> dict:
        # 2021-06-14 (월). pre-market(11:00 UTC) + 정규장(18:00 UTC).
        pre = int(datetime(2021, 6, 14, 11, 0, tzinfo=timezone.utc).timestamp())
        reg = int(datetime(2021, 6, 14, 18, 0, tzinfo=timezone.utc).timestamp())
        return {"chart": {"error": None, "result": [{
            "meta": {"gmtoffset": -4 * 3600},
            "timestamp": [pre, reg],
            "indicators": {"quote": [{
                "open": [10.0, 11.0], "high": [10.5, 11.5],
                "low": [9.5, 10.5], "close": [10.2, 11.2], "volume": [500, 800],
            }]},
        }]}}

    def test_regular_session_flag(self) -> None:
        rows = _parse_yahoo_intraday(self._fixture())
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]["regular"])   # 06:00~07:00 ET → 장전
        self.assertTrue(rows[1]["regular"])     # 13:00~14:00 ET → 정규장
        self.assertEqual(rows[0]["c"], 10.2)

    def test_intraday_bar_dataclass_roundtrip(self) -> None:
        rows = _parse_yahoo_intraday(self._fixture())
        bars = [IntradayBar("SPY", datetime.fromisoformat(r["et"]), r["o"], r["h"],
                            r["l"], r["c"], r["v"], r["regular"]) for r in rows]
        self.assertTrue(bars[1].regular)
        self.assertEqual(bars[0].symbol, "SPY")


class MergeIntradayTest(unittest.TestCase):
    def test_merge_dedups_and_new_overwrites(self) -> None:
        old = [{"ts": 1, "c": 10.0}, {"ts": 2, "c": 20.0}]
        new = [{"ts": 2, "c": 22.0}, {"ts": 3, "c": 30.0}]  # ts=2 정정
        merged = _merge_intraday_rows(old, new)
        self.assertEqual([r["ts"] for r in merged], [1, 2, 3])
        self.assertEqual(merged[1]["c"], 22.0)              # 새 값 우선


class TradeDateTest(unittest.TestCase):
    def test_utc_afternoon_maps_to_same_et_day(self) -> None:
        # 16:00 UTC = 12:00 EDT → 같은 날짜
        self.assertEqual(_trade_date(_utc_epoch(2020, 6, 15, 16, 0)), date(2020, 6, 15))


class CacheRoundTripTest(unittest.TestCase):
    """네트워크 경계만 스텁으로 대체하고 fetch→캐시→load→adjust→align 전 경로를 검증."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_cache = histdata.CACHE_DIR
        self._orig_fetch = histdata._fetch_yahoo_daily
        histdata.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self) -> None:
        histdata.CACHE_DIR = self._orig_cache
        histdata._fetch_yahoo_daily = self._orig_fetch
        self._tmp.cleanup()

    def _rows(self, base: float) -> list[dict]:
        return [
            {"d": "2020-06-15", "o": base, "h": base, "l": base, "c": base, "v": 10, "a": base * 0.95},
            {"d": "2020-06-16", "o": base + 1, "h": base + 1, "l": base + 1,
             "c": base + 1, "v": 10, "a": (base + 1) * 0.95},
            {"d": "2020-06-17", "o": base + 2, "h": base + 2, "l": base + 2,
             "c": base + 2, "v": 10, "a": base + 2},   # 최신봉 a==c
        ]

    def test_fetch_writes_cache_and_load_applies_adjustment(self) -> None:
        histdata._fetch_yahoo_daily = lambda sym: self._rows(100.0)
        payload = fetch_symbol("QQQ", force=True)
        self.assertEqual(payload["source"], "yahoo")
        self.assertTrue((histdata.CACHE_DIR / "QQQ.json").exists())
        adj = load_symbol("QQQ", adjusted=True)
        self.assertAlmostEqual(adj[0].close, 95.0)     # 100 * 0.95
        self.assertAlmostEqual(adj[-1].close, 102.0)   # 최신봉 계수 1
        raw = load_symbol("QQQ", adjusted=False)
        self.assertEqual(raw[0].close, 100.0)

    def test_cache_hit_skips_network(self) -> None:
        histdata._fetch_yahoo_daily = lambda sym: self._rows(100.0)
        fetch_symbol("QQQ", force=True)

        def _boom(sym):
            raise AssertionError("네트워크가 호출되면 안 됨(캐시 히트여야 함)")
        histdata._fetch_yahoo_daily = _boom
        candles = load_symbol("QQQ")               # 캐시에서 로드 → 네트워크 미호출
        self.assertEqual(len(candles), 3)

    def test_load_panel_and_align(self) -> None:
        def _fake(sym):
            return self._rows(100.0 if sym == "QQQ" else 50.0)
        histdata._fetch_yahoo_daily = _fake
        panel = histdata.load_panel(["QQQ", "SPY"], adjusted=True, force=True)
        self.assertEqual(set(panel), {"QQQ", "SPY"})
        aligned = align_panel(panel)
        self.assertEqual(len(aligned["QQQ"]), len(aligned["SPY"]))
        self.assertEqual([c.dt for c in aligned["QQQ"]], [c.dt for c in aligned["SPY"]])


class IntradayCacheMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = histdata.INTRADAY_CACHE_DIR
        self._orig_chunk = histdata._fetch_intraday_chunk
        histdata.INTRADAY_CACHE_DIR = Path(self._tmp.name)

    def tearDown(self) -> None:
        histdata.INTRADAY_CACHE_DIR = self._orig_dir
        histdata._fetch_intraday_chunk = self._orig_chunk
        self._tmp.cleanup()

    def test_refetch_merges_and_accumulates(self) -> None:
        batch = [
            [{"ts": 100, "et": "2021-06-14T10:00:00-04:00", "o": 1, "h": 1, "l": 1,
              "c": 1.0, "v": 5, "regular": True},
             {"ts": 200, "et": "2021-06-14T10:05:00-04:00", "o": 1, "h": 1, "l": 1,
              "c": 2.0, "v": 5, "regular": True}],
            [{"ts": 200, "et": "2021-06-14T10:05:00-04:00", "o": 1, "h": 1, "l": 1,
              "c": 2.5, "v": 9, "regular": True},   # ts=200 정정
             {"ts": 300, "et": "2021-06-14T10:10:00-04:00", "o": 1, "h": 1, "l": 1,
              "c": 3.0, "v": 5, "regular": True}],
        ]
        calls = {"n": 0}

        def _fake_chunk(sym, interval, p1, p2):
            i = min(calls["n"], len(batch) - 1)
            calls["n"] += 1
            return batch[i]
        histdata._fetch_intraday_chunk = _fake_chunk

        first = load_intraday("SPY", "5m", force=True)
        self.assertEqual(len(first), 2)
        second = load_intraday("SPY", "5m", force=True)   # 재요청 → 병합
        self.assertEqual([b.dt.isoformat() for b in second],
                         ["2021-06-14T10:00:00-04:00", "2021-06-14T10:05:00-04:00",
                          "2021-06-14T10:10:00-04:00"])
        self.assertEqual(second[1].close, 2.5)            # 정정값 반영
        self.assertEqual(second[1].volume, 9)


class FredParseTest(unittest.TestCase):
    def test_parses_close_only_and_skips_missing(self) -> None:
        text = ("DATE,NASDAQ100\n"
                "1986-01-02,110.00\n"
                "1986-01-03,.\n"          # 결측치 제외
                "1986-01-06,112.50\n")
        rows = _parse_fred_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["d"], "1986-01-02")
        self.assertEqual(rows[0]["c"], 110.0)
        self.assertEqual(rows[0]["o"], 110.0)       # OHLC 동일(종가전용)
        self.assertIsNone(rows[0]["a"])
        self.assertEqual(rows[1]["c"], 112.5)

    def test_empty_or_headeronly(self) -> None:
        self.assertEqual(_parse_fred_csv(""), [])
        self.assertEqual(_parse_fred_csv("DATE,DTB3\n"), [])


class IndexTotalReturnTest(unittest.TestCase):
    def _idx(self, closes: list[float]) -> list[Candle]:
        return [Candle("NASDAQ100", date(2020, 1, 1 + i), c, c, c, c, 0)
                for i, c in enumerate(closes)]

    def test_adds_daily_dividend_accrual(self) -> None:
        tr = index_total_return(self._idx([100.0, 110.0]), 0.0252, trading_days=252)
        self.assertEqual(tr[0].close, 100.0)
        self.assertAlmostEqual(tr[1].close, 100.0 * (1 + 0.10 + 0.0001))   # dy=0.0252/252

    def test_zero_yield_is_price_return(self) -> None:
        tr = index_total_return(self._idx([100.0, 110.0]), 0.0)
        self.assertAlmostEqual(tr[1].close, 110.0)
        self.assertEqual(tr[0].symbol, "NASDAQ100-TR")


class NasdaqParseTest(unittest.TestCase):
    def test_historical_cleans_and_sorts_ascending(self) -> None:
        data = {"data": {"tradesTable": {"rows": [
            {"date": "09/22/2026", "close": "$339.75", "volume": "40,711,790",
             "open": "$340.135", "high": "$345.34", "low": "$338.75"},
            {"date": "09/21/2026", "close": "$335.00", "volume": "1,000",
             "open": "$334", "high": "$336", "low": "$333"},
        ]}}}
        rows = _parse_nasdaq_historical(data)
        self.assertEqual([r["d"] for r in rows], ["2026-09-21", "2026-09-22"])  # 오름차순
        self.assertEqual(rows[0]["c"], 335.0)
        self.assertEqual(rows[0]["v"], 1000.0)          # 콤마 제거
        self.assertAlmostEqual(rows[1]["h"], 345.34)    # $ 제거
        self.assertIsNone(rows[0]["a"])

    def test_dividends_parse_skips_na(self) -> None:
        data = {"data": {"dividends": {"rows": [
            {"exOrEffDate": "09/21/2026", "amount": "$0.75143"},
            {"exOrEffDate": "N/A", "amount": "$0.5"},           # 제외
            {"exOrEffDate": "06/20/2026", "amount": "$0.68"},
        ]}}}
        divs = _parse_nasdaq_dividends(data)
        self.assertEqual(divs, [(date(2026, 9, 21), 0.75143), (date(2026, 6, 20), 0.68)])

    def test_clean_num_and_mdy(self) -> None:
        self.assertAlmostEqual(_clean_num("$1,234.56"), 1234.56)
        self.assertEqual(_mdy("06/20/2026"), date(2026, 6, 20))
        with self.assertRaises(ValueError):
            _clean_num("N/A")


class DividendAdjustmentTest(unittest.TestCase):
    def _rows(self) -> list[dict]:
        return [
            {"d": "2020-01-02", "o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0, "v": 1, "a": None},
            {"d": "2020-01-03", "o": 101.0, "h": 101.0, "l": 101.0, "c": 101.0, "v": 1, "a": None},
            {"d": "2020-01-06", "o": 102.0, "h": 102.0, "l": 102.0, "c": 102.0, "v": 1, "a": None},
        ]

    def test_dividend_discounts_prior_bars_only(self) -> None:
        # ex-date 2020-01-06, $1.00. prevclose=101 → 계수 (1-1/101). 이전 봉에만 적용.
        adj = _apply_dividend_adjustment(self._rows(), [(date(2020, 1, 6), 1.0)])
        self.assertAlmostEqual(adj[2]["a"], 102.0)          # 최신봉 계수 1
        self.assertAlmostEqual(adj[1]["a"], 100.0)          # 101*(1-1/101)=100
        self.assertAlmostEqual(adj[0]["a"], 100.0 * (1 - 1 / 101))
        # 총수익 조정가는 원가 이하(과거일수록 할인)
        self.assertLess(adj[0]["a"], adj[0]["c"])

    def test_ex_on_nontrading_day_maps_to_next_trading_bar(self) -> None:
        # 2020-01-04(토): 다음 거래일(01-06) 봉을 ex로 보고 직전 거래일(01-03) 종가로 계수.
        adj = _apply_dividend_adjustment(self._rows(), [(date(2020, 1, 4), 1.0)])
        self.assertAlmostEqual(adj[1]["a"], 100.0)
        self.assertAlmostEqual(adj[2]["a"], 102.0)

    def test_no_dividends_leaves_a_equal_close(self) -> None:
        adj = _apply_dividend_adjustment(self._rows(), [])
        self.assertEqual([r["a"] for r in adj], [100.0, 101.0, 102.0])

    def test_adjusted_candles_use_dividend_factor(self) -> None:
        adj = _apply_dividend_adjustment(self._rows(), [(date(2020, 1, 6), 1.0)])
        candles = rows_to_candles("X", adj, adjusted=True)
        self.assertAlmostEqual(candles[1].close, 100.0)     # 101 * (1-1/101)
        self.assertAlmostEqual(candles[2].close, 102.0)     # 최신 계수 1


class FallbackOrderTest(unittest.TestCase):
    """Yahoo 실패 → Nasdaq 폴백이 실제로 동작하는지(캐시 경계만 스텁)."""

    def setUp(self) -> None:
        import tempfile as _tf
        self._tmp = _tf.TemporaryDirectory()
        self._cache = histdata.CACHE_DIR
        self._y, self._n, self._s = (histdata._fetch_yahoo_daily,
                                     histdata._fetch_nasdaq_daily, histdata._fetch_stooq_daily)
        histdata.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self) -> None:
        histdata.CACHE_DIR = self._cache
        histdata._fetch_yahoo_daily, histdata._fetch_nasdaq_daily, histdata._fetch_stooq_daily = (
            self._y, self._n, self._s)
        self._tmp.cleanup()

    def test_falls_through_yahoo_to_nasdaq(self) -> None:
        def _y(sym):
            raise HistDataError("429 blocked")
        rows = [{"d": "2020-06-15", "o": 10.0, "h": 10.0, "l": 10.0, "c": 10.0, "v": 1, "a": 9.5}]
        histdata._fetch_yahoo_daily = _y
        histdata._fetch_nasdaq_daily = lambda sym: rows
        histdata._fetch_stooq_daily = lambda sym: (_ for _ in ()).throw(AssertionError("stooq 도달 금지"))
        payload = fetch_symbol("QQQ", force=True)
        self.assertEqual(payload["source"], "nasdaq")
        self.assertEqual(payload["rows"][0]["a"], 9.5)


if __name__ == "__main__":
    unittest.main()
