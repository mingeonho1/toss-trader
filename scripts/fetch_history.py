#!/usr/bin/env python3
"""기본 유니버스 히스토리 프리페치 — 키 불필요, 총수익(배당조정) 일봉 + 인트라데이 캐시.

일봉을 Yahoo(폴백 Stooq)에서 받아 data/_hist_cache 에 채우고,
심볼별 첫날/마지막날/봉수를 표로 출력한다. 인트라데이(1m/5m/60m)도 선택 수집한다.

사용:
    PYTHONPATH=src python scripts/fetch_history.py                 # 전체(일봉+단일주+인트라데이)
    PYTHONPATH=src python scripts/fetch_history.py --daily-only    # ETF+단일주 일봉만
    PYTHONPATH=src python scripts/fetch_history.py --etf-only      # 코어 ETF 일봉만
    PYTHONPATH=src python scripts/fetch_history.py --validate-leverage  # 합성 vs 실물 레버리지 검증
    PYTHONPATH=src python scripts/fetch_history.py --force         # 캐시 무시 재수집

주의: Yahoo v8 chart 는 공용 IP에서 429(Too Many Requests) 로 스로틀될 수 있다.
--sleep 로 요청 간 간격을 늘리고, 실패 심볼은 표에 사유와 함께 남긴다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import histdata  # noqa: E402
from toss_trader.histdata import (fetch_symbol, index_total_return,  # noqa: E402
                                  load_fred, load_intraday, load_symbol,
                                  synthetic_leveraged, validate_synthetic)

# 코어 ETF 유니버스(광폭 자산군 + 섹터 SPDR + 레버리지 실물).
ETF_UNIVERSE = [
    "QQQ", "SPY", "IWM", "EFA", "EEM", "GLD", "IEF", "TLT", "BIL", "SHY",
    "SCHD", "VTI", "VEA", "VNQ", "DBC", "TQQQ", "QLD", "SSO", "UPRO",
    # 섹터 SPDR
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB",
    # 단기금리 대용(있으면)
    "^IRX",
]

# 유동성 높은 대형 단일주 유니버스 ~100 (현 Nasdaq-100 + 거래대금 상위 S&P500).
# ⚠️ 생존편향(survivorship bias): "현재" 대형·유동주만 담아 과거 시점엔 상장 전이거나
#    지수 편입 전이었던 종목이 섞이고, 그 사이 상장폐지·피인수된 종목은 빠져 있다.
#    → 이 리스트로 과거를 백테스트하면 성과가 낙관적으로 편향된다(사후선택). 팩터/횡단면
#    연구에는 시점별 구성(point-in-time)이 필요하며, 여기 목록은 단일종목 파이프라인
#    개발·데이 트레이딩 대상 선별용일 뿐 역사적 유니버스가 아니다.
SINGLE_STOCKS = [
    # 메가캡 테크/반도체
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "AMD",
    "NFLX", "ADBE", "CRM", "ORCL", "CSCO", "QCOM", "TXN", "INTC", "MU", "AMAT",
    "LRCX", "KLAC", "ADI", "INTU", "NOW", "PANW", "SNPS", "CDNS", "MRVL", "SMCI",
    # 나스닥100 소비/헬스/산업
    "COST", "PEP", "TMUS", "CMCSA", "AMGN", "HON", "SBUX", "MDLZ", "GILD", "VRTX",
    "REGN", "ISRG", "BKNG", "ADP", "LULU", "PYPL", "ABNB", "PDD", "MELI", "ROST",
    "MNST", "KDP", "FTNT", "CTAS", "ORLY", "MAR", "CHTR", "ADSK", "NXPI", "PCAR",
    "AEP", "EXC", "XEL", "CPRT", "CSGP", "DXCM", "IDXX", "FAST", "ODFL", "WDAY",
    # 고성장/고변동 단일주(데이 트레이딩 단골)
    "CRWD", "DDOG", "TTD", "ZS", "SNOW", "NET", "PLTR", "COIN", "MSTR", "RBLX",
    "RIVN", "LCID", "SOFI", "HOOD", "DKNG", "UBER", "SHOP", "ARM", "CELH", "DELL",
    # 대형 금융/소비/에너지(S&P500 거래상위)
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "XOM", "CVX", "WMT",
    "DIS", "NKE", "KO", "PFE", "MRK", "T", "VZ", "BA", "CAT", "GE",
]

# 인트라데이 프리페치 계획: (심볼목록, 인터벌).
INTRADAY_5M_60M = ["SPY", "QQQ", "TQQQ", "NVDA", "TSLA", "AAPL", "AMD", "META", "MSFT", "AMZN"]
INTRADAY_1M = ["SPY", "QQQ"]

# 합성 레버리지 검증 맵: 합성심볼 → (기초, 배수, 실물, 실물 expense ratio).
LEVERAGE_MAP = [
    ("QQQ", 3.0, "TQQQ", 0.0084),
    ("QQQ", 2.0, "QLD", 0.0095),
    ("SPY", 3.0, "UPRO", 0.0091),
    ("SPY", 2.0, "SSO", 0.0089),
]

# FRED 장기 시계열(키 불필요, 종가전용): 지수/금리/변동성.
FRED_SERIES = ["NASDAQ100", "NASDAQCOM", "DTB3", "DGS10", "VIXCLS"]
NDX_DIV_YIELD = 0.007   # NASDAQ-100 근사 배당수익률(총수익 환산용).


def _row(sym: str, candles) -> str:
    if not candles:
        return f"  {sym:8} {'—':>12} {'—':>12} {'0':>7}"
    return (f"  {sym:8} {candles[0].dt.isoformat():>12} {candles[-1].dt.isoformat():>12} "
            f"{len(candles):>7}")


def fetch_daily(symbols: list[str], *, force: bool, sleep: float, adjusted: bool) -> dict:
    """일봉 수집 + 표 출력. 반환: {sym: candles}. 실패 심볼은 사유 출력 후 빈 리스트."""
    print(f"\n{'심볼':8} {'첫날':>12} {'마지막':>12} {'봉수':>7}   (source)")
    print("-" * 60)
    out: dict[str, list] = {}
    ok = fail = 0
    for sym in symbols:
        try:
            payload = fetch_symbol(sym, force=force)
            candles = load_symbol(sym, adjusted=adjusted)
            out[sym] = candles
            print(_row(sym, candles) + f"   ({payload.get('source', '?')})")
            ok += 1
        except Exception as e:  # noqa: BLE001
            out[sym] = []
            print(f"  {sym:8} {'FAILED':>12}   {type(e).__name__}: {str(e)[:60]}")
            fail += 1
        time.sleep(sleep)
    print("-" * 60)
    print(f"  성공 {ok} / 실패 {fail}")
    return out


def fetch_intraday(force: bool, sleep: float) -> None:
    print("\n=== 인트라데이 프리페치 ===")
    print(f"{'심볼':8} {'인터벌':>7} {'첫 바(ET)':>22} {'마지막 바(ET)':>22} {'봉수':>7}")
    print("-" * 74)
    plan = [(s, "5m") for s in INTRADAY_5M_60M] + \
           [(s, "60m") for s in INTRADAY_5M_60M] + \
           [(s, "1m") for s in INTRADAY_1M]
    for sym, interval in plan:
        try:
            bars = load_intraday(sym, interval, force=force)
            if bars:
                print(f"{sym:8} {interval:>7} {bars[0].dt.isoformat():>22} "
                      f"{bars[-1].dt.isoformat():>22} {len(bars):>7}")
            else:
                print(f"{sym:8} {interval:>7} {'(빈 응답)':>22}")
        except Exception as e:  # noqa: BLE001
            print(f"{sym:8} {interval:>7}   FAILED {type(e).__name__}: {str(e)[:50]}")
        time.sleep(sleep)


def fetch_fred(force: bool, sleep: float) -> dict:
    print("\n=== FRED 장기 시계열(종가전용) ===")
    print(f"{'series':12} {'첫날':>12} {'마지막':>12} {'봉수':>7}")
    print("-" * 48)
    out: dict[str, list] = {}
    for sid in FRED_SERIES:
        try:
            candles = load_fred(sid, force=force)
            out[sid] = candles
            print(f"{sid:12} {candles[0].dt.isoformat():>12} {candles[-1].dt.isoformat():>12} "
                  f"{len(candles):>7}")
        except Exception as e:  # noqa: BLE001
            out[sid] = []
            print(f"{sid:12}   FAILED {type(e).__name__}: {str(e)[:50]}")
        time.sleep(sleep)
    return out


def validate_fred_ndx_vs_tqqq(*, borrow_spread: float) -> None:
    """FRED NASDAQ100(1986+) → 배당총수익 근사 → 3x 합성 vs 실물 TQQQ(2010+ 겹침) 검증."""
    print("\n=== FRED NASDAQ100 3x 합성 vs 실물 TQQQ ===")
    try:
        ndx = load_fred("NASDAQ100")
        ndx_tr = index_total_return(ndx, NDX_DIV_YIELD, symbol="NDX-TR")
        # rf: DTB3(3M T-bill 수익률, %) → yield 종류.
        try:
            rf = load_fred("DTB3")
            rf_kind = "yield"
        except Exception:  # noqa: BLE001
            rf, rf_kind = None, "price"
        real = load_symbol("TQQQ", adjusted=True)
        rep = validate_synthetic(ndx_tr, real, 3.0, annual_expense=0.0084,
                                 borrow_spread=borrow_spread, rf_candles=rf, rf_kind=rf_kind,
                                 rf_annual=0.02)
        syn = synthetic_leveraged(ndx_tr, 3.0, annual_expense=0.0084, borrow_spread=borrow_spread,
                                  rf_candles=rf, rf_kind=rf_kind)
        rng = f"{rep['overlap_start']}~{rep['overlap_end']}" if rep["overlap_start"] else "—"
        print(f"NDX-TR 3x합성: {syn[0].dt}~{syn[-1].dt} ({len(syn)}봉, 1986+ 가능)")
        print(f"검증 겹침구간 {rng} ({rep['n']}봉): 상관 {rep['corr']:.4f} | "
              f"추적차(연) {rep['ann_tracking_diff']*100:+.2f}% | "
              f"추적오차(연) {rep['ann_tracking_error']*100:.2f}% | "
              f"CAGR 합성 {rep['cagr_syn']*100:.1f}% vs 실물 {rep['cagr_real']*100:.1f}%")
    except Exception as e:  # noqa: BLE001
        print(f"FRED NDX 검증 FAILED {type(e).__name__}: {str(e)[:80]}")


def validate_leverage(*, borrow_spread: float, rf_kind: str) -> None:
    print("\n=== 합성 레버리지 vs 실물 검증 (겹치는 구간) ===")
    print(f"{'합성':14} {'실물':6} {'구간':>25} {'봉수':>6} {'상관':>7} "
          f"{'추적차(연)':>10} {'추적오차(연)':>12} {'CAGR합성':>9} {'CAGR실물':>9}")
    print("-" * 108)
    # rf 프록시: BIL 총수익(price) 있으면 사용.
    rf_candles = None
    try:
        rf_candles = load_symbol("BIL", adjusted=True)
    except Exception:  # noqa: BLE001
        rf_candles = None
    for base_sym, lev, real_sym, exp in LEVERAGE_MAP:
        try:
            base = load_symbol(base_sym, adjusted=True)
            real = load_symbol(real_sym, adjusted=True)
            rep = validate_synthetic(
                base, real, lev,
                annual_expense=exp, borrow_spread=borrow_spread,
                rf_candles=rf_candles, rf_kind=("price" if rf_candles else rf_kind),
                rf_annual=0.02,
            )
            rng = f"{rep['overlap_start']}~{rep['overlap_end']}" if rep["overlap_start"] else "—"
            print(f"{base_sym}-{lev:g}x-sim  {real_sym:6} {rng:>25} {rep['n']:>6} "
                  f"{rep['corr']:>7.4f} {rep['ann_tracking_diff']*100:>9.2f}% "
                  f"{rep['ann_tracking_error']*100:>11.2f}% "
                  f"{rep['cagr_syn']*100:>8.1f}% {rep['cagr_real']*100:>8.1f}%")
        except Exception as e:  # noqa: BLE001
            print(f"{base_sym}-{lev:g}x-sim  {real_sym:6}   FAILED {type(e).__name__}: {str(e)[:50]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="기본 유니버스 히스토리 프리페치")
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재수집")
    ap.add_argument("--etf-only", action="store_true", help="코어 ETF 일봉만")
    ap.add_argument("--daily-only", action="store_true", help="일봉만(인트라데이 생략)")
    ap.add_argument("--intraday-only", action="store_true", help="인트라데이만")
    ap.add_argument("--fred-only", action="store_true", help="FRED 장기 시계열만")
    ap.add_argument("--no-single", action="store_true", help="단일주 유니버스 생략")
    ap.add_argument("--no-fred", action="store_true", help="FRED 프리페치 생략")
    ap.add_argument("--validate-leverage", action="store_true",
                    help="합성 vs 실물 레버리지 검증(ETF + FRED NDX 3x vs TQQQ)")
    ap.add_argument("--raw", action="store_true", help="배당 미조정(raw) 가격으로 표시")
    ap.add_argument("--sleep", type=float, default=1.0, help="요청 간 간격(초, 기본 1.0)")
    ap.add_argument("--symbols", type=str, default="", help="쉼표구분 심볼 오버라이드(일봉)")
    args = ap.parse_args()

    print(f"캐시 경로: {histdata.CACHE_DIR}")
    adjusted = not args.raw

    if args.validate_leverage:
        validate_leverage(borrow_spread=0.005, rf_kind="price")
        validate_fred_ndx_vs_tqqq(borrow_spread=0.005)
        return 0

    if args.fred_only:
        fetch_fred(args.force, args.sleep)
        return 0

    if args.intraday_only:
        fetch_intraday(args.force, args.sleep)
        return 0

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.etf_only:
        symbols = ETF_UNIVERSE
    else:
        symbols = ETF_UNIVERSE + ([] if args.no_single else SINGLE_STOCKS)

    print(f"일봉 유니버스: {len(symbols)}개 (adjusted={adjusted})")
    fetch_daily(symbols, force=args.force, sleep=args.sleep, adjusted=adjusted)

    if not args.no_fred:
        fetch_fred(args.force, args.sleep)

    if not args.daily_only and not args.etf_only:
        fetch_intraday(args.force, args.sleep)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
