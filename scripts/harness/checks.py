"""toss-trader 연구 루프 도메인 검증 — 이 프로젝트가 **실제로 겪은** 실패만 잡는다.

각 검사(F1..F11)는 순수 함수로 `list[Finding]` 을 돌려준다. 범용 하네스 기능은
넣지 않는다(사용자 지침: 더 나은 모델이면 불필요해질 일반 검사는 만들지 않음).

과거에 실제로 난 사고만 검사한다:
  F1  수수료 가정 오류(25bp 를 주 비용으로 잘못 사용; 토스 표준은 0.1%/10bp)
  F2  결과 파일 경로 충돌(c12_results.json 두 실험이 덮어씀)
  F3  사전등록 누락/결과 뒤 배치 · 실험이 원장 미기록
  F4  홀드아웃 peek-once 위반 · 원장 append-only 파괴
  F5  룩어헤드: 신호함수에 lookahead_guard 테스트 없음
  F6  생존편향: 단일종목 실험이 PIT 없이 상한 라벨 미표기
  F7  신호 재사용인데 semi_contaminated(반오염) 미표기
  F8  규제 매매가능성: 레버리지/인버스 ETP 를 실계좌 그룹에 편성
  F9  비밀정보 추적/스테이징 · publish 허용목록에 비밀 포함
  F10 실주문 안전: plist/스크립트에 --execute/live · dry-run 기본값 훼손 · 주문경로 테스트
  F11 레버리지/타이밍 주장 감사(고점대비 낙폭·합성 2x 배당 이중계상) 미언급

stdlib 만 사용한다.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ── 데이터 모델 ──────────────────────────────────────────────────────────────
@dataclass
class Finding:
    """검사 1건의 결과. level=block 이 하나라도 있으면 verify 는 exit 2."""
    level: str      # "block" | "warn"
    code: str       # "F1".."F11"
    path: str       # 레포 상대경로(레포 전역 검사는 "")
    message: str    # 한국어 설명
    fix_hint: str   # 한국어 조치 힌트


@dataclass
class Context:
    """검사 실행 맥락. scope=None 이면 전체(--all), set 이면 그 상대경로만."""
    root: Path
    scope: set[str] | None = None
    _ledger_cache: dict = field(default_factory=dict)

    def in_scope(self, rel: str) -> bool:
        return self.scope is None or rel in self.scope

    def any_in_scope(self, rels) -> bool:
        return self.scope is None or any(r in self.scope for r in rels)

    def read(self, rel: str) -> str | None:
        p = self.root / rel
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def git(self, *args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                                 text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout


# ── 상수 ─────────────────────────────────────────────────────────────────────
LEDGER = "reports/trials_ledger.jsonl"
EXP_DIR = "experiments"
REPORTS_DIR = "reports"

# 한국 소액·무이력 리테일이 첫 거래로 못 사는 레버리지/인버스-레버리지 ETP.
# (F8: 이들을 '보유'하는 전략은 실계좌 그룹 금지.) 1x 인버스(PSQ/SH/VIXY)는 허용.
LEVERAGED_INVERSE_ETP = frozenset({
    "TQQQ", "SQQQ", "QLD", "SOXL", "SOXS", "TECL", "TECS", "SPXL", "SPXU",
    "UPRO", "SDOW", "UDOW", "TMF", "TMV", "UVXY", "SVXY", "SVIX", "VXX",
    "FAS", "FAZ", "HIBL", "HIBS", "FNGU", "FNGD", "TNA", "TZA", "LABU", "LABD",
    "NAIL", "DPST", "BOIL", "KOLD", "UCO", "SCO", "YINN", "YANG", "CONL",
    "MSTU", "MSTZ", "NVDL", "TSLL", "CURE", "DRN", "SSO",
})
# 1x 단일 인버스/변동성(예탁금 규제 밖) — F8 에서 절대 잡지 않는다.
ALLOWED_1X = frozenset({"PSQ", "SH", "VIXY", "DOG", "RWM", "EUM", "YXI", "SBB"})

# F6: 단일종목 실험 판별용 대표 개별주(리스트 유니버스에 3+ 등장 시 단일종목 매매로 본다).
SINGLE_STOCKS = frozenset({
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AMD",
    "AVGO", "MU", "MRVL", "NFLX", "CRM", "ADBE", "COST", "PEP", "INTC", "QCOM",
    "TXN", "AMAT", "LRCX", "PANW", "SMCI", "MSTR", "COIN", "PLTR", "CRWD",
})

# F1: 비용 스트레스/민감도/what-if 라벨(이 맥락의 25bp 는 정당).
_STRESS_TOKENS = ("stress", "스트레스", "what-if", "whatif", "promo", "프로모",
                  "std_", "sensitivity", "민감도", "mult", "2x", "×2", "2×")

# F7: 신호 재사용 표기 토큰(원장/실험/리포트 중 하나라도 있으면 표기된 것으로 본다).
_REUSE_MARKERS = ("semi_contaminated", "signal_reuse", "반오염", "신호 재사용",
                  "신호재사용", "holdout_semi_contaminated", "semi-contaminated")
# F7: 재사용 신호로 볼 idea_id **접미사**(부록 v2.1 §4). 이미 홀드아웃을 본 신호를 재비용/재실행한
# 판(c4b 의 _fee10/_micro)이 명확한 사례다. '_hibeta' 는 전략 계열명으로도 쓰여 substring 오탐이 커
# 접미사에서 제외한다(c13a/c14a 재실행판은 params 에 이미 semi_contaminated 를 남긴다).
_REUSE_SUFFIXES = ("_fee10", "_micro")

# F10: 실주문 경로 보호 파일(변경 시 주문경로 테스트 필수).
PROTECTED_ORDER_PATH = (
    "src/toss_trader/client.py", "src/toss_trader/broker.py",
    "src/toss_trader/live_exec.py", "scripts/run_dca.py", "scripts/run_strategy.py",
)
ORDER_PATH_TESTS = ("tests/test_dca_split.py", "tests/test_client_v12.py",
                    "tests/test_strategy_live.py", "tests/test_gzip_body.py")

# F9: 비밀 파일 경로 패턴(추적/스테이징/허용목록 금지). .env.example 는 예외.
_SECRET_PATH_RE = re.compile(r"(^|/)(\.env(\.|$)|\.token_cache\.json$|token_cache|"
                             r"[^/]+\.key$|[^/]+\.pem$|secrets\.)")


def _is_secret_path(path: str) -> bool:
    if path.endswith(".env.example"):
        return False
    return bool(_SECRET_PATH_RE.search(path))


def _experiment_files(ctx: Context) -> list[str]:
    d = ctx.root / EXP_DIR
    if not d.is_dir():
        return []
    return sorted(f"{EXP_DIR}/{p.name}" for p in d.glob("c*.py"))


def _exp_id(filename: str) -> str:
    """experiments/c13a_signal_hibeta.py → 'c13a'."""
    base = Path(filename).name
    m = re.match(r"(c\d+[a-z]?)", base)
    return m.group(1) if m else base


# ── F1: 수수료 가정 오류 ──────────────────────────────────────────────────────
def check_f1_fees(ctx: Context) -> list[Finding]:
    """실험의 **주 비용**은 10bp(토스 표준) 또는 TossFeeSchedule/fee_fn 이어야 한다.

    과거 사고: 사이클 2·3 이 API 명세 '예시'의 25bp 를 주 시나리오로 잘못 사용.
    - block: 25bp(또는 10/0 이 아닌 수수료)를 쓰면서 파일 어디에도 10bp 표준/수수료모델
             근거가 전혀 없음(순수 사고 재현).
    - warn : 주 비용 기본값(def 기본값·모듈 상수)이 25bp — 10bp 를 primary 로, 25bp 는
             stress 라벨로 두라는 권고(과거 c2a/c3a/c2d 패턴).
    """
    out: list[Finding] = []
    lit_re = re.compile(r"commission_bps\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)")
    default_re = re.compile(r"commission_bps\s*[:=]\s*25(?:\.0+)?\b")
    for rel in _experiment_files(ctx):
        if not ctx.in_scope(rel):
            continue
        text = ctx.read(rel)
        if not text:
            continue
        lits = [float(x) for x in lit_re.findall(text)]
        has_25 = any(abs(v - 25.0) < 1e-9 for v in lits) or "25bp" in text
        has_10 = (any(abs(v - 10.0) < 1e-9 for v in lits) or "10bp" in text
                  or "PROMO_COMMISSION_BPS" in text or ".promo(" in text)
        has_fees = ("TossFeeSchedule" in text or "as_fee_fn" in text
                    or "fee_fn" in text)
        if has_25 and not (has_10 or has_fees):
            out.append(Finding(
                "block", "F1", rel,
                "실험이 25bp(구 API 예시)를 비용으로 쓰면서 10bp 표준/수수료모델 근거가 전혀 없음.",
                "주 비용을 commission_bps=10 또는 TossFeeSchedule/fee_fn 으로 바꾸고, "
                "25bp 는 stress/what-if 로만 병기하세요."))
            continue
        # 주 비용 기본값이 25bp 인 라인(스트레스 라벨 없음) → 경고.
        for line in text.splitlines():
            if default_re.search(line) and not any(t in line.lower() for t in _STRESS_TOKENS):
                out.append(Finding(
                    "warn", "F1", rel,
                    "주 비용 기본값이 25bp 로 설정됨 — 토스 표준은 0.1%/10bp 입니다.",
                    "기본값을 10bp 로 바꾸고 25bp 는 'stress'/'what-if' 로 라벨해 병기하세요."))
                break
    return out


# ── F2: 결과 파일 경로 충돌 ───────────────────────────────────────────────────
def check_f2_result_paths(ctx: Context) -> list[Finding]:
    """모든 experiments/ 의 RESULTS_JSON/REPORT 경로는 유일해야 한다(정적 스캔).

    과거 사고: 두 실험이 reports/c12_results.json 을 함께 써서 서로 덮어씀.
    충돌은 파일 간 관계이므로 항상 전체를 스캔한다.
    """
    assign_re = re.compile(
        r'(?:RESULTS_JSON|REPORT)\s*=\s*ROOT\s*/\s*"reports"\s*((?:/\s*"[^"]+"\s*)+)')
    seg_re = re.compile(r'"([^"]+)"')
    owners: dict[str, list[str]] = {}
    for rel in _experiment_files(ctx):
        text = ctx.read(rel)
        if not text:
            continue
        for m in assign_re.finditer(text):
            segs = seg_re.findall(m.group(1))
            path = "reports/" + "/".join(segs)
            owners.setdefault(path, [])
            if rel not in owners[path]:
                owners[path].append(rel)
    out: list[Finding] = []
    for path, files in sorted(owners.items()):
        if len(files) < 2:
            continue
        if not ctx.any_in_scope(files):
            continue
        out.append(Finding(
            "block", "F2", path,
            f"결과 파일 경로 충돌: {path} 를 여러 실험이 함께 씀 → {', '.join(files)}.",
            "실험마다 RESULTS_JSON/REPORT 경로를 고유하게(예: cXX_<주제>_results.json) 지정하세요."))
    return out


# ── F3: 사전등록 · 원장 기록 ──────────────────────────────────────────────────
_PREREG_RE = re.compile(r"사전등록|사전확정")
_RESULTS_MARK_RE = re.compile(r"<!--\s*RESULTS_BELOW\s*-->")


def check_f3_prereg(ctx: Context) -> list[Finding]:
    """리포트는 결과보다 **먼저** 사전등록 섹션을 둬야 하고, 실험은 원장에 기록해야 한다.

    - block: 실험 결과 리포트(cycle*.md, RESULTS_BELOW 또는 판정표 보유)에 사전등록이
             없거나 결과 뒤에 있음.
    - warn : 게이트 평가를 수행하는 실험이 원장(trials_ledger/gate_eval)을 참조하지 않음.
    """
    out: list[Finding] = []
    rep = ctx.root / REPORTS_DIR
    reports = sorted(rep.glob("cycle*_*.md")) if rep.is_dir() else []
    for p in reports:
        name = p.name
        rel = f"{REPORTS_DIR}/{name}"
        if not ctx.in_scope(rel):
            continue
        # 대상: 사전등록 규약을 따르는 전략 실험 리포트만. 감사/재검토(사후분석)는 제외.
        if any(k in name for k in ("audit", "recheck", "dsr")):
            continue
        if not re.match(r"cycle\d+_(c\d|composer|trend_vol|hibeta)", name):
            continue
        text = ctx.read(rel)
        if not text or "판정" not in text:
            continue                       # 판정(결과)이 없는 문서 → 대상 아님
        pm = _PREREG_RE.search(text)
        if not pm:
            out.append(Finding(
                "block", "F3", rel,
                "전략 실험 리포트에 사전등록(가설·규칙·파라미터) 섹션이 없음.",
                "결과표 앞에 '## 사전등록 (가설·규칙, 결과 보기 전)' 섹션을 추가하세요."))
            continue
        rm = _RESULTS_MARK_RE.search(text)   # 명시적 결과 경계 마커만 사용(오탐 방지).
        if rm and pm.start() > rm.start():
            out.append(Finding(
                "block", "F3", rel,
                "사전등록 섹션이 결과 마커(<!-- RESULTS_BELOW -->) 뒤에 위치함(결과 보고 등록 의심).",
                "사전등록 섹션을 RESULTS_BELOW 마커 앞으로 옮기세요."))
    # 실험 → 원장 기록(게이트 평가 수행 시).
    for rel in _experiment_files(ctx):
        if not ctx.in_scope(rel):
            continue
        text = ctx.read(rel)
        if not text:
            continue
        does_gate = ("gate_eval" in text or "run_splits" in text
                     or re.search(r"\bgate\.decide\b", text))
        logs = ("trials_ledger" in text or "ledger_append" in text
                or "append_holdout_peek" in text or "gate_eval" in text)
        if does_gate and not logs:
            out.append(Finding(
                "warn", "F3", rel,
                "게이트 평가를 수행하지만 원장(trials_ledger.jsonl) 기록 근거가 보이지 않음.",
                "모든 시도를 gate_eval/ledger_append 로 원장에 남기세요(우회한 결과는 불인정)."))
    return out


# ── F4: peek-once · append-only ──────────────────────────────────────────────
def _ledger_rows(ctx: Context) -> list[dict]:
    if "rows" in ctx._ledger_cache:
        return ctx._ledger_cache["rows"]
    rows: list[dict] = []
    text = ctx.read(LEDGER)
    if text:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    ctx._ledger_cache["rows"] = rows
    return rows


def check_f4_holdout_append(ctx: Context) -> list[Finding]:
    """홀드아웃은 idea_id 당 1회 · 원장은 append-only(레포 전역, 항상 실행).

    - block: 같은 idea_id 에 홀드아웃 행이 2개 이상(peek-once 위반).
    - block: HEAD 의 원장 기존 행이 작업본에서 변경/삭제됨(append-only 파괴).
    """
    out: list[Finding] = []
    rows = _ledger_rows(ctx)
    holdout: dict[str, int] = {}
    for r in rows:
        if r.get("period") == "holdout" or r.get("peeked_holdout") is True:
            idea = str(r.get("idea_id"))
            holdout[idea] = holdout.get(idea, 0) + 1
    for idea, n in sorted(holdout.items()):
        if n > 1:
            out.append(Finding(
                "block", "F4", LEDGER,
                f"idea_id={idea} 홀드아웃 행 {n}개 — peek-once 위반(홀드아웃은 아이디어당 1회).",
                "재평가는 새 idea_id(N++)로 등록하세요. gate.append_holdout_peek 를 우회하지 마세요."))
    # append-only: HEAD 의 라인들이 작업본의 정확한 접두여야 한다.
    head = ctx.git("show", f"HEAD:{LEDGER}")
    if head is not None:
        head_lines = head.splitlines()
        work = ctx.read(LEDGER)
        work_lines = work.splitlines() if work else []
        if work_lines[:len(head_lines)] != head_lines:
            out.append(Finding(
                "block", "F4", LEDGER,
                "원장이 append-only 가 아님 — HEAD 의 기존 행이 변경/삭제됨.",
                "원장은 오직 append 만 허용됩니다. 기존 행을 되돌리고 변경분은 새 행으로 추가하세요."))
    return out


# ── F5: 룩어헤드 가드 테스트 ──────────────────────────────────────────────────
# 신호/결정 함수 정의 판별. lookahead_guard 가 검사하는 (price history, dates)→weights 형태의
# 공개 신호함수만 대상. 선행 밑줄 private 헬퍼(_target_weights 등 내부 배분기)와 범용 *_weights 는 제외.
_SIGNAL_DEF_RE = re.compile(
    r"^def\s+(?:sig_|decide_|signal_)\w+\s*\(|^def\s+(?!_)\w+(?:_signal|_positions)\s*\(",
    re.M)


def check_f5_lookahead(ctx: Context) -> list[Finding]:
    """신호/결정 함수를 정의한 실험은 lookahead_guard 를 호출하는 테스트가 있어야 한다.

    과거 사고: 미래참조(look-ahead)로 성과가 부풀려짐. 규약: 모든 신호함수 guard 필수.
    """
    out: list[Finding] = []
    tests_dir = ctx.root / "tests"
    for rel in _experiment_files(ctx):
        if not ctx.in_scope(rel):
            continue
        text = ctx.read(rel)
        if not text:
            continue
        if not _SIGNAL_DEF_RE.search(text):
            continue                       # 신호함수를 새로 정의하지 않음 → 대상 아님
        eid = _exp_id(rel)
        guarded = False
        if tests_dir.is_dir():
            for t in tests_dir.glob(f"test_{eid}*.py"):
                tt = t.read_text(encoding="utf-8", errors="ignore")
                if "lookahead_guard" in tt:
                    guarded = True
                    break
        if not guarded:
            out.append(Finding(
                "block", "F5", rel,
                f"실험 {eid} 은 신호함수를 정의하지만 lookahead_guard 를 호출하는 "
                f"tests/test_{eid}*.py 가 없음.",
                "tests/test_{}*.py 에 research.lookahead_guard(signal_fn, closes, dates) 검증을 "
                "추가하세요(누출/무누출 양방향).".format(eid)))
    return out


# ── F6: 생존편향 ─────────────────────────────────────────────────────────────
def _traded_single_stocks(text: str) -> int:
    toks = set(re.findall(r"[A-Z]{2,5}", text))
    return len(toks & SINGLE_STOCKS)


def check_f6_survivorship(ctx: Context) -> list[Finding]:
    """단일종목을 매매하는 실험은 PIT 멤버십(c3c)을 쓰거나 결과를 '상한'으로 명시해야 한다.

    과거 사고: 현재 구성종목(생존자)만으로 백테스트 → 생존편향. 결과를 상한/UPPER BOUND
    로 라벨하거나 point-in-time 멤버십을 써야 한다. 실험 또는 그 리포트 중 하나면 충족.
    """
    out: list[Finding] = []
    for rel in _experiment_files(ctx):
        if not ctx.in_scope(rel):
            continue
        text = ctx.read(rel)
        if not text:
            continue
        # 단일종목 매매 실험 판별: 개별주 3+ 등장 + 신호/결정 함수 정의.
        if _traded_single_stocks(text) < 3 or not _SIGNAL_DEF_RE.search(text):
            continue
        eid = _exp_id(rel)
        blob = text
        rep = ctx.root / REPORTS_DIR
        if rep.is_dir():
            for p in rep.glob(f"cycle*_{eid}_*.md"):
                blob += "\n" + p.read_text(encoding="utf-8", errors="ignore")
            for p in rep.glob(f"cycle*_{eid}.md"):
                blob += "\n" + p.read_text(encoding="utf-8", errors="ignore")
        ok = (any(k in blob for k in ("상한", "UPPER BOUND", "생존편향", "point-in-time"))
              or re.search(r"\bPIT\b", blob) or "c3c" in text or "pit_" in text)
        if not ok:
            out.append(Finding(
                "warn", "F6", rel,
                f"단일종목 매매 실험 {eid} 이 PIT 멤버십도, 상한(UPPER BOUND)/생존편향 라벨도 없음.",
                "c3c PIT 멤버십을 쓰거나 리포트에 결과를 '상한(생존편향)'으로 명시하세요."))
    return out


# ── F7: 신호 재사용 반오염 표기 ───────────────────────────────────────────────
def check_f7_signal_reuse(ctx: Context) -> list[Finding]:
    """홀드아웃을 이미 본 신호를 재실행한 원장 행은 semi_contaminated(반오염)를 표기해야 한다.

    휴리스틱: idea_id 접미사(_fee10/_micro/_hibeta/_psq) → 재사용. 원장/실험/리포트 중
    한 곳이라도 반오염 표기가 있으면 충족(경고 수준).
    """
    out: list[Finding] = []
    rows = _ledger_rows(ctx)
    by_idea: dict[str, list[dict]] = {}
    for r in rows:
        by_idea.setdefault(str(r.get("idea_id")), []).append(r)
    flagged: set[str] = set()
    for idea, recs in sorted(by_idea.items()):
        if not any(idea.endswith(s) for s in _REUSE_SUFFIXES):
            continue
        marked = False
        for r in recs:
            p = r.get("params") if isinstance(r.get("params"), dict) else {}
            if r.get("semi_contaminated") or r.get("signal_reuse") \
                    or p.get("semi_contaminated") or p.get("signal_reuse"):
                marked = True
                break
        if marked:
            continue
        # 실험/리포트 예외: 해당 사이클이 반오염을 문서화했으면 충족.
        eid = _exp_id(idea)
        blob = ""
        for cand in (f"{EXP_DIR}/{eid}_", ):
            d = ctx.root / EXP_DIR
            if d.is_dir():
                for f in d.glob(f"{eid}_*.py"):
                    blob += f.read_text(encoding="utf-8", errors="ignore")
        rep = ctx.root / REPORTS_DIR
        if rep.is_dir():
            for f in rep.glob(f"cycle*_{eid}*.md"):
                blob += f.read_text(encoding="utf-8", errors="ignore")
        if any(m in blob for m in _REUSE_MARKERS):
            continue
        flagged.add(idea)
    for idea in sorted(flagged):
        out.append(Finding(
            "warn", "F7", LEDGER,
            f"원장 idea_id={idea} 는 신호 재사용(접미사)인데 semi_contaminated 표기가 없음.",
            "재사용 행은 params 에 semi_contaminated=True(또는 리포트에 '반오염')를 남기고, "
            "유의성 임계를 RC/SPA p<0.01 로 강화하세요(부록 v2.1 §4)."))
    return out


# ── F8: 규제 매매가능성(레버리지 ETP ↔ 실계좌) ────────────────────────────────
def _probe_held_symbols(strat, universe: list[str]) -> tuple[set[str], bool]:
    """전략 decide() 를 합성 캔들로 여러 시나리오 돌려 '실제 보유(체결 목표) 심볼'을 모은다.

    universe 에 레버리지 심볼이 있어도 **신호 전용**(보유는 1x/바스켓)이면 여기 안 잡힌다.
    반환: (보유심볼 집합, 정상실행 여부).
    """
    import random
    from datetime import date, timedelta
    try:
        from toss_trader.models import Candle
    except Exception:
        return set(), False

    def synth(sym: str, seed: int, drift: float, vol: float, n: int = 420):
        r = random.Random(hash((sym, seed)) & 0xFFFFFFFF)
        px = 100.0
        d = date(2022, 1, 3)
        rows = []
        for _ in range(n):
            ret = drift + vol * r.gauss(0, 1)
            o = px
            px = max(1.0, px * (1 + ret))
            hi = max(o, px) * (1 + abs(r.gauss(0, 0.003)))
            lo = min(o, px) * (1 - abs(r.gauss(0, 0.003)))
            rows.append(Candle(symbol=sym, dt=d, open=o, high=hi, low=lo,
                               close=px, volume=1e6))
            d += timedelta(days=1)
        return rows

    held: set[str] = set()
    scenarios = [(0.001, 0.015), (-0.0015, 0.03), (0.0, 0.02),
                 (0.002, 0.045), (-0.003, 0.05)]
    for seed in range(5):
        for drift, vol in scenarios:
            hist = {s: synth(s, seed, drift, vol) for s in universe}
            try:
                tgt = strat.decide(hist, {})
            except Exception:
                return held, False
            for k, v in (tgt or {}).items():
                if v and abs(v) > 1e-9:
                    held.add(k)
    return held, True


def check_f8_tradability(ctx: Context) -> list[Finding]:
    """페이퍼 랩 로스터: 레버리지/인버스-레버리지 ETP 를 '보유'하는 전략은 실계좌 그룹 금지.

    과거 사고: 레버리지 ETP 보유 전략이 실계좌(비레버리지) 그룹에 편성됨. 신호에만 레버리지
    심볼을 쓰고 보유는 1x/주식인 전략(c13a)은 정당하므로 universe 가 아니라 **실제 보유**를 본다.
    """
    strat_files = [f"src/toss_trader/paperlab_strategies/{p.name}"
                   for p in (ctx.root / "src/toss_trader/paperlab_strategies").glob("*.py")] \
        if (ctx.root / "src/toss_trader/paperlab_strategies").is_dir() else []
    if not ctx.any_in_scope(strat_files + ["src/toss_trader/paperlab.py"]):
        return []
    import sys
    src = str(ctx.root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from toss_trader.paperlab import classify_group
        from toss_trader.paperlab_strategies import build_roster
    except Exception as e:  # pragma: no cover - import 실패 방어
        return [Finding("warn", "F8", "src/toss_trader/paperlab_strategies/",
                        f"로스터 임포트 실패로 F8 검사를 건너뜀: {e!r}",
                        "PYTHONPATH=src 로 임포트 가능한지 확인하세요.")]
    out: list[Finding] = []
    try:
        roster = build_roster()
    except Exception as e:  # pragma: no cover
        return [Finding("warn", "F8", "src/toss_trader/paperlab_strategies/",
                        f"build_roster() 실패로 F8 검사를 건너뜀: {e!r}", "로스터 구성을 확인하세요.")]
    for s in roster:
        try:
            uni = list(s.universe())
        except Exception:
            continue
        grp = classify_group(uni, getattr(s, "group", None))
        if grp != "retail":
            continue                       # leverage/explore 그룹은 보유 허용
        lev_in_uni = [x for x in uni if x in LEVERAGED_INVERSE_ETP]
        if not lev_in_uni:
            continue                       # 유니버스에 레버리지 없음 → 안전
        held, ok = _probe_held_symbols(s, uni)
        lev_held = sorted(h for h in held if h in LEVERAGED_INVERSE_ETP
                          and h not in ALLOWED_1X)
        name = getattr(s, "name", type(s).__name__)
        if ok and lev_held:
            out.append(Finding(
                "block", "F8", "src/toss_trader/paperlab_strategies/",
                f"전략 '{name}' 이 레버리지/인버스-레버리지 ETP {lev_held} 를 보유하는데 "
                f"실계좌(비레버리지) 그룹에 편성됨.",
                "group='leverage' 로 두거나(예탁금 규제), 위험선호 레그를 1x/고베타 바스켓으로 "
                "치환해 보유가 1x 가 되게 하세요(c13a remap_signal 참고)."))
        elif not ok:
            out.append(Finding(
                "warn", "F8", "src/toss_trader/paperlab_strategies/",
                f"전략 '{name}' 이 유니버스에 레버리지 {lev_in_uni} 를 참조하나 decide() 합성실행이 "
                f"실패해 보유 여부를 확인 못함.",
                "신호 전용(보유 1x)인지 수동 확인하거나 decide() 가 합성 캔들에서 동작하게 하세요."))
    return out


# ── F9: 비밀정보 ─────────────────────────────────────────────────────────────
def check_f9_secrets(ctx: Context) -> list[Finding]:
    """.env / 토큰 캐시 / *.key 는 추적·스테이징 금지 · publish 허용목록에 포함 금지.

    과거 사고 방지: 비밀정보 유출. .env.example(템플릿)은 예외.
    """
    out: list[Finding] = []
    # (a) publish_records 허용목록(ALLOW)에 비밀 경로가 들어갔는가.
    pub = ctx.read("scripts/publish_records.py")
    if pub:
        m = re.search(r"ALLOW\s*=\s*\[(.*?)\]", pub, re.S)
        if m:
            for src in re.findall(r'\(\s*"([^"]+)"', m.group(1)):
                if _is_secret_path(src):
                    out.append(Finding(
                        "block", "F9", "scripts/publish_records.py",
                        f"publish 허용목록에 비밀 경로가 포함됨: {src}.",
                        "ALLOW 에서 해당 항목을 제거하세요(비밀은 구조적으로 제외되어야 함)."))
    # (b) 추적 중인 비밀 파일.
    tracked = ctx.git("ls-files")
    if tracked:
        for f in tracked.splitlines():
            if _is_secret_path(f):
                out.append(Finding(
                    "block", "F9", f, f"비밀 파일이 git 에 추적되고 있음: {f}.",
                    "git rm --cached 로 추적 해제하고 .gitignore 에 추가하세요."))
    # (c) 스테이징된 비밀 파일.
    staged = ctx.git("diff", "--cached", "--name-only")
    if staged:
        for f in staged.splitlines():
            if _is_secret_path(f):
                out.append(Finding(
                    "block", "F9", f, f"비밀 파일이 스테이징됨: {f}.",
                    "git restore --staged 로 스테이징을 해제하세요."))
    return out


# ── F10: 실주문 안전 ─────────────────────────────────────────────────────────
def _plist_program_args(root: Path, rel: str) -> tuple[list[str], dict]:
    try:
        with open(root / rel, "rb") as f:
            data = plistlib.load(f)      # 주석은 무시됨(정확)
    except Exception:
        return [], {}
    args = data.get("ProgramArguments", []) or []
    env = data.get("EnvironmentVariables", {}) or {}
    return [str(a) for a in args], env


def check_f10_real_money(ctx: Context) -> list[Finding]:
    """자동화 plist/스크립트는 --execute/live 금지 · dry-run 기본값 유지 · 주문경로 테스트.

    과거 사고: 실주문 경로 중복매수 버그. 실주문 전환은 **오직 사용자 결정**이어야 한다.
    """
    out: list[Finding] = []
    # (a) plist: ProgramArguments 에 --execute · Env 에 TRADING_MODE=live 금지(파싱, 주석 무시).
    autod = ctx.root / "automation"
    if autod.is_dir():
        for p in sorted(autod.glob("*.plist")):
            rel = f"automation/{p.name}"
            if not ctx.in_scope(rel):
                continue
            args, env = _plist_program_args(ctx.root, rel)
            if any("--execute" in a for a in args):
                out.append(Finding(
                    "block", "F10", rel,
                    "자동화 plist 의 ProgramArguments 에 --execute 가 활성 상태로 들어있음.",
                    "무인 실주문은 금지입니다. --execute 를 제거하세요(실주문은 사용자만 수동으로)."))
            if str(env.get("TRADING_MODE", "")).lower() == "live":
                out.append(Finding(
                    "block", "F10", rel,
                    "자동화 plist 의 EnvironmentVariables 에 TRADING_MODE=live 가 설정됨.",
                    "TRADING_MODE=live 를 제거하세요(자동화는 dry-run 만)."))
    # (b) 자동화 스크립트: TRADING_MODE=live 를 비주석 라인에서 무조건 설정 금지.
    scd = ctx.root / "scripts"
    if scd.is_dir():
        for p in list(scd.glob("install_*automation*.sh")) + list(scd.glob("*_cron.sh")) \
                + list(scd.glob("install_*.sh")):
            rel = f"scripts/{p.name}"
            if not ctx.in_scope(rel):
                continue
            text = ctx.read(rel) or ""
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("#"):
                    continue
                if re.search(r"TRADING_MODE\s*=\s*live", s):
                    out.append(Finding(
                        "block", "F10", rel,
                        "자동화 스크립트가 TRADING_MODE=live 를 설정함.",
                        "라이브 전환은 사용자 수동 결정으로만. 이 줄을 제거하세요."))
                    break
    # (c) run_dca/run_strategy dry-run 기본값 유지.
    for rel in ("scripts/run_dca.py", "scripts/run_strategy.py"):
        if not ctx.in_scope(rel):
            continue
        text = ctx.read(rel)
        if not text:
            continue
        m = re.search(r'add_argument\(\s*"--execute"[^)]*\)', text, re.S)
        if not m:
            out.append(Finding(
                "warn", "F10", rel, "--execute 인자 정의를 찾지 못함(dry-run 기본값 확인 필요).",
                "--execute 는 action=\"store_true\"(기본 off)여야 합니다."))
            continue
        blk = m.group(0)
        if "store_true" not in blk or re.search(r"default\s*=\s*True", blk):
            out.append(Finding(
                "block", "F10", rel,
                "--execute 가 dry-run 기본값(store_true, 기본 off)이 아님.",
                "add_argument(\"--execute\", action=\"store_true\") 로 되돌리세요."))
        if not re.search(r"is_live|TRADING_MODE", text):
            out.append(Finding(
                "warn", "F10", rel, "실주문 라이브 가드(is_live/TRADING_MODE) 근거가 안 보임.",
                "--execute 는 TRADING_MODE=live 에서만 실주문하도록 가드를 유지하세요."))
    # (d) 보호 주문경로 파일이 스코프에 변경됐으면 주문경로 테스트 통과 필수.
    if ctx.scope is not None:
        changed_protected = [f for f in PROTECTED_ORDER_PATH if f in ctx.scope]
        if changed_protected:
            existing = [t for t in ORDER_PATH_TESTS if ctx.exists(t)]
            if existing:
                ok, detail = _run_order_path_tests(ctx, existing)
                if not ok:
                    out.append(Finding(
                        "block", "F10", ", ".join(changed_protected),
                        f"주문경로 파일 변경 후 주문경로 테스트 실패: {detail}",
                        f"수정 후 `.venv/bin/python -m pytest -q {' '.join(existing)}` 통과를 확인하세요."))
    return out


def _run_order_path_tests(ctx: Context, tests: list[str]) -> tuple[bool, str]:
    py = ctx.root / ".venv/bin/python"
    exe = str(py) if py.exists() else "python3"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ctx.root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    try:
        r = subprocess.run([exe, "-m", "pytest", "-q", *tests], cwd=ctx.root,
                           capture_output=True, text=True, timeout=240, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"실행 실패 {e!r}"
    if r.returncode == 0:
        return True, "ok"
    tail = (r.stdout or "").strip().splitlines()[-3:]
    return False, " / ".join(tail) or "테스트 실패"


# ── F11: 레버리지/타이밍 주장 감사 ────────────────────────────────────────────
def check_f11_audit(ctx: Context) -> list[Finding]:
    """고점대비 낙폭·합성 2x 배당 이중계상을 쓰면서 PASS 를 주장하는 리포트는 해당 감사를 언급해야 한다.

    과거 사고: 시장ATH vs 코호트ATH, 종료일 절단, 합성 2x 배당 이중계상. 경고 수준(휴리스틱).
    """
    out: list[Finding] = []
    rep = ctx.root / REPORTS_DIR
    if not rep.is_dir():
        return out
    # 트리거는 '주장'에 한정(단순 낙폭/ATH 언급 제외 — 오탐 방지).
    ath_re = re.compile(r"고점\s*대비|ATH\s*대비|시장\s*고점|market[- ]?ATH|고점[- ]?to[- ]?trough")
    synth_re = re.compile(r"합성\s*(?:2x|3x|레버리지|2배|3배)|synthetic\s*(?:lever|2x|3x)")
    audit_re = re.compile(r"코호트|cohort|종료일|end[- ]?date|배당\s*이중|double\s*count|"
                          r"감사|audit|c7a|c6a|market[- ]?ATH")
    for p in sorted(rep.glob("cycle*_*.md")):
        rel = f"{REPORTS_DIR}/{p.name}"
        if not ctx.in_scope(rel):
            continue
        text = ctx.read(rel)
        if not text or "PASS" not in text:
            continue
        uses = ath_re.search(text) or synth_re.search(text)
        if uses and not audit_re.search(text):
            out.append(Finding(
                "warn", "F11", rel,
                "고점대비 낙폭/합성 레버리지 근거로 PASS 를 주장하지만 대응 감사(코호트ATH·종료일 "
                "절단·배당 이중계상) 언급이 없음.",
                "시장ATH vs 코호트ATH, 종료일 절단, 합성 2x 배당 이중계상 점검을 리포트에 명시하세요."))
    return out


# ── 집계 ─────────────────────────────────────────────────────────────────────
ALL_CHECKS = (
    check_f1_fees, check_f2_result_paths, check_f3_prereg, check_f4_holdout_append,
    check_f5_lookahead, check_f6_survivorship, check_f7_signal_reuse,
    check_f8_tradability, check_f9_secrets, check_f10_real_money, check_f11_audit,
)


def run_all(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for chk in ALL_CHECKS:
        try:
            findings.extend(chk(ctx))
        except Exception as e:  # pragma: no cover - 개별 검사 실패는 warn 으로 격리
            findings.append(Finding("warn", chk.__name__, "",
                                    f"검사 실행 중 예외: {e!r}", "검사 코드를 점검하세요."))
    return findings
