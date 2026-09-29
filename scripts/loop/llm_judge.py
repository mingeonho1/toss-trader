#!/usr/bin/env python3
"""LLM 판단 레이어 — 결정론 엔진 위에 GPT-6(로컬 Codex CLI) 판사를 얹는다.

결정론 엔진(``scripts/loop/daily_decision.py``)이 매일 페이퍼 장부만으로 각 전략의 상태를
결정론적으로 산출한다. 이 모듈은 그 산출을 **감사 가능한 백본**으로 두고, 그 위에 GPT-6 을
해석·판단 레이어로 얹는다. GPT-6 은 로컬 ``codex`` CLI 를 통해 **읽기 전용 샌드박스**에서만
호출되며, 이 모듈은 **어떤 주문 API 도 호출하지 않고 어떤 실행/라이브 플래그도 넘기지 않는다.**

파이프라인: ``daily_decision`` 직후 별도 격리 단계(``llm_judge``)로 돈다. 결정론 산출물
(``data/loop/decisions.jsonl``·``requests.jsonl``·``decision_state.json``)을 읽어 컨텍스트 번들을
만들고, GPT-6 에게 오늘의 판단을 구조화 JSON 으로 요청한다(``--output-schema``).

LLM 하네스(루프의 "fail → 되돌려 보냄"에 대응):
  - 도메인 검증 실패 → 위반 사유를 붙여 1~2회 재프롬프트 → 그래도 실패면 결정론으로 폴백
    (``llm_status="rejected"``).
  - codex 실패/타임아웃/미로그인 → ``llm_status="unavailable"``, 결정론 결과가 그대로 선다.

병합(보수적): 최종 판단 = 결정론 ∘ 검증된 LLM 레이어. state_override 는 **강등만** 반영하고
(백본 파일은 건드리지 않음 — 병합 뷰는 ``llm_judgments.jsonl`` 과 리포트에만 기록), LLM 요청은
``requests.jsonl`` 에 ``source="llm"`` 으로 추가한다. 모든 것(프롬프트 해시·모델·원출력·검증
오류·시도·상태)을 ``data/loop/llm_judgments.jsonl`` 에 남긴다(비밀 없음).

리포트: ``reports/decision_latest.md`` 끝에 "🤖 LLM 판단 (gpt-6-astra)" 섹션을 덧붙인다.

긴급 킥: 병합 후 오늘 자로 priority "high" 요청(규칙 또는 LLM)이 있으면, agentloop launchd
잡이 **로드돼 있을 때만** ``launchctl kickstart`` 로 에이전트 팀 루프를 한 번 깨운다(ET 날짜당
1회, ``data/loop/kick_state.json`` 에 영속).

사용:
  PYTHONPATH=src python scripts/loop/llm_judge.py            # GPT-6 호출(실제)
  PYTHONPATH=src python scripts/loop/llm_judge.py --offline  # codex 미호출(결정론 유지; CI/로컬)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for _p in (str(SRC), str(SCRIPTS), str(SCRIPTS / "loop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = ROOT / "data"
REPORTS = ROOT / "reports"
LOOP_DIR = DATA / "loop"
PIPELINE_INTEL = ROOT / "docs" / "pipeline" / "intel"
EXPECT_FILE = ROOT / "docs" / "loop" / "backtest_expectations.json"

REPORT_LATEST = REPORTS / "decision_latest.md"
DECISIONS_FILE = LOOP_DIR / "decisions.jsonl"
REQUESTS_FILE = LOOP_DIR / "requests.jsonl"
STATE_FILE = LOOP_DIR / "decision_state.json"
JUDGMENTS_FILE = LOOP_DIR / "llm_judgments.jsonl"
KICK_STATE_FILE = LOOP_DIR / "kick_state.json"

# ── 상수(도메인 검증·codex 호출) ──────────────────────────────────────────────
MODEL = "gpt-6-astra"
CODEX_BIN = "/opt/homebrew/bin/codex"       # 없으면 PATH 의 "codex" 로 폴백.
CODEX_TIMEOUT = 180.0
MAX_ATTEMPTS = 3                            # 최초 1 + 재프롬프트 최대 2.
SLEEVE_MIN, SLEEVE_MAX = 0.0, 0.5
CONF_MIN, CONF_MAX = 0.0, 1.0

AGENTLOOP_LABEL = "com.tosstrader.agentloop"

BENCHMARKS = frozenset({"qqq_bh", "tqqq_bh"})
ACTIONABLE_STATES = frozenset({"CANDIDATE", "LIVE_READY"})
# 결정 엔진(daily_decision)과 동일한 상태 우선순위(강등 판정에 사용). 동결.
STATE_PRIORITY = {"LIVE_READY": 5, "CANDIDATE": 4, "EVALUATING": 3,
                  "WARMUP": 2, "DEMOTED": 1, "RETIRED": 0}
VALID_STATES = frozenset(STATE_PRIORITY)
REQUEST_TYPES = ("audit", "scout", "investigate_divergence", "regime_note")
PRIORITIES = ("high", "medium", "low")
# 규칙(결정론) 요청 중 킥을 유발하는 고우선 종류(규칙 요청엔 priority 필드가 없다).
HIGH_PRIORITY_RULE_TYPES = frozenset({"audit", "investigate_divergence"})

# 프롬프트에 넣는 하드 제약(한국 리테일 규제; 비밀 없음).
HARD_CONSTRAINTS_KO = [
    "레버리지/인버스-레버리지 ETP 는 한국 리테일 첫 거래 불가(해외 레버리지 ETP 예탁금 ₩1,000만 "
    "+ 사전교육·승인 요건). 따라서 실계좌 추천 대상이 아니다(1x 인버스는 허용).",
    "≤$10 매수는 수수료 무료. 매도는 SEC/TAF 최소 $0.01. 환전은 주간 우대 0.05%.",
    "이 엔진은 절대 실주문을 내지 않는다. LIVE_READY 조차 '사용자 승인 필요' 표시일 뿐이다.",
    "추천/참고 대상 제외: 벤치마크(qqq_bh/tqqq_bh), 이름이 _moc 로 끝나는 전략(같은-종가 체결 가정), "
    "탐색(explore) 그룹.",
]


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


# ── 저수준 JSON/JSONL IO(멱등·원자적) ─────────────────────────────────────────
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _upsert_jsonl(path: Path, record: dict[str, Any], key: str = "session_date") -> None:
    rows = _read_jsonl(path)
    kv = str(record.get(key))
    rows = [r for r in rows if str(r.get(key)) != kv]
    rows.append(record)
    rows.sort(key=lambda r: str(r.get(key)))
    _write_jsonl(path, rows)


def _load_latest_intel(intel_dir: Path, *, max_chars: int = 4000) -> str | None:
    """docs/pipeline/intel/ 의 가장 최근 파일 텍스트(있으면; 잘라서). 없으면 None."""
    if not intel_dir.is_dir():
        return None
    files = [p for p in intel_dir.iterdir()
             if p.is_file() and p.suffix in (".md", ".json", ".txt")]
    if not files:
        return None
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        text = latest.read_text(encoding="utf-8")
    except OSError:
        return None
    return f"[{latest.name}]\n{text[:max_chars]}"


# ── 컨텍스트 번들(compact·비밀 없음) ──────────────────────────────────────────
def build_bundle(*, decisions_rows: Sequence[dict[str, Any]],
                 requests_rows: Sequence[dict[str, Any]],
                 expect: Mapping[str, Any], intel_text: str | None,
                 session_date: str) -> dict[str, Any]:
    """GPT-6 에게 줄 판단 근거 번들. 오늘 상세 + 최근 10일 요약 + 요청 + 기대치 + 정찰 + 제약."""
    today = decisions_rows[-1] if decisions_rows else {}
    prior = list(decisions_rows[:-1])[-10:]      # 오늘 제외 직전 최대 10일.
    history = [{
        "session_date": rec.get("session_date"),
        "changes": rec.get("changes", []),
        "recommendation": rec.get("recommendation"),
    } for rec in reversed(prior)]                # 최신 → 과거 순.
    return {
        "session_date": session_date,
        "today": {
            "generated": today.get("generated"),
            "strategies": today.get("strategies", {}),   # 전 전략 state/group/지표.
            "changes": today.get("changes", []),
            "recommendation": today.get("recommendation"),
            "requests": today.get("requests", []),
        },
        "history_last10": history,
        "open_requests": list(requests_rows),
        "backtest_expectations": dict(expect),
        "scout_intel": intel_text,
        "hard_constraints": HARD_CONSTRAINTS_KO,
    }


# ── 출력 스키마(--output-schema; 구조화 출력 강제) ────────────────────────────
def output_schema() -> dict[str, Any]:
    req_item = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "type": {"type": "string", "enum": list(REQUEST_TYPES)},
            "strategy": {"type": ["string", "null"]},
            "reason": {"type": "string"},
            "priority": {"type": "string", "enum": list(PRIORITIES)},
        },
        "required": ["type", "strategy", "reason", "priority"],
    }
    override_item = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "strategy": {"type": "string"},
            "to": {"type": "string", "enum": sorted(VALID_STATES)},
            "reason": {"type": "string"},
        },
        "required": ["strategy", "to", "reason"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "stance": {"type": "string", "enum": ["hold_dca", "recommend"]},
            "recommended_strategy": {"type": ["string", "null"]},
            "sleeve_frac": {"type": "number"},
            "confidence": {"type": "number"},
            "state_overrides": {"type": "array", "items": override_item},
            "rationale_ko": {"type": "string"},
            "what_changed_ko": {"type": "string"},
            "watch_items": {"type": "array", "items": {"type": "string"}},
            "requests": {"type": "array", "items": req_item},
        },
        "required": ["stance", "recommended_strategy", "sleeve_frac", "confidence",
                     "state_overrides", "rationale_ko", "what_changed_ko",
                     "watch_items", "requests"],
    }


# ── 프롬프트(한국어) ──────────────────────────────────────────────────────────
def build_prompt(bundle: Mapping[str, Any], *, prior_errors: Sequence[str] | None = None) -> str:
    L: list[str] = [
        "당신은 toss-trader 의 데일리 포트폴리오 판사(daily portfolio judge)다.",
        "결정론적 상태기계가 이미 각 전략의 상태를 산출했다. 당신은 그 위에 얹는 해석·판단 레이어다.",
        "역할: (1) 어제 대비 무엇이 바뀌었는지 해석, (2) 백테스트 기대 대비 괴리가 "
        "노이즈/수수료/레짐 중 무엇으로 보이는지 판단, (3) 아래 스키마의 구조화 JSON 산출.",
        "",
        "반드시 지켜야 할 규칙(위반 시 응답이 기각되고 재요청된다):",
        "- recommended_strategy 는 null 이거나 실계좌(retail) 그룹 전략이어야 한다. "
        "벤치마크·이름이 _moc 로 끝나는 전략·탐색(explore) 그룹은 절대 추천 불가.",
        "- 추천 전략의 결정론적 상태는 CANDIDATE 또는 LIVE_READY 여야 한다. 당신은 더 보수적일 수 "
        "있으나(추천 안 함) 결코 덜 보수적일 수 없다.",
        "- stance 가 \"recommend\" 이면 유효한 recommended_strategy 가 반드시 있어야 한다. "
        "아니면 \"hold_dca\".",
        "- sleeve_frac 는 0 이상 0.5 이하.",
        "- confidence 는 0 이상 1 이하.",
        "- state_overrides 는 오직 강등(다운그레이드)만 가능하다(예: CANDIDATE→EVALUATING, "
        "무엇이든→DEMOTED). 승격·동일 상태 금지. 우선순위: "
        "LIVE_READY>CANDIDATE>EVALUATING>WARMUP>DEMOTED>RETIRED.",
        "- 언급하는 모든 전략 이름은 번들 today.strategies 에 실제로 존재해야 한다. "
        "번들에 없는 수치를 지어내지 말라.",
        "- requests[].type 은 audit|scout|investigate_divergence|regime_note, "
        "priority 는 high|medium|low.",
        "",
        "하드 제약(한국 리테일):",
    ]
    for c in bundle.get("hard_constraints", []):
        L.append(f"- {c}")
    L.append("")
    if prior_errors:
        L.append("이전 응답이 다음 규칙을 위반했다. 반드시 교정하라:")
        for e in prior_errors:
            L.append(f"- {e}")
        L.append("")
    L += [
        "아래는 판단 근거 번들(JSON)이다:",
        "```json",
        json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
        "```",
        "",
        "출력은 제공된 JSON 스키마를 정확히 따르는 JSON 하나만. 스키마 밖 텍스트 금지. "
        "rationale_ko·what_changed_ko 는 한국어로 작성.",
    ]
    return "\n".join(L)


# ── codex 러너(읽기 전용 샌드박스; 주문/실행 경로 없음) ────────────────────────
def default_codex_runner(prompt: str, schema: Mapping[str, Any], *, model: str = MODEL,
                         timeout: float = CODEX_TIMEOUT) -> dict[str, Any]:
    """GPT-6 을 로컬 codex CLI 로 1회 호출. 항상 ``--sandbox read-only``.

    승인 우회 플래그는 절대 넘기지 않는다. 반환:
    ``{"status":"ok"|"error","raw":str|None,"error":str|None,"returncode":int|None}``.
    """
    codex = CODEX_BIN if Path(CODEX_BIN).exists() else "codex"
    with tempfile.TemporaryDirectory() as td:
        schema_path = Path(td) / "schema.json"
        out_path = Path(td) / "out.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        argv = [codex, "exec", "-m", model, "--sandbox", "read-only",
                "--skip-git-repo-check", "--ephemeral",
                "--output-schema", str(schema_path), "-o", str(out_path), prompt]
        try:
            proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "error", "raw": None,
                    "error": f"타임아웃 {timeout:.0f}s 초과", "returncode": None}
        except (OSError, subprocess.SubprocessError) as e:
            return {"status": "error", "raw": None,
                    "error": f"{type(e).__name__}: {e}", "returncode": None}
        raw = None
        try:
            if out_path.exists():
                raw = out_path.read_text(encoding="utf-8").strip()
        except OSError:
            raw = None
        if proc.returncode != 0:
            return {"status": "error", "raw": raw,
                    "error": f"rc={proc.returncode}: {(proc.stderr or '').strip()[-200:]}",
                    "returncode": proc.returncode}
        if not raw:
            return {"status": "error", "raw": None, "error": "빈 출력(codex)",
                    "returncode": proc.returncode}
        return {"status": "ok", "raw": raw, "error": None, "returncode": proc.returncode}


# ── 파싱 + 도메인 검증(= LLM 하네스) ──────────────────────────────────────────
def parse_output(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError) as e:
        return None, f"JSON 파싱 실패: {e}"
    if not isinstance(obj, dict):
        return None, "최상위가 JSON 객체가 아님"
    return obj, None


_REQUIRED_KEYS = ("stance", "recommended_strategy", "sleeve_frac", "confidence",
                  "state_overrides", "rationale_ko", "what_changed_ko",
                  "watch_items", "requests")
_TOKEN_RE = re.compile(r"`([a-z][a-z0-9_]{2,})`")
_KNOWN_WORDS = frozenset({
    "hold_dca", "live_ready", "candidate", "evaluating", "warmup", "demoted", "retired",
    "excess_qqq", "excess_tqqq", "dd_ratio", "sleeve_frac", "backtest_cagr",
    "tracking_vol_annual", "exp_annual_log_excess_vs_qqq", "backtest_mdd",
})


def _rationale_soft_flags(obj: Mapping[str, Any], strategies: Mapping[str, Any]) -> list[str]:
    """근거 텍스트의 백틱 토큰 중 전략id 형태인데 번들에 없는 것(경미 플래그; 기각 아님)."""
    text = f"{obj.get('rationale_ko', '')} {obj.get('what_changed_ko', '')}"
    flags: list[str] = []
    for tok in sorted(set(_TOKEN_RE.findall(text))):
        if tok in strategies or tok in _KNOWN_WORDS:
            continue
        if "_" in tok:                          # 전략 id 형태(예: foo_bar)만 경미 플래그.
            flags.append(f"근거에 번들에 없는 전략/지표 토큰: `{tok}`")
    return flags


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(obj: Mapping[str, Any], bundle: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(hard_errors, soft_flags). hard_errors 가 있으면 재프롬프트/최종 기각."""
    hard: list[str] = []
    soft: list[str] = []
    strategies = (bundle.get("today") or {}).get("strategies", {}) or {}

    for k in _REQUIRED_KEYS:
        if k not in obj:
            hard.append(f"필수 키 누락: {k}")
    if hard:
        return hard, soft                       # 구조가 깨졌으면 더 검증 불가.

    def state_of(name: str) -> str | None:
        return (strategies.get(name) or {}).get("state")

    def group_of(name: str) -> str | None:
        return (strategies.get(name) or {}).get("group")

    stance = obj.get("stance")
    if stance not in ("hold_dca", "recommend"):
        hard.append(f"stance 값 불가: {stance!r} (hold_dca|recommend)")

    rec = obj.get("recommended_strategy")
    rec_valid = True
    if rec is not None:
        if not isinstance(rec, str) or rec not in strategies:
            hard.append(f"recommended_strategy 가 번들에 없음: {rec!r}")
            rec_valid = False
        else:
            if rec in BENCHMARKS:
                hard.append(f"recommended_strategy 가 벤치마크임: {rec}")
                rec_valid = False
            if rec.endswith("_moc"):
                hard.append(f"recommended_strategy 가 _moc 변이임: {rec}")
                rec_valid = False
            g = group_of(rec)
            if g != "retail":
                hard.append(f"recommended_strategy 가 실계좌(retail) 그룹이 아님: {rec} (group={g})")
                rec_valid = False
            st = state_of(rec)
            if st not in ACTIONABLE_STATES:
                hard.append(f"recommended_strategy 상태가 CANDIDATE/LIVE_READY 아님: {rec} (state={st})")
                rec_valid = False
    else:
        rec_valid = False

    if stance == "recommend" and not (rec is not None and rec_valid):
        hard.append("stance=recommend 인데 유효한 recommended_strategy 가 없음")

    sf = obj.get("sleeve_frac")
    if not _is_number(sf) or not (SLEEVE_MIN <= float(sf) <= SLEEVE_MAX):
        hard.append(f"sleeve_frac 범위 밖: {sf!r} (0~0.5)")

    conf = obj.get("confidence")
    if not _is_number(conf) or not (CONF_MIN <= float(conf) <= CONF_MAX):
        hard.append(f"confidence 범위 밖: {conf!r} (0~1)")

    overrides = obj.get("state_overrides")
    if not isinstance(overrides, list):
        hard.append("state_overrides 가 배열이 아님")
    else:
        for ov in overrides:
            if not isinstance(ov, dict):
                hard.append("state_overrides 항목이 객체가 아님")
                continue
            nm, to = ov.get("strategy"), ov.get("to")
            if nm not in strategies:
                hard.append(f"state_override 전략이 번들에 없음: {nm!r}")
                continue
            if to not in VALID_STATES:
                hard.append(f"state_override 목표 상태 불가: {to!r}")
                continue
            cur = state_of(nm)
            cur_p = STATE_PRIORITY.get(cur, -1)
            if STATE_PRIORITY.get(to, 99) >= cur_p:
                hard.append(f"state_override 는 강등만 가능: {nm} {cur}→{to} (승격/동일 금지)")

    reqs = obj.get("requests")
    if not isinstance(reqs, list):
        hard.append("requests 가 배열이 아님")
    else:
        for rq in reqs:
            if not isinstance(rq, dict):
                hard.append("requests 항목이 객체가 아님")
                continue
            if rq.get("type") not in REQUEST_TYPES:
                hard.append(f"request type 불가: {rq.get('type')!r}")
            if rq.get("priority") not in PRIORITIES:
                hard.append(f"request priority 불가: {rq.get('priority')!r}")
            s = rq.get("strategy")
            if s is not None and s not in strategies:
                soft.append(f"request 가 번들에 없는 전략을 참조: {s!r}")

    soft.extend(_rationale_soft_flags(obj, strategies))
    return hard, soft


