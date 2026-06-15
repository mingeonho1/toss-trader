#!/usr/bin/env python3
"""무인 엔진을 합성 데이터로 일자별 실행 → 손절/일일차단/일지자동화 검증(키 불필요).

급락 구간을 일부러 넣어 손절·트레일링·일일손실차단이 발동하는지 본다.
일지/로그는 data/demo/ 아래에 쓴다(실제 dev 일지와 분리).
"""
from __future__ import annotations

import math
import random
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader.broker import PaperBroker          # noqa: E402
from toss_trader.costs import CostModel             # noqa: E402
from toss_trader.engine import TradingEngine        # noqa: E402
from toss_trader.journal import JournalWriter       # noqa: E402
from toss_trader.marketdata import ReplayMarketData  # noqa: E402
from toss_trader.models import Candle               # noqa: E402
from toss_trader.risk import RiskConfig, RiskManager  # noqa: E402
from toss_trader.strategy import SmaCrossStrategy   # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"


def make_series(symbol: str, seed: int, n: int, start: float) -> list[Candle]:
    rng = random.Random(seed)
    out: list[Candle] = []
    price = start
    d = date(2025, 1, 1)
    i = 0
    while len(out) < n:
        if d.weekday() < 5:
            # 전반부 완만한 상승 → 후반 급락(손절/차단 유도)
            drift = 0.004 if i < n * 0.6 else -0.02
            price = max(1.0, price * (1 + drift + rng.gauss(0, 0.012)))
            out.append(Candle(symbol, d, price, price * 1.01, price * 0.99,
                              price, rng.randint(1000, 9000)))
            i += 1
        d += timedelta(days=1)
    return out


def main() -> int:
    if DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    n = 120
    panel = {
        "AAPL": make_series("AAPL", 1, n, 180),
        "NVDA": make_series("NVDA", 2, n, 120),
    }
    data = ReplayMarketData(panel)
    broker = PaperBroker(cash=100_000 / 1380.0, cost_model=CostModel())
    risk = RiskManager(RiskConfig(stop_loss_pct=0.08, take_profit_pct=0.25,
                                  trailing_stop_pct=0.12, daily_max_loss_pct=0.05))
    journal = JournalWriter(journal_dir=str(DEMO_DIR / "journal"),
                            data_dir=str(DEMO_DIR))
    strat = SmaCrossStrategy(universe=list(panel), fast=10, slow=30, max_positions=2)
    engine = TradingEngine(data=data, broker=broker, strategy=strat, risk=risk,
                           journal=journal, universe=list(panel))

    dates = sorted({c.dt for c in panel["AAPL"]})
    halts = exits = trades = errors = 0
    for d in dates:
        data.set_cursor(d)
        rep = engine.run_once(d)
        halts += 1 if rep.halted else 0
        exits += len(rep.exits)
        trades += len(rep.fills)
        errors += len(rep.errors)

    final_prices = {s: panel[s][-1].close for s in panel}
    final_eq = broker.equity(final_prices)
    print(f"실행일수 {len(dates)} | 총체결 {trades} | 보호청산 {exits}회 | "
          f"일일차단 {halts}일 | 에러 {errors}")
    print(f"최종 자본 ${final_eq:,.2f} (시작 ${100_000/1380:,.2f}) | "
          f"총비용 ${sum(f.cost for f in broker.fills):.2f}")
    runs = (DEMO_DIR / "runs.jsonl")
    mds = list((DEMO_DIR / "journal").glob("*.md"))
    print(f"\n일지 자동기록 확인: 마크다운 {len(mds)}개, runs.jsonl "
          f"{'생성됨' if runs.exists() else '없음'}, "
          f"errors.jsonl {'생성됨' if (DEMO_DIR/'errors.jsonl').exists() else '없음'}")
    print("(참고: 전략의 추세이탈 매도가 손절보다 먼저 작동하면 보호청산 0이 정상."
          " 손절/일일차단의 결정론적 검증은 scripts/selftest.py 참고.)")
    assert runs.exists() and mds and errors == 0, "일지 자동기록/무에러 실패"
    print("\n✅ 무인 엔진: 일자별 실행 + 일지 자동화 정상 동작")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
