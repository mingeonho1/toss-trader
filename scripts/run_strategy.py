#!/usr/bin/env python3
"""페이퍼 전략 1개를 **실계좌 주문**으로 돌리는 브릿지 — 기본은 항상 dry-run(주문 없음).

⚠️⚠️ 실주문은 **자동으로 켜지지 않는다**. 다음이 전부 참일 때만 실주문을 낸다:
  1) ``--execute`` 플래그 명시
  2) ``TRADING_MODE=live`` (paper면 거부)
  3) 미국 정규장 **금액주문 접수 시간창**(정규장 시작 ~ 종료 1시간 전) 안
  4) 사전 안전검사(레버리지 ETP 게이트 · 킬스위치) 통과
그 외에는 오늘의 '목표비중 → 주문 플랜'만 계산해 출력·기록한다.

동작:
  - paperlab 레지스트리에서 전략을 로드하고, **페이퍼 랩과 동일한 코드**(strategy.decide)로 최신
    종가 기준 오늘의 목표비중을 산출한다(동일 입력 → 동일 결정).
  - 실보유/매수가능금액을 읽어 주문 플랜을 만든다: **매도 먼저**(전량 매도는 단건, 부분 매도는 소수점
    6자리 내림 단건 — SEC/TAF 최소금액 최소화), 그다음 **매수**를 ≤$10 청크로 분할(무료), 매수가능금액과
    ``--max-usd`` 로 캡.
  - run_dca 의 하드닝된 머신러리(toss_trader.live_exec)를 재사용한다(시간창·멱등 cid·분할·BP캡·FX).

사용:
  PYTHONPATH=src python scripts/run_strategy.py --strategy ftlt_1x                 # dry-run 플랜
  PYTHONPATH=src python scripts/run_strategy.py --strategy ftlt_1x --sleeve-frac 0.2
  TRADING_MODE=live PYTHONPATH=src python scripts/run_strategy.py --strategy ftlt_1x --execute --max-usd 50
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_trader import live_exec                       # noqa: E402
from toss_trader import paperlab as pl                  # noqa: E402
from toss_trader.broker import _fmt_sell_qty            # noqa: E402
from toss_trader.client import TossClient               # noqa: E402
from toss_trader.config import get_settings             # noqa: E402
from toss_trader.errors import TossAPIError             # noqa: E402

DATA = ROOT / "data"
PAPERLAB_DIR = DATA / "paperlab"
LIVE_DIR = DATA / "strategy_live"
REPORTS = ROOT / "reports"
REPORT_LATEST = REPORTS / "strategy_live_latest.md"

MIN_TRADE_USD = 1.0             # $1 미만 조각 주문은 생략(소액 누적 후)
DEFAULT_MAX_PAPER_DD = 0.35    # 페이퍼 장부 낙폭 킬스위치 기본(−35%). 0 이하로 주면 끔.
DEFAULT_MAX_INTRADAY_LOSS = 0.10  # 계좌 당일 손익 킬스위치 기본(−10%). 0 이하로 주면 끔.

# 한국 레버리지/인버스 ETP 규제 안내(리테일). 첫 거래 예탁금 + 교육, 단일주식 레버리지는 건별 예탁금.
REG_MESSAGE = (
    "⛔ 레버리지/인버스 ETP 규제(한국 리테일): 최초 거래 시 기본예탁금 ₩10,000,000 예치 + "
    "사전 위험고지 교육 1시간 이수가 필요합니다. 개별종목(단일주식) 레버리지 ETP는 매수 건마다 "
    "기본예탁금 ₩30,000,000이 필요합니다. 요건 충족 후에도 이 봇으로 진행하려면 "
    "--allow-leveraged-etp 를 명시하세요."
)

BUY_CID_PREFIX = "strat"       # 매수 멱등키 네임스페이스(DCA 봇 "dca"와 분리 → 서버 dedup 안 섞임)
SELL_CID_PREFIX = "strat-sell"  # 매도 멱등키 네임스페이스


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────────────────────── 주문 플랜 자료형
@dataclass(frozen=True)
class SellOrder:
    symbol: str
    qty: str                 # 소수점 6자리 내림 문자열(_fmt_sell_qty)
    est_price: float
    est_proceeds: float
    full_exit: bool          # 목표비중 0 → 전량 매도(단건)


@dataclass(frozen=True)
class BuyOrder:
    symbol: str
    usd: float               # 이번 실행 매수 노셔널(캡 적용 후)
    chunks: list[float]      # ≤$10 분할(무료). 단건이면 [usd].


@dataclass
class OrderPlan:
    target: dict[str, float]
    total_equity: float
    investable: float
    sells: list[SellOrder] = field(default_factory=list)
    buys: list[BuyOrder] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def buy_total(self) -> float:
        return round(sum(b.usd for b in self.buys), 2)


# ─────────────────────────────────────────── 목표비중(페이퍼 랩과 동일 코드)
def compute_target_weights(strategy, panel: Mapping[str, Sequence[Any]],
                           strategy_state: Mapping[str, Any] | None = None) -> dict[str, float]:
    """전략의 최신 종가 기준 오늘의 목표비중. 페이퍼 랩과 **동일한** strategy.decide 를 호출한다.

    - ``panel[sym]`` = 그 종목의 오름차순 Candle 전체(캐시). decide 는 가시 히스토리(오늘 종가까지)만
      본다 → 미래참조 없음. 페이퍼 랩이 최신 종가 t 에서 산출한 목표(t+1 체결 대상)와 동일하다.
    - ``strategy_state`` = 페이퍼 랩 영속 strategy_state(있으면 상태연속성 보존; 없으면 {}).
    반환: {sym: weight>0}. 빈 dict 는 100% 현금.
    """
    visible = {s: list(panel.get(s, []) or []) for s in strategy.universe()}
    tw = strategy.decide(visible, dict(strategy_state or {})) or {}
    return {s: float(w) for s, w in tw.items() if float(w) > 0}


# ─────────────────────────────────────────── 주문 플랜 구성(매도 먼저 → 매수)
def build_order_plan(target: Mapping[str, float], holdings: Mapping[str, Mapping[str, float]],
                     buying_power: float, *, sleeve_frac: float = 1.0,
                     max_usd: float | None = None, split: bool = True,
                     min_trade: float = MIN_TRADE_USD) -> OrderPlan:
    """목표비중 → (매도 먼저, 매수 나중) 주문 플랜.

    - ``holdings[sym] = {"qty", "price"}`` (실보유; price=현재가/lastPrice).
    - 투자가능액 ``investable = sleeve_frac × (매수가능금액 + 보유평가)``. 목표달러 = weight × investable.
    - **매도**: 목표 미달(초과보유) 종목. 목표비중 0 이면 전량 매도(단건), 아니면 초과분 수량을 소수점
      6자리 **내림**(_fmt_sell_qty)해 단건 매도. 분할하지 않는다(SEC/TAF 최소금액 때문에 쪼갤수록 비쌈).
    - **매수**: 목표 초과(미달보유) 종목. 큰 미달부터 매수가능금액·max_usd 한도 내에서 ≤$10 청크로 분할.
      ⚠️ 매도 대금은 즉시 정산되지 않으므로 매수는 **현재 매수가능금액**으로만 캡(과매수 방지, run_dca 규약).
    """
    sleeve_frac = min(1.0, max(0.0, float(sleeve_frac)))
    held_val: dict[str, float] = {}
    for s, h in holdings.items():
        q = float(h.get("qty", 0.0) or 0.0)
        p = float(h.get("price", 0.0) or 0.0)
        if q > 0 and p > 0:
            held_val[s] = q * p
    total_equity = float(buying_power) + sum(held_val.values())
    investable = sleeve_frac * total_equity
    syms = set(target) | set(held_val)
    tval = {s: float(target.get(s, 0.0)) * investable for s in syms}

    plan = OrderPlan(target=dict(target), total_equity=round(total_equity, 2),
                     investable=round(investable, 2))

    # 1) 매도(초과 보유분) — 단건. 목표 0 → 전량, 아니면 초과 수량 내림.
    for s in sorted(syms):
        cur = held_val.get(s, 0.0)
        h = holdings.get(s, {})
        px = float(h.get("price", 0.0) or 0.0)
        held_qty = float(h.get("qty", 0.0) or 0.0)
        if px <= 0 or held_qty <= 0:
            continue
        diff = tval.get(s, 0.0) - cur
        if diff >= -min_trade:
            continue
        full = float(target.get(s, 0.0)) <= 0.0
        raw_qty = held_qty if full else min(held_qty, (-diff) / px)
        qty_str = _fmt_sell_qty(raw_qty)          # 소수점 6자리 내림, 보유초과 방지
        if qty_str is None:
            continue
        q = float(qty_str)
        if q <= 0:
            continue
        plan.sells.append(SellOrder(symbol=s, qty=qty_str, est_price=px,
                                    est_proceeds=round(q * px, 2), full_exit=full))

    # 2) 매수(미달분) — 큰 미달부터, 매수가능금액·max_usd 캡, ≤$10 분할.
    buy_needs = sorted(((s, tval.get(s, 0.0) - held_val.get(s, 0.0)) for s in syms),
                       key=lambda kv: kv[1], reverse=True)
    bp_left = float(buying_power)
    spent = 0.0
    for s, diff in buy_needs:
        if diff <= min_trade:
            continue
        usd = diff
        if bp_left <= 0:
            plan.notes.append(f"{s}: 매수가능금액 소진 → 보류")
            continue
        usd = min(usd, bp_left)
        if max_usd is not None:
            usd = min(usd, max_usd - spent)
        usd = round(usd, 2)
        if usd < min_trade:
            continue
        chunks = live_exec.plan_buy_chunks(usd, split=split)
        plan.buys.append(BuyOrder(symbol=s, usd=usd, chunks=[round(c, 2) for c in chunks]))
        bp_left -= usd
        spent += usd
    return plan


# ─────────────────────────────────────────── 안전장치: 레버리지 ETP 게이트
def _stock_rows(info: Any) -> list[dict]:
    if isinstance(info, list):
        return [r for r in info if isinstance(r, dict)]
    if isinstance(info, dict):
        return [r for r in (info.get("items") or info.get("stocks") or []) if isinstance(r, dict)]
    return []


def leverage_gate(client, symbols: Sequence[str], *, allow_leveraged: bool,
                  log) -> tuple[bool, list[str], dict[str, float]]:
    """매수 대상 종목의 stocks info `leverageFactor` 로 레버리지/인버스 ETP 사전 검사.

    |leverageFactor| > 1 인 종목이 있으면 규제 안내(REG_MESSAGE)를 출력하고, allow_leveraged 아니면 거부.
    조회가 422 prerequisite-required / stock-restricted 로 실패하면 그 자체가 규제/제한 신호로 보고 거부.
    반환: (통과여부, 위반심볼목록, {sym: factor}).
    """
    syms = sorted({s for s in symbols if s})
    if not syms:
        return True, [], {}
    try:
        info = client.get_stocks(syms)
    except TossAPIError as e:
        if e.code in ("prerequisite-required", "stock-restricted"):
            log(REG_MESSAGE)
            log(f"  (stocks info 조회 {e.code} → 규제/제한 종목으로 간주, 거부)")
            return False, syms, {}
        log(f"  ⚠️ stocks info 조회 실패({e.code}) → 레버리지 확인 불가, 안전상 거부: {e}")
        return False, syms, {}
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠️ stocks info 조회 실패 → 레버리지 확인 불가, 안전상 거부: {e}")
        return False, syms, {}

    factors: dict[str, float] = {}
    for r in _stock_rows(info):
        sym = str(r.get("symbol", "")).upper()
        lf = r.get("leverageFactor")
        if sym:
            try:
                factors[sym] = float(lf) if lf is not None else 1.0
            except (TypeError, ValueError):
                factors[sym] = 1.0
    offending = sorted(s for s in syms if abs(factors.get(s, 1.0)) > 1.0 + 1e-9)
    if offending:
        log(f"  레버리지/인버스 ETP 감지: "
            + ", ".join(f"{s}(×{factors.get(s, 1.0):g})" for s in offending))
        log(REG_MESSAGE)
        if not allow_leveraged:
            return False, offending, factors
        log("  ⚠️ --allow-leveraged-etp 지정 → 규제 요건 충족을 가정하고 진행(본인 책임).")
    return True, offending, factors


def map_order_error(exc: Exception) -> str | None:
    """주문 422 코드를 사람이 읽을 안내로 매핑. 매핑 없으면 None.

    - prerequisite-required / stock-restricted → 레버리지 ETP 규제 안내(REG_MESSAGE).
    - account-restricted → 계좌 주문 제한 안내.
    - insufficient-buying-power → 매수가능금액 부족 안내.
    """
    code = getattr(exc, "code", None)
    if code in ("prerequisite-required", "stock-restricted"):
        return REG_MESSAGE
    if code == "account-restricted":
        return "⛔ 계좌 상태가 주문을 허용하지 않습니다(거래정지/제한 등). 토스 앱/고객센터에서 확인하세요."
    if code == "insufficient-buying-power":
        return "⛔ 매수가능금액 부족. 주문금액/--max-usd 를 줄이거나 USD 매수가능금액을 먼저 확보하세요."
    return None


# ─────────────────────────────────────────── 안전장치: 킬스위치
def kill_switch_check(paper_dd: float | None, intraday_pnl: float | None, *,
                      max_paper_dd: float, max_intraday_loss: float) -> tuple[bool, list[str]]:
    """페이퍼 장부 낙폭 / 계좌 당일 손익이 한도를 넘으면 거래 거부.

    - ``paper_dd`` (음수, 예 −0.30) < −|max_paper_dd| 면 위반. max_paper_dd<=0 이면 검사 안 함.
    - ``intraday_pnl`` (음수) < −|max_intraday_loss| 면 위반. max_intraday_loss<=0 이면 검사 안 함.
    반환: (통과여부, 사유목록).
    """
    reasons: list[str] = []
    if max_paper_dd and max_paper_dd > 0 and paper_dd is not None:
        if paper_dd <= -abs(max_paper_dd) + 1e-12:
            reasons.append(f"페이퍼 장부 낙폭 {paper_dd * 100:.1f}% ≤ 한도 −{abs(max_paper_dd) * 100:.1f}%")
    if max_intraday_loss and max_intraday_loss > 0 and intraday_pnl is not None:
        if intraday_pnl <= -abs(max_intraday_loss) + 1e-12:
            reasons.append(
                f"계좌 당일 손익 {intraday_pnl * 100:.1f}% ≤ 한도 −{abs(max_intraday_loss) * 100:.1f}%")
    return (not reasons), reasons


def paper_drawdown(name: str) -> float | None:
    """페이퍼 랩 상태(data/paperlab/{name})의 단위장부 에쿼티 곡선 최대낙폭. 없으면 None."""
    state = pl.load_state(PAPERLAB_DIR, name)
    if not state:
        return None
    vals = [float(r[1]) for r in (state.get("equity") or []) if len(r) >= 2]
    return pl.max_drawdown(vals) if len(vals) >= 2 else None


def paper_strategy_state(name: str) -> dict[str, Any]:
    """페이퍼 랩 영속 strategy_state(결정 연속성용). 없으면 {}."""
    state = pl.load_state(PAPERLAB_DIR, name)
    if isinstance(state, dict):
        ss = state.get("strategy_state")
        if isinstance(ss, dict):
            return ss
    return {}


# ─────────────────────────────────────────── 상태(멱등·세션당 1회)
def _state_path(name: str) -> Path:
    return LIVE_DIR / f"{name}_state.json"


def load_live_state(name: str) -> dict[str, Any]:
    p = _state_path(name)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save_live_state(name: str, st: dict[str, Any]) -> None:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    st["last_run"] = _now_iso()
    _state_path(name).write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _session_state(name: str, session_date: str) -> dict[str, Any]:
    st = load_live_state(name)
    if st.get("session_date") != session_date:
        st = {"session_date": session_date}
    st.setdefault("sells_done", {})
    st.setdefault("chunks_done", {})
    st.setdefault("done_symbols", [])
    return st


# ─────────────────────────────────────────── 로깅
def log_jsonl(name: str, record: dict[str, Any]) -> None:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with (LIVE_DIR / f"{name}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def render_report(name: str, plan: OrderPlan, *, mode: str, session_date: str | None,
                  gate_note: str, kill_note: str, placed: Sequence[str]) -> str:
    L = [
        f"# 전략 실계좌 브릿지 — `{name}`",
        "",
        f"- Generated: `{_now_iso()}`  |  Mode: **{mode}**  |  US session: `{session_date or 'n/a'}`",
        f"- 총자산(보유+현금): ${plan.total_equity:,.2f}  |  투자가능(sleeve): ${plan.investable:,.2f}",
        f"- 안전검사: {gate_note}  |  킬스위치: {kill_note}",
        "",
        "## 목표비중",
        "",
        ("- " + ", ".join(f"`{s}`={w:.2%}" for s, w in sorted(plan.target.items())) if plan.target
         else "- (100% 현금)"),
        "",
        "## 매도 (먼저, 단건)",
        "",
    ]
    if plan.sells:
        L += ["| 종목 | 수량 | 예상단가 | 예상대금 | 전량 |", "|---|---:|---:|---:|:--:|"]
        for s in plan.sells:
            L.append(f"| `{s.symbol}` | {s.qty} | ${s.est_price:,.2f} | ${s.est_proceeds:,.2f} | "
                     f"{'✅' if s.full_exit else ''} |")
    else:
        L.append("- (없음)")
    L += ["", "## 매수 (나중, ≤$10 분할)", ""]
    if plan.buys:
        L += ["| 종목 | 금액 | 청크 |", "|---|---:|---|"]
        for b in plan.buys:
            L.append(f"| `{b.symbol}` | ${b.usd:,.2f} | {'×'.join(f'${c:g}' for c in b.chunks)} |")
        L.append(f"| **합계** | **${plan.buy_total:,.2f}** | |")
    else:
        L.append("- (없음)")
    if plan.notes:
        L += ["", "## 참고", ""] + [f"- {n}" for n in plan.notes]
    if mode == "execute":
        L += ["", "## 접수", "", ("- " + ", ".join(f"`{p}`" for p in placed)) if placed
              else "- (접수 없음)"]
    L += ["", "> ⚠️ 실주문은 --execute + TRADING_MODE=live + 정규장 접수시간창 + 안전검사 통과에서만.", ""]
    return "\n".join(L)


# ─────────────────────────────────────────── 실행(매도 먼저 → 매수)
def execute_plan(client, plan: OrderPlan, session_date: str, st: dict[str, Any], log, *,
                 split: bool, buying_power: float, name: str) -> bool:
    """플랜을 접수한다: **매도(단건) 먼저 → 매수(분할) 나중**. 세션 상태로 멱등(재실행 시 중복 없음).

    - 매도: 이번 세션에 이미 접수한 종목(st['sells_done'])은 스킵. cid=strat-sell-{date}-{sym}(서버 dedup).
    - 매수: live_exec.execute_buys 재사용(≤$10 분할·BP캡·청크 재개·주문한도). cid_prefix="strat".
    반환: 전부 성공/스킵이면 True.
    """
    sells_done: dict = st["sells_done"]
    ok = True
    for so in plan.sells:
        if so.symbol in sells_done:
            log(f"  ⏭ {so.symbol} 매도 이미 접수(이번 세션) → 건너뜀")
            continue
        cid = live_exec.client_order_id(session_date, so.symbol, prefix=SELL_CID_PREFIX)
        try:
            resp = client.create_order(so.symbol, "SELL", order_type="MARKET",
                                       quantity=so.qty, client_order_id=cid)
            oid = resp.get("orderId") if isinstance(resp, dict) else None
            sells_done[so.symbol] = cid
            save_live_state(name, st)
            log(f"  ✅ {so.symbol} {so.qty}주 매도 접수: orderId={oid} (cid={cid})")
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"  ❌ {so.symbol} 매도 실패: {e}")
            msg = map_order_error(e)
            if msg:
                log("  " + msg)
            return False        # 매도 실패 시 매수로 진행하지 않음(안전)

    # 매수 — live_exec 하드닝 경로 재사용.
    buy_plan = [(b.symbol, b.usd) for b in plan.buys]
    done = set(st.get("done_symbols", []))
    # 이미 완료한 종목은 skip 에 넣어 재실행 시 중복 접수를 막는다(run_dca 규약: skip |= done).
    # 부분 접수(chunks_done 有, done 無) 종목은 skip 밖 → execute_buys 가 청크 번호를 이어 재개.
    skip = set(done)
    chunks_done = {s: list(v) for s, v in st.get("chunks_done", {}).items()}

    def _record(sd, dn, cd):
        st["done_symbols"] = sorted(dn)
        st["chunks_done"] = {s: list(v) for s, v in cd.items()}
        save_live_state(name, st)

    def _on_error(sym, e):
        msg = map_order_error(e)
        if msg:
            log("  " + msg)

    buys_ok = live_exec.execute_buys(
        client, buy_plan, session_date, skip=skip, done=done, log=log,
        split=split, chunks_done=chunks_done, remaining_bp=buying_power,
        record_progress=_record, on_error=_on_error, cid_prefix=BUY_CID_PREFIX)
    return ok and buys_ok


# ─────────────────────────────────────────── 전략 로드(레지스트리 + 캐시 유니버스)
def load_strategy(name: str):
    """paperlab 레지스트리에서 전략 인스턴스를 로드(러너와 동일한 캐시 유니버스 주입)."""
    import paperlab_run as plr
    swing_uni = plr._cached_universe()
    from toss_trader.paperlab_strategies._universes import NDX_100
    mom_uni = [s for s in NDX_100 if plr._cache_file(s).exists()]
    hibeta_uni = plr._hibeta_cached()
    roster = plr._build(swing_uni, mom_uni, hibeta_uni)
    for strat in roster:
        if strat.name == name:
            return strat, plr
    return None, plr


# ─────────────────────────────────────────── 오케스트레이터
def run(args: argparse.Namespace) -> int:
    name = args.strategy
    log = print
    strat, plr = load_strategy(name)
    if strat is None:
        from toss_trader.paperlab_strategies import ROSTER_FACTORIES
        log(f"❌ 전략 '{name}' 없음. 가능: {', '.join(sorted(ROSTER_FACTORIES))}")
        return 2
    if getattr(strat, "overnight", False):
        log(f"❌ '{name}' 은 오버나이트(종가매수→익일시가매도) 전략이라 목표비중 리밸런스 브릿지 대상이 아닙니다.")
        return 2

    # 목표비중(페이퍼 랩과 동일 코드) — 캐시 히스토리로 산출.
    needed = sorted(set(strat.universe()) | {"QQQ"})
    panel = plr._load_panel(needed, offline=args.offline)
    target = compute_target_weights(strat, panel, paper_strategy_state(name))
    log(f"전략 `{name}` | 목표비중: "
        + (", ".join(f"{s}={w:.2%}" for s, w in sorted(target.items())) if target else "100% 현금"))

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

    # 실주문이면 시간창·세션완료 가드를 매수가능 조회보다 먼저 확인.
    session_date = None
    reg = None
    if args.execute:
        cal = client.get_market_calendar("US")
        today = cal.get("today", {}) or {}
        reg = today.get("regularMarket")
        session_date = today.get("date")
        if not reg:
            log(f"⏸ 미국 정규장 아님(금액주문 불가) → 종료. (US date={session_date})")
            return 0
        ok_win, why = live_exec.order_window_status(reg)
        if not ok_win:
            log(f"⏸ 미국 금액주문 접수 시간 아님 → 종료. {why}")
            return 0
        pre = load_live_state(name)
        if pre.get("session_date") == session_date and pre.get("complete"):
            log(f"✅ 이미 이번 세션({session_date}) 실행 완료 → 중복 방지, 종료.")
            return 0

    # 계좌 스냅샷.
    krw = float(client.get_buying_power("KRW").get("cashBuyingPower", 0) or 0)
    fx = float(client.get_exchange_rate("USD", "KRW").get("rate", 0) or 0)
    usd_bp = float(client.get_buying_power("USD").get("cashBuyingPower", 0) or 0)
    avail_usd = usd_bp if usd_bp > 0 else (krw / fx if fx else 0.0)
    holdings_raw = client.get_holdings()
    items = holdings_raw.get("items", []) if isinstance(holdings_raw, dict) else (holdings_raw or [])
    holdings: dict[str, dict[str, float]] = {}
    for it in items:
        if str(it.get("marketCountry", "US")).upper() != "US":
            continue
        sym = it.get("symbol")
        qty = float(it.get("quantity", 0) or 0)
        px = float(it.get("lastPrice", 0) or it.get("averagePurchasePrice", 0) or 0)
        if sym and qty > 0:
            holdings[sym] = {"qty": qty, "price": px}
    held_total = sum(h["qty"] * h["price"] for h in holdings.values())
    log(f"계좌 {client.s.account_seq} | 매수가능 ${avail_usd:,.2f} | 보유평가 ${held_total:,.2f} "
        f"| sleeve={args.sleeve_frac:g} | max-usd={args.max_usd if args.max_usd is not None else '∞'}")

    plan = build_order_plan(target, holdings, avail_usd, sleeve_frac=args.sleeve_frac,
                            max_usd=args.max_usd, split=args.split_small_orders)
    log("매도: " + (", ".join(f"{o.symbol} {o.qty}" for o in plan.sells) if plan.sells else "없음"))
    log("매수: " + (", ".join(f"{b.symbol} ${b.usd:.2f}" for b in plan.buys) if plan.buys else "없음"))

    # ── 안전검사(항상 계산·기록; 실주문일 때만 차단) ──────────────────────
    buy_syms = [b.symbol for b in plan.buys]
    gate_ok, offending, factors = leverage_gate(client, buy_syms,
                                                 allow_leveraged=args.allow_leveraged_etp, log=log)
    gate_note = "통과" if gate_ok else f"거부(레버리지 {','.join(offending)})"

    p_dd = paper_drawdown(name)
    prev = load_live_state(name)
    snap = float(prev.get("snapshot_equity", 0) or 0) if prev.get("session_date") == session_date else 0.0
    cur_equity = avail_usd + held_total
    intraday_pnl = ((cur_equity - snap) / snap) if snap > 0 else None
    kill_ok, kill_reasons = kill_switch_check(
        p_dd, intraday_pnl, max_paper_dd=args.max_paper_dd,
        max_intraday_loss=args.max_intraday_loss)
    kill_note = "통과" if kill_ok else "차단(" + "; ".join(kill_reasons) + ")"
    log(f"안전검사: 레버리지 {gate_note} | 킬스위치 {kill_note} "
        f"(paper_dd={p_dd if p_dd is None else round(p_dd, 4)}, "
        f"intraday={intraday_pnl if intraday_pnl is None else round(intraday_pnl, 4)})")

    placed: list[str] = []
    mode = "execute" if args.execute else "dry-run"

    def _finish(rc: int) -> int:
        REPORTS.mkdir(parents=True, exist_ok=True)
        REPORT_LATEST.write_text(
            render_report(name, plan, mode=mode, session_date=session_date,
                          gate_note=gate_note, kill_note=kill_note, placed=placed),
            encoding="utf-8")
        log_jsonl(name, {
            "ts": _now_iso(), "mode": mode, "session_date": session_date, "target": target,
            "total_equity": plan.total_equity, "investable": plan.investable,
            "sells": [{"symbol": o.symbol, "qty": o.qty, "full_exit": o.full_exit} for o in plan.sells],
            "buys": [{"symbol": b.symbol, "usd": b.usd, "chunks": b.chunks} for b in plan.buys],
            "leverage_gate": {"ok": gate_ok, "offending": offending, "factors": factors},
            "kill_switch": {"ok": kill_ok, "reasons": kill_reasons,
                            "paper_dd": p_dd, "intraday_pnl": intraday_pnl},
            "placed": placed, "rc": rc,
        })
        log(f"→ 리포트 {REPORT_LATEST}  |  로그 {LIVE_DIR / (name + '.jsonl')}")
        return rc

    if not args.execute:
        log("ℹ️ dry-run(플랜만). 실주문은 --execute (+TRADING_MODE=live, 정규장·접수시간창).")
        return _finish(0)

    # ── 실주문 경로 ──────────────────────────────────────────────────────
    if not client.s.is_live:
        log("❌ --execute에는 TRADING_MODE=live 필요. paper라 주문 안 함.")
        return _finish(1)
    if not gate_ok:
        log("⛔ 레버리지 ETP 게이트 거부 → 주문 안 함. (--allow-leveraged-etp 필요)")
        return _finish(1)
    if not kill_ok:
        log("⛔ 킬스위치 발동 → 주문 안 함.")
        return _finish(1)

    # FX 프리플라이트(KRW 자동환전 경고/보류).
    proceed, _short = live_exec.fx_window_preflight(client, plan.buy_total, fx, log,
                                                    require_usd=args.require_usd)
    if args.require_usd and not proceed:
        return _finish(0)   # 세션 미완료로 남겨 다음 트리거/우대창에서 재개

    st = _session_state(name, session_date)
    if not st.get("snapshot_equity"):
        st["snapshot_equity"] = round(cur_equity, 2)   # 세션 최초: 당일 손익 기준선
    # 서버 OPEN 매수주문 기준 세션 스킵(best-effort)은 execute_buys 내부 skip=set()로 두고
    # 멱등은 결정론적 cid + 로컬 상태(chunks_done/sells_done)로 보장.
    ok = execute_plan(client, plan, session_date, st, log, split=args.split_small_orders,
                      buying_power=avail_usd, name=name)
    if ok:
        remaining = [b.symbol for b in plan.buys if b.symbol not in set(st.get("done_symbols", []))]
        remaining += [o.symbol for o in plan.sells if o.symbol not in st.get("sells_done", {})]
        if not remaining:
            st["complete"] = True
    save_live_state(name, st)
    return _finish(0 if ok else 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="페이퍼 전략 1개를 실계좌 주문으로 실행(기본 dry-run, 절대 자동 활성화 안 됨).")
    ap.add_argument("--strategy", required=True, help="paperlab 레지스트리의 전략 이름(예: ftlt_1x)")
    ap.add_argument("--execute", action="store_true",
                    help="실주문 실행(정규장 접수시간창 + TRADING_MODE=live + 안전검사 통과 필요)")
    ap.add_argument("--max-usd", type=float, default=None, help="이번 실행 총 매수 노셔널 상한(USD)")
    ap.add_argument("--sleeve-frac", type=float, default=1.0,
                    help="전략이 운용할 계좌 비율(0~1, 기본 1.0). 투자가능=frac×(보유+현금).")
    ap.add_argument("--allow-leveraged-etp", action="store_true",
                    help="레버리지/인버스 ETP 매수 허용(규제 예탁금·교육 요건 충족 가정, 본인 책임).")
    ap.add_argument("--max-paper-dd", type=float, default=DEFAULT_MAX_PAPER_DD,
                    help="페이퍼 장부 낙폭 킬스위치(0.35=−35%%). 0 이하면 끔.")
    ap.add_argument("--max-intraday-loss", type=float, default=DEFAULT_MAX_INTRADAY_LOSS,
                    help="계좌 당일 손익 킬스위치(0.10=−10%%). 0 이하면 끔.")
    ap.add_argument("--split-small-orders", action=argparse.BooleanOptionalAction, default=True,
                    help="매수를 ≤$10 무료 청크로 분할(기본 ON). 정책 리스크 시 --no-split-small-orders.")
    ap.add_argument("--require-usd", action="store_true",
                    help="USD 부족으로 KRW 야간 자동환전이 필요하고 우대창 밖이면 매수 보류.")
    ap.add_argument("--offline", action="store_true", help="네트워크 금지(캐시만) — 목표비중 산출용.")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