# ── 병합: LLM 요청을 requests.jsonl 에 추가(source=llm; 멱등) ──────────────────
def append_llm_requests(path: Path, session_date: str,
                        llm_requests: Sequence[dict[str, Any]]) -> None:
    """그 세션의 기존 source=llm 행을 제거 후 새 LLM 요청을 추가(멱등). 규칙 요청은 보존."""
    rows = [r for r in _read_jsonl(path)
            if not (r.get("source") == "llm" and str(r.get("session_date")) == str(session_date))]
    rows.extend(llm_requests)
    rows.sort(key=lambda r: (str(r.get("session_date")), str(r.get("source", "")),
                             str(r.get("type")), str(r.get("strategy") or "")))
    _write_jsonl(path, rows)


# ── 긴급 킥(agentloop launchd 잡; 로드돼 있을 때만·ET 날짜당 1회) ──────────────
def default_kicker(label: str) -> dict[str, str]:
    """launchctl kickstart. 잡이 로드돼 있지 않으면 no-op(에러 아님)."""
    target = f"gui/{os.getuid()}/{label}"
    try:
        chk = subprocess.run(["launchctl", "print", target],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return {"status": "error", "detail": f"print 실패: {type(e).__name__}: {e}"}
    if chk.returncode != 0:
        return {"status": "not_loaded", "detail": "agentloop 잡 미로드 — 킥 생략"}
    try:
        r = subprocess.run(["launchctl", "kickstart", target],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return {"status": "error", "detail": f"kickstart 실패: {type(e).__name__}: {e}"}
    if r.returncode != 0:
        return {"status": "error", "detail": (r.stderr or "").strip()[-150:] or f"rc={r.returncode}"}
    return {"status": "kicked", "detail": target}


def maybe_kick(session_date: str, kick_state_path: Path,
               kicker: Callable[[str], dict[str, str]], *,
               now: datetime | None = None) -> dict[str, Any]:
    st = _read_json(kick_state_path)
    if str(st.get("last_kick_date")) == str(session_date):
        return {"attempted": False, "status": "already", "detail": f"이미 {session_date} 킥 완료"}
    res = kicker(AGENTLOOP_LABEL)
    out = {"attempted": True, "status": res.get("status"), "detail": res.get("detail", "")}
    if res.get("status") == "kicked":
        _write_json(kick_state_path, {"last_kick_date": str(session_date),
                                      "kicked_at": _now_iso(now), "label": AGENTLOOP_LABEL})
    return out


def high_priority_requests(rows: Sequence[dict[str, Any]], session_date: str) -> list[dict[str, Any]]:
    """오늘 세션의 요청 중 킥 유발 대상(priority=high, 또는 priority 없는 규칙 요청 중 고우선 종류)."""
    high: list[dict[str, Any]] = []
    for r in rows:
        if str(r.get("session_date")) != str(session_date):
            continue
        pr = r.get("priority")
        if pr == "high":
            high.append(r)
        elif pr is None and r.get("type") in HIGH_PRIORITY_RULE_TYPES:
            high.append(r)
    return high


# ── 리포트 섹션(멱등: 기존 섹션 제거 후 재작성) ───────────────────────────────
LLM_SECTION_HEADER = "## 🤖 LLM 판단 (gpt-6-astra)"
_STATUS_KO = {"accepted": "채택(accepted)", "rejected": "기각(rejected)",
              "unavailable": "불가(unavailable)"}


def _strip_llm_section(text: str) -> str:
    idx = text.find("\n" + LLM_SECTION_HEADER)
    if idx != -1:
        return text[:idx].rstrip() + "\n"
    if text.startswith(LLM_SECTION_HEADER):
        return ""
    return text


def render_llm_section(j: Mapping[str, Any]) -> str:
    status = j.get("llm_status")
    L: list[str] = ["", LLM_SECTION_HEADER, "",
                    f"- 상태: **{_STATUS_KO.get(status, status)}** · 모델 `{j.get('model')}` · "
                    f"시도 {len(j.get('attempts', []))}회 · prompt_hash `{j.get('prompt_hash')}`"]
    v = j.get("validated")
    if status == "accepted" and v:
        L.append(f"- 입장(stance): **{v.get('stance')}** · 추천 전략: "
                 f"`{v.get('recommended_strategy')}` · 슬리브 "
                 f"{float(v.get('sleeve_frac', 0.0)) * 100:.1f}% · confidence {v.get('confidence')}")
        L.append(f"- 무엇이 바뀌었나: {v.get('what_changed_ko', '')}")
        L.append(f"- 판단 근거: {v.get('rationale_ko', '')}")
        wi = v.get("watch_items") or []
        if wi:
            L.append("- 관찰 항목:")
            L += [f"  - {w}" for w in wi]
        downs = j.get("downgrades") or []
        if downs:
            L.append("- 적용된 강등(다운그레이드):")
            L += [f"  - `{d['strategy']}` {d['from']}→**{d['to']}** — {d['reason']}" for d in downs]
        else:
            L.append("- 적용된 강등: 없음")
        lr = j.get("llm_requests") or []
        if lr:
            L.append("- LLM 방출 요청(requests.jsonl · source=llm):")
            L += [f"  - `{r['type']}` [{r.get('priority')}] {r.get('strategy') or ''} — "
                  f"{r.get('reason', '')}" for r in lr]
        sf = j.get("soft_flags") or []
        if sf:
            L.append("- 경미 플래그: " + "; ".join(sf))
    elif status == "rejected":
        L.append("- LLM 응답이 도메인 검증을 통과하지 못해 **기각**되었고, 결정론적 판단이 그대로 유지된다.")
        errs = j.get("validation_errors") or []
        if errs:
            L.append("- 검증 위반:")
            L += [f"  - {e}" for e in errs]
    else:  # unavailable
        L.append("- codex(GPT-6) 호출 불가(미로그인/타임아웃/오프라인 등) — "
                 "결정론적 판단이 그대로 유지된다.")
        if j.get("unavailable_reason"):
            L.append(f"  - 사유: {j.get('unavailable_reason')}")
    k = j.get("kick") or {}
    if k.get("attempted"):
        L.append(f"- 에이전트 루프 킥: {k.get('status')} ({k.get('detail', '')})")
    L.append("")
    return "\n".join(L)


def write_report_section(report_path: Path, j: Mapping[str, Any]) -> None:
    try:
        text = report_path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    text = _strip_llm_section(text)
    _write_text(report_path, text.rstrip() + "\n" + render_llm_section(j))


# ── 오케스트레이션 ────────────────────────────────────────────────────────────
def run(*, decisions_path: Path = DECISIONS_FILE, requests_path: Path = REQUESTS_FILE,
        state_path: Path = STATE_FILE, expect_path: Path = EXPECT_FILE,
        report_path: Path = REPORT_LATEST, judgments_path: Path = JUDGMENTS_FILE,
        kick_state_path: Path = KICK_STATE_FILE, intel_dir: Path = PIPELINE_INTEL,
        codex_runner: Callable[..., dict[str, Any]] = default_codex_runner,
        kicker: Callable[[str], dict[str, str]] = default_kicker,
        max_attempts: int = MAX_ATTEMPTS, offline: bool = False,
        now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    decisions_rows = _read_jsonl(decisions_path)
    if not decisions_rows:
        return {"llm_status": "unavailable", "session_date": None,
                "unavailable_reason": "결정 이력 없음(decisions.jsonl 비어있음)"}

    today = decisions_rows[-1]
    session_date = str(today.get("session_date") or "")
    requests_rows = _read_jsonl(requests_path)
    expect = _read_json(expect_path).get("strategies", {})
    intel_text = _load_latest_intel(intel_dir)
    bundle = build_bundle(decisions_rows=decisions_rows, requests_rows=requests_rows,
                          expect=expect, intel_text=intel_text, session_date=session_date)
    schema = output_schema()
    base_prompt = build_prompt(bundle)
    prompt_hash = hashlib.sha256(base_prompt.encode("utf-8")).hexdigest()[:16]

    status = "unavailable"
    unavailable_reason: str | None = None
    validated: dict[str, Any] | None = None
    validated_soft: list[str] = []
    last_hard_errors: list[str] = []
    attempts_log: list[dict[str, Any]] = []
    raw_last: str | None = None
    prior_errors: list[str] | None = None

    if offline:
        unavailable_reason = "offline 모드(codex 미호출)"
        attempts_log.append({"attempt": 0, "result": "skipped_offline"})
    else:
        for attempt in range(1, max_attempts + 1):
            prompt = build_prompt(bundle, prior_errors=prior_errors)
            res = codex_runner(prompt, schema)
            entry: dict[str, Any] = {"attempt": attempt, "codex_status": res.get("status"),
                                     "error": res.get("error")}
            if res.get("status") != "ok":
                entry["result"] = "codex_error"
                attempts_log.append(entry)
                status = "unavailable"
                unavailable_reason = res.get("error")
                raw_last = res.get("raw")
                break                            # codex 실패 → 결정론 유지(재프롬프트 안 함).
            raw_last = res.get("raw")
            obj, perr = parse_output(res["raw"])
            if obj is None:
                entry["result"] = "parse_error"
                entry["errors"] = [perr]
                attempts_log.append(entry)
                prior_errors = [perr or "파싱 실패"]
                last_hard_errors = prior_errors
                status = "rejected"
                continue
            hard, soft = validate(obj, bundle)
            entry["hard_errors"] = hard
            entry["soft_flags"] = soft
            if hard:
                entry["result"] = "invalid"
                attempts_log.append(entry)
                prior_errors = hard
                last_hard_errors = hard
                status = "rejected"
                continue
            entry["result"] = "accepted"
            attempts_log.append(entry)
            validated, validated_soft, status = obj, soft, "accepted"
            break

    # ── 병합(보수적): 강등만 반영(백본 파일 불변), LLM 요청은 requests.jsonl 에 추가 ──
    strategies = today.get("strategies", {}) or {}
    merged_states = {n: (strategies.get(n) or {}).get("state") for n in strategies}
    downgrades: list[dict[str, Any]] = []
    llm_requests: list[dict[str, Any]] = []
    if status == "accepted" and validated:
        for ov in validated.get("state_overrides") or []:
            nm, to = ov["strategy"], ov["to"]
            downgrades.append({"strategy": nm, "from": merged_states.get(nm),
                               "to": to, "reason": ov.get("reason", "")})
            merged_states[nm] = to
        for rq in validated.get("requests") or []:
            llm_requests.append({"type": rq["type"], "strategy": rq.get("strategy"),
                                 "reason": rq.get("reason", ""), "priority": rq.get("priority"),
                                 "source": "llm", "session_date": session_date})
        append_llm_requests(requests_path, session_date, llm_requests)

    # ── 긴급 킥: 오늘 자 high priority 요청(규칙+LLM)이 있으면 1회 ──
    all_rows = _read_jsonl(requests_path)
    highs = high_priority_requests(all_rows, session_date)
    kick = {"attempted": False, "status": "none", "detail": "high priority 요청 없음"}
    if highs:
        kick = maybe_kick(session_date, kick_state_path, kicker, now=now)

    # ── 기록(비밀 없음) ──
    judgment = {
        "session_date": session_date, "generated": _now_iso(now), "model": MODEL,
        "llm_status": status, "prompt_hash": prompt_hash, "attempts": attempts_log,
        "raw_output": raw_last, "validation_errors": last_hard_errors or None,
        "validated": validated, "soft_flags": validated_soft or None,
        "downgrades": downgrades, "llm_requests": llm_requests,
        "merged_states": merged_states, "kick": kick,
        "unavailable_reason": unavailable_reason, "n_high_priority": len(highs),
    }
    _upsert_jsonl(judgments_path, judgment)
    write_report_section(report_path, judgment)

    return {"llm_status": status, "session_date": session_date,
            "recommended_strategy": (validated or {}).get("recommended_strategy"),
            "n_llm_requests": len(llm_requests), "n_downgrades": len(downgrades),
            "attempts": len([a for a in attempts_log if a.get("attempt", 0) > 0]),
            "kick": kick, "report": str(report_path), "judgments": str(judgments_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="LLM 판단 레이어(결정론 위 GPT-6 판사; 읽기 전용·주문 없음).")
    ap.add_argument("--offline", action="store_true",
                    help="codex(GPT-6) 미호출 — 결정론 유지(CI/로컬 검증).")
    ap.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                    help="검증 실패 시 최대 시도 횟수(최초 1 + 재프롬프트).")
    args = ap.parse_args(argv)
    res = run(offline=args.offline, max_attempts=args.max_attempts)
    print(json.dumps(res, ensure_ascii=False))
    print(f"→ {res.get('report')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
