#!/usr/bin/env python3
"""기본 운용 = 적립식(DCA) + 분산 바이앤홀드.

게이트 검증 결론(reports/strategy_gate_2026-06-28.md): 이 시드·비용에선 액티브 타이밍이
B&H를 못 이긴다. 그래서 기본 전략을 '분산 바스켓을 매월 적립 매수 후 보유(매도 최소)'로 확정.

사용:
  python scripts/run_dca.py --backtest        # 분산안 과거 검증(키 불필요, 캐시 사용)
  python scripts/run_dca.py                    # 실계좌 적립 매수 '플랜'만 출력(dry-run, 주문 없음)
  python scripts/run_dca.py --execute          # 실주문(정규장에서만). TRADING_MODE=live 필요.

설계: 신규 현금(입금액)으로 target 비중 미달분을 매수만 한다(매도 없음→저회전).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toss_trader import policy_lifecycle as pl         # noqa: E402
from toss_trader.backtest import run_dca               # noqa: E402
from toss_trader.client import TossClient              # noqa: E402
from toss_trader.config import get_settings            # noqa: E402
from toss_trader.costs import CostModel                # noqa: E402
from toss_trader.fees import TossFeeSchedule           # noqa: E402
from toss_trader import fx as fxmod                     # noqa: E402
from toss_trader.marketdata import TossMarketData      # noqa: E402
from toss_trader.models import Candle                  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

# ── 확정된 기본 분산 배분 ──────────────────────────────────────────────
# QQQ 코어(성장) + SCHD(배당·저베타 분산) + GLD(위기 비상관 헤지).
# 게이트 검증상 QQQ 집중이 수익은 최고였으나 MDD −35%+. 분산으로 낙폭을 낮추되
# 성장 노출은 유지. 합 = 1.0.
DEFAULT_ALLOCATION = {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15}

DATA = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA / "_candle_cache"
LOG = DATA / "dca.log"
STATE = DATA / "dca_state.json"


def _log(msg: str) -> None:
    """콘솔 + data/dca.log 동시 기록(자동 실행 추적용)."""
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line)
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except ValueError:
            return {}
    return {}


def _save_state(d: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2))


# ── 정규장 주문 접수 시간 가드 ─────────────────────────────────────────
# 미국 금액주문(orderAmount)·소수점 매도는 정규장 시작 ~ **정규장 종료 1시간 전**까지만 접수된다
# (그 외엔 422 amount-order-outside-regular-hours / fractional-quantity-outside-regular-hours).
# 예) 서머타임(EDT): 정규장 22:30~05:00 KST → 접수 마감 04:00 KST.
#     표준시(EST, 11/1~): 정규장 23:30~06:00 KST → 접수 마감 05:00 KST.
# → launchd 트리거를 여러 개 두고(23:00·23:45·00:45 KST 등) 이 가드로 창 밖은 스킵, 세션당 1회만 실주문.
KST = timezone(timedelta(hours=9))
ORDER_CLOSE_BUFFER = timedelta(hours=1)  # 금액/소수점 주문 마감 = 정규장 종료 −1h


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)  # '+09:00' 오프셋 파싱 (Py≥3.11)
    except (ValueError, TypeError):
        return None


def order_window(reg: dict | None) -> tuple[datetime | None, datetime | None]:
    """정규장 세션 reg={startTime,endTime}에서 금액/소수점 주문 접수 구간 [start, end−1h] 반환.

    파싱 실패 시 (None, None). (market-calendar US today.regularMarket 스키마)
    """
    if not isinstance(reg, dict):
        return (None, None)
    start = _parse_dt(reg.get("startTime"))
    end = _parse_dt(reg.get("endTime"))
    if start is None or end is None:
        return (None, None)
    return (start, end - ORDER_CLOSE_BUFFER)


def order_window_status(reg: dict | None,
                        now: datetime | None = None) -> tuple[bool, str]:
    """(접수 가능 여부, 사유). now 미지정 시 현재 KST. DST/표준시 모두 캘린더 값으로 자동 판정."""
    now = now or datetime.now(KST)
    start, close = order_window(reg)
    if start is None or close is None:
        return (False, "정규장 세션 시간(startTime/endTime) 파싱 실패")
    if now < start:
        return (False, f"정규장 시작 전 (now={now.isoformat()} < start={start.isoformat()})")
    if now > close:
        return (False, "금액주문 마감(정규장 종료 1시간 전) 경과 "
                       f"(now={now.isoformat()} > close={close.isoformat()})")
    return (True, f"접수 가능 구간 내 (start={start.isoformat()} ~ close={close.isoformat()})")


def client_order_id(session_date: str, sym: str, k: int | None = None) -> str:
    """결정론적 멱등키. 단건 dca-{session_date}-{sym}, 분할건 dca-{session_date}-{sym}-{k}. ≤36자.

    동일 세션·동일 종목(·동일 청크) 재요청 시 서버가 원주문을 그대로 반환(10분 유효)해 중복을 막는다.
    분할 인덱스 접미사(-k)는 항상 보존하도록 base를 먼저 자른다(접미사 절단으로 인한 cid 충돌 방지).
    """
    cid = re.sub(r"[^A-Za-z0-9_-]", "-", f"dca-{session_date}-{sym}")
    if k is None:
        return cid[:36]
    suffix = f"-{k}"
    return cid[:36 - len(suffix)] + suffix


# 분할 매수 정책(수수료 절감): 건당 체결금액 ≤ $10 면 수수료 무료(fees.py). 큰 매수를 ≤$10 청크로
# 쪼개면 총 수수료가 준다. ⚠️ 정책 리스크: 토스가 '남용'으로 보거나 정책을 바꿀 수 있어 **플래그**로 둔다.
FEES = TossFeeSchedule()
MAX_SPLIT_ORDERS_PER_RUN = 20          # 이번 실행에서 낼 (분할 포함) 총 주문 건수 상한(레이트/남용 가드)


def plan_buy_chunks(amount: float, *, split: bool = True,
                    max_orders: int = MAX_SPLIT_ORDERS_PER_RUN,
                    min_chunk: float = 1.0) -> list[float]:
    """매수 금액을 (분할이 수수료를 줄일 때만) ≤$10 청크 리스트로. 아니면 [amount] 단건.

    ≤$10 단건은 이미 무료라 그대로 두고, 분할이 실제로 총수수료를 낮출 때만 여러 건으로 나눈다.
    """
    amt = round(float(amount), 2)
    if not split or amt <= FEES.free_threshold_usd:
        return [amt]
    plan = FEES.plan_split("BUY", amt, price=None, max_orders=max_orders, min_chunk=min_chunk)
    if len(plan.notionals) > 1 and plan.total_fee < FEES.order_fee("BUY", amt) - 1e-12:
        return [round(c, 2) for c in plan.notionals]
    return [amt]


def _execute_buys(client, plan, session_date, skip, done, log, *,
                  split: bool = True,
                  max_orders_per_run: int = MAX_SPLIT_ORDERS_PER_RUN) -> bool:
    """플랜의 각 (종목, 금액)을 매수 접수한다. split이면 ≤$10 청크로 나눠(수수료 절감) 낸다.

    - 각 청크 cid = dca-{session}-{sym}-{k}(단건은 -k 없음). 서버측 cid dedup으로 재실행 멱등.
    - 이번 실행 총 주문 건수는 max_orders_per_run으로 제한(레이트리밋 그룹 ORDER·남용 가드).
    - 어떤 청크라도 실패하면 그 즉시 분할·실행을 중단(부분 체결 연쇄 방지)하고 False.
    - 종목은 그 종목의 **모든 청크**가 성공했을 때만 done 처리(부분 성공은 done 아님 → 재실행 시 cid 멱등 재개).
    반환: 전부 성공/스킵이면 True, 하나라도 실패면 False.
    """
    ok = True
    orders_placed = 0
    for sym, amt in plan:
        if sym in skip:
            log(f"  ⏭ {sym} 이미 이번 세션 주문 존재 → 건너뜀(중복 방지)")
            continue
        budget = max_orders_per_run - orders_placed
        if budget <= 0:
            log(f"  ⏸ 이번 실행 주문 한도({max_orders_per_run}건) 도달 → {sym} 이하 보류(다음 트리거에서 재개)")
            break
        chunks = plan_buy_chunks(amt, split=split, max_orders=min(budget, MAX_SPLIT_ORDERS_PER_RUN))
        multi = len(chunks) > 1
        placed_this_sym = 0
        sym_ok = True
        for k, chunk in enumerate(chunks):
            cid = client_order_id(session_date, sym, k if multi else None)
            tag = f" [{k + 1}/{len(chunks)}]" if multi else ""
            try:
                resp = client.create_order(sym, "BUY", order_type="MARKET",
                                           order_amount=f"{chunk:.2f}", client_order_id=cid)
                oid = resp.get("orderId") if isinstance(resp, dict) else None
                orders_placed += 1
                placed_this_sym += 1
                log(f"  ✅ {sym} ${chunk:.2f} 매수 접수{tag}: orderId={oid} (cid={cid})")
            except Exception as e:  # noqa: BLE001
                sym_ok = False
                ok = False
                log(f"  ❌ {sym} ${chunk:.2f} 매수 실패{tag}{' [분할 중단]' if multi else ''}: {e}")
                break                              # 어떤 오류든 즉시 분할 중단
        if placed_this_sym > 0 and sym_ok:
            done.add(sym)
            _record_session_progress(session_date, done)   # 증분 저장(중간 실패 대비)
        if not sym_ok:
            break                                  # 실패 시 이번 실행 전체 중단(안전)
    return ok


# ── FX(환전) 시간대 프리플라이트 ──────────────────────────────────────────
# DCA 는 미 정규장(야간 KST)에 돈다. 계좌가 KRW면 주문 시 자동환전이 야간 요율(≈0.5%)로 붙어
# FX 가 최대 비용이 될 수 있다(검증·공식: 평일 09:00–15:30 KST 95% 우대=0.05%, 그 외 50%=0.5%).
# USD 매수가능금액을 조회해 '플랜 중 KRW 자동환전이 필요한 부분(shortfall)'을 계산하고, 지금이
# 우대창 밖이면 초과 FX 비용을 경고한다(+--require-usd면 매수 보류). ⚠️ 자동환전 발생·요율은
# 스펙 미노출(공식/언론 기반 가정) — API엔 환전 엔드포인트가 없어 시각 제어 불가(앱에서 수동 환전).
FX_MODEL = fxmod.FxCostModel()


def fx_window_preflight(client, plan_total_usd: float, fx_rate: float, log, *,
                        require_usd: bool = False, now=None,
                        model: "fxmod.FxCostModel | None" = None) -> tuple[bool, float]:
    """플랜이 KRW 자동환전을 요구하고 지금이 환전 우대창 밖이면 경고(+선택 보류).

    반환 (proceed, shortfall_usd): proceed=False면 --require-usd로 이번 매수를 미룬다.
    USD 매수가능금액을 조회해 플랜 총액 중 USD로 못 대는 부분(shortfall)을 계산하고,
    shortfall>0(=KRW 자동환전 필요) & 우대창 밖 → 야간 환전비·주간창 대비 초과분을 경고한다.
    조회 실패 시 (True, 0.0)로 보수적 통과(경고만 생략).
    """
    model = model or FX_MODEL
    now = now or datetime.now(fxmod.KST)
    try:
        usd_bp = float(client.get_buying_power("USD").get("cashBuyingPower", 0) or 0)
    except Exception as e:  # noqa: BLE001
        log(f"  (USD 매수가능 조회 실패 → FX 프리플라이트 생략: {e})")
        return True, 0.0
    shortfall = max(0.0, plan_total_usd - usd_bp)
    if shortfall <= 1e-9:
        log(f"  FX: 플랜 ${plan_total_usd:.2f} ≤ USD 매수가능 ${usd_bp:.2f} → 자동환전 불필요(추가 FX 없음).")
        return True, 0.0
    shortfall_krw = shortfall * fx_rate if fx_rate else 0.0
    if model.window.contains(now):
        log(f"  FX: USD 부족 ${shortfall:.2f}(≈₩{shortfall_krw:,.0f})는 KRW 자동환전 대상이나 "
            f"지금은 환전 우대창 내 → 우대 요율(≈{model.in_window_bps:.0f}bps) 기대.")
        return True, shortfall
    out_fee_krw = shortfall_krw * model.out_window_bps * 1e-4
    extra_krw = model.extra_cost_krw(shortfall_krw, now)
    nxt = model.window.next_open(now)
    log("  ⚠️ FX 경고: 지금은 환전 우대창(평일 09:00–15:30 KST) 밖. "
        f"USD 부족분 ${shortfall:.2f}(≈₩{shortfall_krw:,.0f})가 KRW 자동환전되면 야간 요율"
        f"(≈{model.out_window_bps:.0f}bps)로 ≈₩{out_fee_krw:,.0f} 환전비 — 주간창 대비 초과 ≈₩{extra_krw:,.0f}. "
        f"다음 우대창: {nxt:%Y-%m-%d %H:%M} KST. (자동환전 여부·요율은 가정 — scripts/fx_advice.py)")
    if require_usd:
        log("  ⏸ --require-usd: USD 매수가능 확보 전까지 이번 매수 보류(앱에서 우대창에 KRW→USD 환전 후 재실행).")
        return False, shortfall
    return True, shortfall


ALLOC_CANDIDATES = {
    "QQQ 100%": {"QQQ": 1.0},
    "QQQ60/SCHD25/GLD15 (기본)": {"QQQ": 0.60, "SCHD": 0.25, "GLD": 0.15},
    "분산5 QQQ40/SPY20/SCHD20/GLD10/EFA10": {"QQQ": 0.40, "SPY": 0.20, "SCHD": 0.20, "GLD": 0.10, "EFA": 0.10},
}


def _load_cached(sym: str, depth: int = 2500) -> list[Candle]:
    f = CACHE / f"{sym}_{depth}.json"
    if not f.exists():
        f = CACHE / f"{sym}_1250.json"
    rows = json.loads(f.read_text())
    return [Candle(sym, date.fromisoformat(r["d"]), r["o"], r["h"], r["l"], r["c"], r["v"]) for r in rows]


def backtest_mode() -> int:
    fx = 1541.6
    monthly = 50_000.0 / fx   # ₩5만/월 가정
    cost = CostModel()
    syms = sorted({s for w in ALLOC_CANDIDATES.values() for s in w})
    panel = {s: _load_cached(s) for s in syms}
    start = max(c[0].dt for c in panel.values())
    panel = {s: [c for c in cs if c.dt >= start] for s, cs in panel.items()}
    print(f"적립식 검증 | 월 ${monthly:.2f}(₩50,000) | 비용 {cost.roundtrip_bps:.0f}bps | "
          f"{start}~{panel[syms[0]][-1].dt}\n")
    for name, w in ALLOC_CANDIDATES.items():
        res = run_dca(panel, w, monthly_usd=monthly, cost_model=cost)
        print(f"  [{name}]\n     {res.summary()}")
    print("\n→ 기본 배분 확정: QQQ60/SCHD25/GLD15 (성장 노출 유지 + 분산으로 낙폭 완화).")
    print("  (QQQ100은 수익 최고지만 낙폭 최대. 곧 쓸 돈이 아니면 QQQ100도 합리적 — 취향/위험감내에 따라.)")
    return 0


def _session_skip_symbols(client, session_date: str, log) -> set[str]:
    """이번 세션에 이미 접수된 것으로 볼 종목 집합(서버 OPEN 매수주문 기준, best-effort).

    ⚠️ Order 스키마는 clientOrderId를 응답하지 않으므로 cid로는 매칭할 수 없다. 진짜 멱등은
    결정론적 cid의 서버측 dedup(동일 cid 재요청=원주문 반환, 10분)이 담당하며, 여기선
    '아직 미체결(OPEN)인 매수주문이 있는 종목'만 스킵해 중복 접수를 줄인다.
    """
    skip: set[str] = set()
    try:
        resp = client.list_orders("OPEN")
        for o in (resp.get("orders") if isinstance(resp, dict) else []) or []:
            sym = o.get("symbol")
            if sym and str(o.get("side", "")).upper() == "BUY":
                skip.add(sym)
    except Exception as e:  # noqa: BLE001
        log(f"  (사전 list_orders 확인 실패, 무시: {e})")
    return skip


def _record_session_progress(session_date: str, done: set[str]) -> None:
    """세션 진행 상황을 증분 저장(중간 실패 후 재실행 시 완료 종목 스킵용)."""
    st = _state()
    if st.get("session_date") != session_date:
        st = {"session_date": session_date}
    st["done_symbols"] = sorted(done)
    st["last_run"] = datetime.now(timezone.utc).isoformat()
    _save_state(st)


def _mark_session_complete(session_date: str, done: set[str] | None = None) -> None:
    """세션 완료 마킹 → 같은 밤 다른 트리거가 발화해도 즉시 no-op."""
    st = _state()
    if st.get("session_date") != session_date:
        st = {"session_date": session_date}
    if done is not None:
        st["done_symbols"] = sorted(done)
    st["complete"] = True
    st["last_run"] = datetime.now(timezone.utc).isoformat()
    _save_state(st)


def live_plan(execute: bool, auto: bool = False, split: bool = True,
              require_usd: bool = False) -> int:
    log = _log if auto else (lambda m: print(m))
    s = get_settings()
    s.require_credentials()
    client = TossClient(s)
    accounts = client.get_accounts()
    if not s.account_seq and isinstance(accounts, list) and accounts:
        import dataclasses
        client.s = dataclasses.replace(client.s, account_seq=str(accounts[0]["accountSeq"]))
    if not client.s.account_seq:
        log("❌ accountSeq를 확인할 수 없습니다 (.env ACCOUNT_SEQ).")
        return 1

    # 실주문(execute)이면: 정규장 → '접수 시간 창(정규장 종료 1시간 전 마감)' → 세션완료 가드를
    # 매수가능 조회보다 먼저 확인. (DST/표준시 상관없이 캘린더 값으로 자동 판정)
    session_date = None
    if execute:
        cal = client.get_market_calendar("US")
        today = cal.get("today", {}) or {}
        reg = today.get("regularMarket")
        session_date = today.get("date")
        if not reg:
            log(f"⏸ 미국 정규장 아님(금액주문 불가) → 매수 건너뜀. (US date={session_date})")
            return 0
        ok_win, why = order_window_status(reg)
        if not ok_win:
            log(f"⏸ 미국 금액주문 접수 시간 아님 → 매수 건너뜀. {why}")
            return 0
        st = _state()
        if st.get("session_date") == session_date and st.get("complete"):
            log(f"✅ 이미 이번 세션({session_date}) 적립 매수 완료 → 중복 방지, 종료.")
            return 0

    krw = float(client.get_buying_power("KRW").get("cashBuyingPower", 0) or 0)
    fx = float(client.get_exchange_rate("USD", "KRW").get("rate", 0) or 0)
    avail_usd = krw / fx if fx else 0.0
    holdings = client.get_holdings()
    held = {it["symbol"]: float(it.get("quantity", 0) or 0) * float(it.get("lastPrice", 0) or 0)
            for it in (holdings.get("items", []) if isinstance(holdings, dict) else [])}
    held_total = sum(held.values())
    log(f"계좌 {client.s.account_seq} | 매수가능 ₩{krw:,.0f} (= ${avail_usd:.2f} @ {fx}) | "
        f"보유평가 ${held_total:.2f}")

    # 목표: (보유 + 가용)을 target 비중으로. 미달분을 가용현금으로 매수만(매도 없음).
    total_after = held_total + avail_usd
    plan = []
    remaining = avail_usd
    for sym in sorted(DEFAULT_ALLOCATION, key=lambda x: DEFAULT_ALLOCATION[x] * total_after - held.get(x, 0), reverse=True):
        need = max(0.0, DEFAULT_ALLOCATION[sym] * total_after - held.get(sym, 0.0))
        buy = min(need, remaining)
        if buy >= 1.0:   # 1달러 미만 조각 매수는 생략(소액 누적 후 매수)
            plan.append((sym, round(buy, 2)))
            remaining -= buy
    log(f"적립 매수 플랜 (목표배분 {DEFAULT_ALLOCATION}): "
        + (", ".join(f"{s}=${a:.2f}" for s, a in plan) if plan else "없음"))
    if not plan:
        log("  (매수할 미달분 없음 또는 가용현금 < $1)")
        if execute and session_date:
            _mark_session_complete(session_date)
        return 0

    if split:
        preview = {s: plan_buy_chunks(a, split=True) for s, a in plan}
        n_split = sum(1 for s in preview if len(preview[s]) > 1)
        if n_split:
            log("  ↳ 분할매수(≤$10 무료 활용): "
                + ", ".join(f"{s}×{len(c)}" for s, c in preview.items() if len(c) > 1)
                + "  (--no-split-small-orders로 끔; 정책 리스크 유의)")

    # FX 시간대 프리플라이트: KRW 자동환전이 필요하고 우대창 밖이면 경고(+--require-usd면 보류).
    plan_total_usd = sum(a for _, a in plan)
    proceed, _short = fx_window_preflight(client, plan_total_usd, fx, log,
                                          require_usd=require_usd)
    if execute and require_usd and not proceed:
        return 0                                # 세션 미완료로 남겨 다음 트리거/우대창에서 재개

    if not execute:
        log("ℹ️ dry-run(플랜만). 실주문은 --execute (+TRADING_MODE=live, 정규장·접수시간창).")
        return 0
    if not client.s.is_live:
        log("❌ --execute에는 TRADING_MODE=live 필요. paper라 주문 안 함.")
        return 1

    # 멱등: 이미 이번 세션에 접수된 종목(로컬 상태 + 서버 OPEN 매수주문)은 건너뛴다.
    skip = _session_skip_symbols(client, session_date, log)
    st = _state()
    done = set(st.get("done_symbols", [])) if st.get("session_date") == session_date else set()
    skip |= done

    ok = _execute_buys(client, plan, session_date, skip, done, log, split=split)

    # 모든 대상이 성공/기존존재로 처리됐으면 세션 완료 마킹(이후 트리거는 즉시 no-op).
    if ok:
        remaining_targets = [s for s, _ in plan if s not in done and s not in skip]
        if not remaining_targets:
            _mark_session_complete(session_date, done)
    return 0 if ok else 1


# ── 옵트인 라이프사이클(레버리지) DCA 정책 ──────────────────────────────────
# ⚠️ 기본 비활성. POLICY=lifecycle AND LIFECYCLE_SLEEVE>0 일 때만 슬리브를 운용한다.
# 슬리브(계좌의 LIFECYCLE_SLEEVE 비율)는 QQQ(1x)+QLD(2x) 혼합으로 생애주기 글라이드 노출을
# 태우고, 나머지는 기존 기본배분(QQQ60/SCHD25/GLD15)을 따른다. 상세·리스크는 README 참고.
LIFECYCLE_BAND = 0.3          # 매도(디레버리지) 트리거 밴드: E_actual > E_target + band
LIFECYCLE_SLEEVE_SYMS = (pl.QQQ, pl.QLD)


def check_qld_tradable(client, log=lambda _m: None) -> bool:
    """QLD가 토스에서 거래 가능한 ETF인지 **읽기전용** 확인(주문 없음). 실패 시 보수적 False.

    자격증명이 없거나 조회 실패면 예외를 삼키고 False(슬리브 QLD 주문 보류)를 반환한다.
    """
    try:
        info = client.get_stocks(["QLD"])
    except Exception as e:  # noqa: BLE001
        log(f"  QLD 종목정보 조회 실패(보수적 보류): {e}")
        return False
    rows = info if isinstance(info, list) else (info.get("items") or info.get("stocks") or [])
    for r in rows or []:
        if str(r.get("symbol", "")).upper() == "QLD":
            status = str(r.get("status", "")).upper()
            sectype = str(r.get("securityType", "")).upper()
            ok = status in ("", "ACTIVE") and ("ETF" in sectype or sectype == "")
            log(f"  QLD 확인: status={status or 'n/a'} type={sectype or 'n/a'} "
                f"→ {'거래가능' if ok else '보류'}")
            return ok
    log("  QLD 종목정보 없음 → 거래가능성 확인 실패(보류).")
    return False


def _lifecycle_months_remaining(plan_years: int, *, persist: bool):
    """계획 잔여 개월수. 상태의 lifecycle_start_month(YYYY-MM)에서 경과분을 뺀다.

    최초 실행이면 이번 달을 시작으로 삼되, **execute(persist=True)일 때만** 상태에 저장한다
    (dry-run은 부작용 없음 → 매번 month 0 = 최대 레버리지로 보수적 표시).
    반환: (months_remaining, start_str, elapsed_months).
    """
    plan_months = int(plan_years) * 12
    st = _state()
    start = st.get("lifecycle_start_month")
    today = date.today()
    if not start:
        start = f"{today.year:04d}-{today.month:02d}"
        if persist:
            st["lifecycle_start_month"] = start
            _save_state(st)
    try:
        sy, sm = (int(x) for x in str(start).split("-")[:2])
        elapsed = max(0, (today.year - sy) * 12 + (today.month - sm))
    except (ValueError, TypeError):
        elapsed = 0
    return max(0, plan_months - elapsed), start, elapsed


def _account_snapshot(client, log):
    """(krw, fx, avail_usd, held{sym:usd}, held_total). live_plan 과 동일 규약."""
    krw = float(client.get_buying_power("KRW").get("cashBuyingPower", 0) or 0)
    fx = float(client.get_exchange_rate("USD", "KRW").get("rate", 0) or 0)
    avail_usd = krw / fx if fx else 0.0
    holdings = client.get_holdings()
    items = holdings.get("items", []) if isinstance(holdings, dict) else (holdings or [])
    held = {it["symbol"]: float(it.get("quantity", 0) or 0) * float(it.get("lastPrice", 0) or 0)
            for it in items}
    return krw, fx, avail_usd, held, sum(held.values())


def _base_buys(base_alloc: dict, base_held: dict, base_cash: float) -> dict:
    """기본배분(비-슬리브)에 대한 매수전용 플랜 {sym: usd}. live_plan 과 동일 로직."""
    base_total = sum(base_held.values()) + base_cash
    buys: dict = {}
    remaining = base_cash
    for sym in sorted(base_alloc, key=lambda x: base_alloc[x] * base_total - base_held.get(x, 0.0),
                      reverse=True):
        need = max(0.0, base_alloc[sym] * base_total - base_held.get(sym, 0.0))
        buy = min(need, remaining)
        if buy >= 1.0:
            buys[sym] = round(buy, 2)
            remaining -= buy
    return buys


def lifecycle_plan(execute: bool, auto: bool = False, split: bool = True,
                   require_usd: bool = False) -> int:
    """라이프사이클 슬리브 + 기본배분 적립 플랜. dry-run이 기본이며 주문을 내지 않는다.

    슬리브 = 계좌의 LIFECYCLE_SLEEVE 비율(QQQ/QLD 글라이드 노출), 나머지 = 기본배분.
    --execute + TRADING_MODE=live + 정규장 접수창에서만 **매수**를 접수한다(분할·멱등 재사용).
    ⚠️ 밴드 매도(디레버리지)는 자동 실행하지 않고 경고만 한다(레버리지 축소는 수동 검토 대상).
    """
    log = _log if auto else (lambda m: print(m))
    s = get_settings()
    s.require_credentials()

    sleeve = min(1.0, max(0.0, float(getattr(s, "lifecycle_sleeve", 0.0))))
    if sleeve <= 0.0:
        log("⚠️ LIFECYCLE_SLEEVE=0 → 라이프사이클 슬리브 비활성. 기본 DCA로 폴백.")
        return live_plan(execute, auto=auto, split=split)

    client = TossClient(s)
    accounts = client.get_accounts()
    if not s.account_seq and isinstance(accounts, list) and accounts:
        import dataclasses
        client.s = dataclasses.replace(client.s, account_seq=str(accounts[0]["accountSeq"]))
    if not client.s.account_seq:
        log("❌ accountSeq를 확인할 수 없습니다 (.env ACCOUNT_SEQ).")
        return 1

    session_date = None
    qld_ok = True
    if execute:
        cal = client.get_market_calendar("US")
        today = cal.get("today", {}) or {}
        reg = today.get("regularMarket")
        session_date = today.get("date")
        if not reg:
            log(f"⏸ 미국 정규장 아님(금액주문 불가) → 매수 건너뜀. (US date={session_date})")
            return 0
        ok_win, why = order_window_status(reg)
        if not ok_win:
            log(f"⏸ 미국 금액주문 접수 시간 아님 → 매수 건너뜀. {why}")
            return 0
        st = _state()
        if st.get("session_date") == session_date and st.get("complete"):
            log(f"✅ 이미 이번 세션({session_date}) 적립 매수 완료 → 중복 방지, 종료.")
            return 0
        qld_ok = check_qld_tradable(client, log)
        if not qld_ok:
            log("⏸ QLD 거래가능성 미확인 → 슬리브 QLD 매수는 이번 실행에서 보류(QQQ만).")

    krw, fx, avail_usd, held, held_total = _account_snapshot(client, log)
    total = held_total + avail_usd
    plan_years = int(getattr(s, "lifecycle_plan_years", 25))
    e_max = float(getattr(s, "lifecycle_emax", 2.0))
    months_remaining, start_str, elapsed = _lifecycle_months_remaining(
        plan_years, persist=bool(execute))

    log(f"계좌 {client.s.account_seq} | 매수가능 ₩{krw:,.0f} (= ${avail_usd:.2f} @ {fx}) | "
        f"보유평가 ${held_total:.2f}")
    log(f"라이프사이클(lifecycle) 슬리브={sleeve:.0%} 계획={plan_years}년 "
        f"(시작 {start_str}, 경과 {elapsed}개월, 잔여 {months_remaining}개월) e_max={e_max:g}")

    # 슬리브 목표노출 E — Samuelson share 는 스케일 불변이므로 슬리브 스케일로 계산.
    W_sleeve = sleeve * held_total
    monthly_est = sleeve * avail_usd            # 이번 세션 입금액을 월 적립 근사로 사용
    E = pl.lifecycle_target(W_sleeve, monthly_est, months_remaining, e_max=e_max)
    sleeve_cash = sleeve * avail_usd
    base_cash = avail_usd - sleeve_cash
    sleeve_held = {pl.QQQ: sleeve * held.get(pl.QQQ, 0.0), pl.QLD: held.get(pl.QLD, 0.0)}
    dep = pl.deposit_plan(sleeve_held, sleeve_cash, E, band=LIFECYCLE_BAND)
    log(f"  슬리브 목표노출 E={E:.3f} → 비중 QQQ {dep.weights[pl.QQQ]:.2f} / "
        f"QLD {dep.weights[pl.QLD]:.2f} (실제노출 E_actual={dep.e_actual:.3f})")
    if dep.sell_triggered:
        log("  ⚠️ 실제노출 > 목표+밴드 → 디레버리지 매도 신호: "
            + ", ".join(f"{sym}=${amt:.2f}" for sym, amt in dep.sells.items())
            + "  (자동 매도 안 함 — 수동 검토 권장)")

    # 기본배분(비-슬리브): QQQ는 슬리브가 (sleeve)만큼 가져가므로 (1−sleeve)만 배정.
    base_alloc = DEFAULT_ALLOCATION
    base_held = {}
    for sym in base_alloc:
        h = held.get(sym, 0.0)
        base_held[sym] = (1.0 - sleeve) * h if sym == pl.QQQ else h
    base_buys = _base_buys(base_alloc, base_held, base_cash)

    # 매수 병합(같은 심볼은 합산) — 슬리브 QLD가 보류면 제외.
    merged: dict = {}
    for sym, amt in dep.buys.items():
        if sym == pl.QLD and not qld_ok:
            log(f"  ↳ QLD 매수 ${amt:.2f} 보류(거래가능성 미확인).")
            continue
        merged[sym] = merged.get(sym, 0.0) + amt
    for sym, amt in base_buys.items():
        merged[sym] = merged.get(sym, 0.0) + amt
    plan = [(sym, round(amt, 2)) for sym, amt in merged.items() if round(amt, 2) >= 1.0]
    plan.sort(key=lambda x: x[1], reverse=True)

    log("적립 매수 플랜(슬리브+기본): "
        + (", ".join(f"{s_}=${a:.2f}" for s_, a in plan) if plan else "없음"))
    if not plan:
        log("  (매수할 미달분 없음 또는 가용현금 < $1)")
        if execute and session_date:
            _mark_session_complete(session_date)
        return 0

    if split:
        preview = {s_: plan_buy_chunks(a, split=True) for s_, a in plan}
        n_split = sum(1 for s_ in preview if len(preview[s_]) > 1)
        if n_split:
            log("  ↳ 분할매수(≤$10 무료 활용): "
                + ", ".join(f"{s_}×{len(c)}" for s_, c in preview.items() if len(c) > 1))

    # FX 시간대 프리플라이트: KRW 자동환전이 필요하고 우대창 밖이면 경고(+--require-usd면 보류).
    plan_total_usd = sum(a for _, a in plan)
    proceed, _short = fx_window_preflight(client, plan_total_usd, fx, log,
                                          require_usd=require_usd)
    if execute and require_usd and not proceed:
        return 0                                # 세션 미완료로 남겨 다음 트리거/우대창에서 재개

    if not execute:
        log("ℹ️ dry-run(플랜만). 실주문은 --policy lifecycle --execute "
            "(+TRADING_MODE=live, 정규장·접수시간창).")
        return 0
    if not client.s.is_live:
        log("❌ --execute에는 TRADING_MODE=live 필요. paper라 주문 안 함.")
        return 1

    skip = _session_skip_symbols(client, session_date, log)
    st = _state()
    done = set(st.get("done_symbols", [])) if st.get("session_date") == session_date else set()
    skip |= done
    ok = _execute_buys(client, plan, session_date, skip, done, log, split=split)
    if ok:
        remaining_targets = [s_ for s_, _ in plan if s_ not in done and s_ not in skip]
        if not remaining_targets:
            _mark_session_complete(session_date, done)
    return 0 if ok else 1


def tax_report_mode() -> int:
    """해외주식 양도세 리포트(dry-run) 위임. 구현은 scripts/tax_report.py."""
    import tax_report  # 같은 scripts/ 디렉터리(실행 시 sys.path[0]) — 주문 없음, 조회만
    return tax_report.run([])


def main() -> int:
    ap = argparse.ArgumentParser(description="적립식(DCA) + 분산 바이앤홀드 실행기")
    ap.add_argument("--backtest", action="store_true", help="분산안 과거 검증")
    ap.add_argument("--execute", action="store_true", help="실주문 실행(정규장·live 필요)")
    ap.add_argument("--auto", action="store_true",
                    help="자동 실행 모드: data/dca.log 기록 + US세션당 1회 중복방지 가드")
    ap.add_argument("--tax-report", action="store_true",
                    help="현재연도 실현/미실현 원화 + 하베스팅 플랜(dry-run, 주문 없음)")
    ap.add_argument("--policy", choices=("dca", "lifecycle"), default=None,
                    help="운용 정책. 미지정 시 .env POLICY(기본 dca). "
                         "lifecycle은 옵트인 레버리지 슬리브(LIFECYCLE_SLEEVE>0 필요, 기본 비활성).")
    ap.add_argument("--split-small-orders", action=argparse.BooleanOptionalAction, default=True,
                    help="DCA 매수를 건당 ≤$10 무료 청크로 분할해 수수료 절감(기본 ON). "
                         "정책 리스크가 있으면 --no-split-small-orders로 끈다.")
    ap.add_argument("--require-usd", action="store_true",
                    help="USD 매수가능금액이 부족해 KRW 자동환전(야간 ≈0.5%%)이 필요하고 지금이 "
                         "환전 우대창(평일 09:00-15:30 KST) 밖이면 매수를 보류(기본 OFF). "
                         "우대창에 앱에서 KRW→USD 환전 후 재실행하면 재개.")
    args = ap.parse_args()
    if args.tax_report:
        return tax_report_mode()
    if args.backtest:
        return backtest_mode()
    policy = args.policy or get_settings().policy
    if policy == "lifecycle":
        return lifecycle_plan(args.execute, auto=args.auto, split=args.split_small_orders,
                              require_usd=args.require_usd)
    return live_plan(args.execute, auto=args.auto, split=args.split_small_orders,
                     require_usd=args.require_usd)


if __name__ == "__main__":
    raise SystemExit(main())
