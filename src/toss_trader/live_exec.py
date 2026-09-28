"""실계좌 주문 실행 공용 머신러리 — run_dca(적립식)와 run_strategy(전략 브릿지)가 공유한다.

여기 있는 헬퍼들은 ``scripts/run_dca.py`` 에서 검증·하드닝된 로직을 그대로 옮긴 것이다
(정규장 접수 시간창 가드 · 결정론적 멱등 cid · ≤$10 분할매수 · 잔여 매수가능금액 캡 ·
FX 프리플라이트 · 세션 스킵). run_dca 는 이 모듈을 import 해 동일 이름으로 재노출하므로
동작·테스트가 바뀌지 않는다.

두 가지 확장점만 추가했다(기존 동작 불변):
  - ``client_order_id(..., prefix="dca")`` — cid 네임스페이스. run_dca 는 "dca", run_strategy 는
    "strat" 를 써서 같은 날 같은 종목이라도 두 봇의 서버측 cid dedup 이 섞이지 않는다.
  - ``execute_buys(..., record_progress=None, cid_prefix="dca")`` — 세션 진행 영속화를 콜백으로
    주입(각 스크립트가 자기 상태파일에 기록). 미지정 시 no-op.

표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import fx as fxmod
from .fees import TossFeeSchedule

__all__ = [
    "KST", "ORDER_CLOSE_BUFFER", "FEES", "FX_MODEL", "MAX_SPLIT_ORDERS_PER_RUN",
    "order_window", "order_window_status", "client_order_id", "plan_buy_chunks",
    "fx_window_preflight", "session_skip_symbols", "execute_buys",
]

# ── 정규장 주문 접수 시간 가드 ─────────────────────────────────────────
# 미국 금액주문(orderAmount)·소수점 매도는 정규장 시작 ~ **정규장 종료 1시간 전**까지만 접수된다
# (그 외엔 422 amount-order-outside-regular-hours / fractional-quantity-outside-regular-hours).
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


def client_order_id(session_date: str, sym: str, k: int | None = None, *,
                    prefix: str = "dca") -> str:
    """결정론적 멱등키. 단건 {prefix}-{session_date}-{sym}, 분할건 …-{sym}-{k}. ≤36자.

    동일 세션·동일 종목(·동일 청크) 재요청 시 서버가 원주문을 그대로 반환(10분 유효)해 중복을 막는다.
    분할 인덱스 접미사(-k)는 항상 보존하도록 base를 먼저 자른다(접미사 절단으로 인한 cid 충돌 방지).
    prefix 로 봇별 네임스페이스를 분리한다(run_dca="dca", run_strategy="strat").
    """
    cid = re.sub(r"[^A-Za-z0-9_-]", "-", f"{prefix}-{session_date}-{sym}")
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


def execute_buys(client, plan, session_date, skip, done, log, *,
                 split: bool = True,
                 max_orders_per_run: int = MAX_SPLIT_ORDERS_PER_RUN,
                 chunks_done: dict | None = None,
                 remaining_bp: float | None = None,
                 record_progress: Callable[[str, set, dict], None] | None = None,
                 on_error: Callable[[str, Exception], None] | None = None,
                 cid_prefix: str = "dca") -> bool:
    """플랜의 각 (종목, 금액)을 매수 접수한다. split이면 ≤$10 청크로 나눠(수수료 절감) 낸다.

    - 청크 번호는 **이번 세션에 이미 접수된 청크 수(chunks_done[sym])에서 이어** 매긴다. 따라서
      같은 세션의 다음 트리거가 부분 실패분을 재개해도 성공한 청크 cid를 절대 재사용하지 않는다
      → 서버 cid dedup(10분) 만료 후에도 중복 매수가 생기지 않는다.
    - 각 청크는 접수 성공 즉시 chunks_done[sym]에 (cid, amount)로 기록하고 record_progress로
      영속화한다(중간 실패/크래시 대비). done은 그 종목의 남은 청크가 모두 성공해야만 세팅한다.
    - remaining_bp 지정 시 이번 실행 누적 매수 노셔널을 실제 매수가능금액으로 제한(과매수 방지).
    - 이번 실행 총 주문 건수는 max_orders_per_run으로 제한(레이트리밋 그룹 ORDER·남용 가드).
    - 어떤 청크라도 실패하면 그 즉시 분할·실행을 중단(부분 체결 연쇄 방지)하고 False.
    반환: 전부 성공/스킵/보류면 True, 하나라도 실패면 False.
    """
    record = record_progress if record_progress is not None else (lambda *a, **k: None)
    ok = True
    orders_placed = 0
    chunks_done = chunks_done if chunks_done is not None else {}
    bp_left = remaining_bp                          # None이면 매수가능금액 제한 없음
    for sym, amt in plan:
        if sym in skip:
            log(f"  ⏭ {sym} 이미 이번 세션 주문 존재 → 건너뜀(중복 방지)")
            continue
        budget = max_orders_per_run - orders_placed
        if budget <= 0:
            log(f"  ⏸ 이번 실행 주문 한도({max_orders_per_run}건) 도달 → {sym} 이하 보류(다음 트리거에서 재개)")
            break
        start_idx = len(chunks_done.get(sym, []))   # 이번 세션 이미 접수된 청크 수 → 이어서 번호 매김
        chunks = plan_buy_chunks(amt, split=split, max_orders=min(budget, MAX_SPLIT_ORDERS_PER_RUN))
        # 재개(start_idx>0)면 단건이라도 인덱스 접미사를 붙여 앞선 청크 cid와의 충돌을 막는다.
        multi = len(chunks) > 1 or start_idx > 0
        placed_this_sym = 0
        sym_ok = True
        deferred = False
        for j, chunk in enumerate(chunks):
            if bp_left is not None and chunk > bp_left + 1e-9:
                log(f"  ⏸ {sym} ${chunk:.2f}: 잔여 매수가능금액 ${bp_left:.2f} 부족 → 이하 보류(과매수 방지)")
                deferred = True
                break                              # 매수가능금액 초과 → 이번 실행 보류(다음 트리거 재개)
            k = start_idx + j
            cid = client_order_id(session_date, sym, k if multi else None, prefix=cid_prefix)
            tag = f" [#{k + 1}]" if multi else ""
            try:
                resp = client.create_order(sym, "BUY", order_type="MARKET",
                                           order_amount=f"{chunk:.2f}", client_order_id=cid)
                oid = resp.get("orderId") if isinstance(resp, dict) else None
                orders_placed += 1
                placed_this_sym += 1
                if bp_left is not None:
                    bp_left -= chunk
                # 접수 성공 즉시 세션 누적에 기록·영속(재개 시 cid 재사용 금지의 근거).
                chunks_done.setdefault(sym, []).append({"cid": cid, "amount": round(float(chunk), 2)})
                record(session_date, done, chunks_done)
                log(f"  ✅ {sym} ${chunk:.2f} 매수 접수{tag}: orderId={oid} (cid={cid})")
            except Exception as e:  # noqa: BLE001
                sym_ok = False
                ok = False
                log(f"  ❌ {sym} ${chunk:.2f} 매수 실패{tag}{' [분할 중단]' if multi else ''}: {e}")
                if on_error is not None:
                    on_error(sym, e)               # 호출자 매핑(예: 422 규제 코드 → 안내 메시지)
                break                              # 어떤 오류든 즉시 분할 중단
        # 이 종목의 남은 청크를 모두 성공적으로 접수했을 때만 done(부분·보류는 done 아님 → 재개).
        if placed_this_sym > 0 and sym_ok and not deferred:
            done.add(sym)
            record(session_date, done, chunks_done)
        if not sym_ok:
            break                                  # 실패 시 이번 실행 전체 중단(안전)
        if deferred:
            break                                  # 매수가능금액 소진 → 이번 실행 중단(다음 트리거 재개)
    return ok


# ── FX(환전) 시간대 프리플라이트 ──────────────────────────────────────────
# 계좌가 KRW면 주문 시 자동환전이 야간 요율(≈0.5%)로 붙어 FX가 최대 비용이 될 수 있다.
# USD 매수가능금액을 조회해 '플랜 중 KRW 자동환전이 필요한 부분(shortfall)'을 계산하고, 지금이
# 우대창 밖이면 초과 FX 비용을 경고한다(+require_usd면 매수 보류).
FX_MODEL = fxmod.FxCostModel()


def fx_window_preflight(client, plan_total_usd: float, fx_rate: float, log, *,
                        require_usd: bool = False, now=None,
                        model: "fxmod.FxCostModel | None" = None) -> tuple[bool, float]:
    """플랜이 KRW 자동환전을 요구하고 지금이 환전 우대창 밖이면 경고(+선택 보류).

    반환 (proceed, shortfall_usd): proceed=False면 require_usd로 이번 매수를 미룬다.
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
        log("  ⏸ require_usd: USD 매수가능 확보 전까지 이번 매수 보류(앱에서 우대창에 KRW→USD 환전 후 재실행).")
        return False, shortfall
    return True, shortfall


