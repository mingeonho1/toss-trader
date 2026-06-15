#!/usr/bin/env python3
"""합성 데이터로 백테스트 엔진 전체 경로를 검증한다(실 API 불필요).

키가 발급되면 이 합성 패널을 TossClient.get_candles로 받은 실제 일봉으로 교체한다.
목적: 엔진(전략→리밸런싱→비용→성과지표)이 끝까지 도는지 + 비용 영향 확인.
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.backtest import run_backtest      # noqa: E402
from toss_trader.costs import CostModel            # noqa: E402
from toss_trader.models import Candle              # noqa: E402
from toss_trader.strategy import SmaCrossStrategy  # noqa: E402


def synth_series(symbol: str, days: int, seed: int,
                 drift: float, vol: float, start: float) -> list[Candle]:
    """기하브라운운동 + 약한 추세 사이클로 합성 일봉 생성(주말 제외)."""
    rng = random.Random(seed)
    candles: list[Candle] = []
    price = start
    d = date(2024, 1, 1)
    made = 0
    while made < days:
        if d.weekday() < 5:  # 평일만
            cycle = 0.0008 * math.sin(made / 20.0)  # 완만한 추세 전환
            ret = drift + cycle + rng.gauss(0, vol)
            o = price
            price = max(1.0, price * (1 + ret))
            hi = max(o, price) * (1 + abs(rng.gauss(0, vol / 3)))
            lo = min(o, price) * (1 - abs(rng.gauss(0, vol / 3)))
            candles.append(Candle(symbol, d, o, hi, lo, price, rng.randint(1_000, 50_000)))
            made += 1
        d += timedelta(days=1)
    return candles


def main() -> int:
    days = 504  # 약 2년 거래일
    panel = {
        "AAPL": synth_series("AAPL", days, seed=1, drift=0.0006, vol=0.015, start=180),
        "MSFT": synth_series("MSFT", days, seed=2, drift=0.0005, vol=0.014, start=400),
        "NVDA": synth_series("NVDA", days, seed=3, drift=0.0009, vol=0.025, start=120),
        "KO":   synth_series("KO",   days, seed=4, drift=0.0001, vol=0.008, start=60),
    }

    seed_krw = 100_000
    fx = 1_380.0  # 가정 환율 (실전은 get_exchange_rate)
    start_cash_usd = seed_krw / fx

    strat = SmaCrossStrategy(universe=list(panel), fast=20, slow=60, max_positions=2)
    cost = CostModel()  # 보수적 가정 요율

    print(f"시드 {seed_krw:,}원 ≈ ${start_cash_usd:,.2f} | 왕복비용 ~{cost.roundtrip_bps:.0f}bps "
          f"({cost.roundtrip_bps/100:.2f}%)\n")

    res = run_backtest(panel, strat, start_cash=start_cash_usd, cost_model=cost)
    print(res.performance.summary())

    # 비용 0 가정과 비교 → 비용이 성과를 얼마나 깎는지 가시화
    res0 = run_backtest(panel, strat, start_cash=start_cash_usd,
                        cost_model=CostModel(0, 0, 0))
    drag = res0.performance.total_return - res.performance.total_return
    print(f"\n[비용영향] 무비용 가정 수익률 {res0.performance.total_return*100:+.2f}% "
          f"→ 비용 반영 {res.performance.total_return*100:+.2f}% (드래그 {drag*100:.2f}%p)")
    print("\n주의: 합성 데이터다. 양수여도 전략이 좋다는 뜻이 아니라 '엔진이 정상 동작'한다는 의미.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