def session_skip_symbols(client, session_date: str, log,
                         session_start: datetime | None = None) -> set[str]:
    """이번 세션에 이미 접수된 것으로 볼 종목 집합(서버 OPEN 매수주문 기준, best-effort).

    ⚠️ Order 스키마는 clientOrderId를 응답하지 않으므로 cid로는 매칭할 수 없다. 진짜 멱등은
    결정론적 cid의 서버측 dedup(동일 cid 재요청=원주문 반환, 10분)이 담당하며, 여기선
    '이번 세션에 이 봇이 낸 미체결(OPEN) 매수주문이 있는 종목'만 스킵해 중복 접수를 줄인다.

    사용자가 직접 낸 무관한 LIMIT 매수까지 스킵하면 정작 필요한 매수를 건너뛰어 **미매수**가 된다.
    그래서 (1) MARKET/금액(orderAmount) 주문이고 (2) orderedAt이 세션 시작 이후인 OPEN BUY만 대상.
    """
    skip: set[str] = set()
    try:
        resp = client.list_orders("OPEN")
        for o in (resp.get("orders") if isinstance(resp, dict) else []) or []:
            sym = o.get("symbol")
            if not sym or str(o.get("side", "")).upper() != "BUY":
                continue
            is_amount = o.get("orderAmount") not in (None, "")
            if str(o.get("orderType", "")).upper() != "MARKET" and not is_amount:
                continue
            if session_start is not None:
                oa = _parse_dt(o.get("orderedAt"))
                if oa is None or oa < session_start:
                    continue
            skip.add(sym)
    except Exception as e:  # noqa: BLE001
        log(f"  (사전 list_orders 확인 실패, 무시: {e})")
    return skip
