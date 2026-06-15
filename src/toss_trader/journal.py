"""매매일지/에러로그 자동화.

- 사람이 읽는 일지: journal/YYYY-MM-DD.md (날짜별 append)
- 기계가 읽는 로그: data/runs.jsonl, data/errors.jsonl (JSON Lines)
'오늘보다 나은 내일'을 위해 시그널·체결·차단·에러를 모두 남긴다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass
class DailyReport:
    day: str
    equity_start: float = 0.0
    equity_end: float = 0.0
    weights: dict[str, float] = field(default_factory=dict)
    exits: list[tuple[str, str]] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def day_return(self) -> float:
        return (self.equity_end / self.equity_start - 1.0) if self.equity_start > 0 else 0.0


class JournalWriter:
    def __init__(self, journal_dir: str = "journal", data_dir: str = "data") -> None:
        self.journal_dir = Path(journal_dir)
        self.data_dir = Path(data_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def write_run(self, report: DailyReport) -> None:
        self._append_jsonl(self.data_dir / "runs.jsonl", {
            "ts": self._now(), **asdict(report),
        })
        self._append_md(report)

    def write_error(self, day: str, context: str, message: str) -> None:
        self._append_jsonl(self.data_dir / "errors.jsonl", {
            "ts": self._now(), "day": day, "context": context, "message": message,
        })
        path = self.journal_dir / f"{day}.md"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n> ⚠️ ERROR [{context}] {message}\n")

    # --- 내부 ---
    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _append_jsonl(path: Path, obj: dict) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")

    def _append_md(self, r: DailyReport) -> None:
        path = self.journal_dir / f"{r.day}.md"
        new = not path.exists()
        lines: list[str] = []
        if new:
            lines.append(f"# 매매 기록 — {r.day}\n")
        lines.append(f"\n## 실행 {self._now()}")
        lines.append(f"- 자본: ${r.equity_start:,.2f} → ${r.equity_end:,.2f} "
                     f"({r.day_return*100:+.2f}%)")
        if r.halted:
            lines.append(f"- ⛔ 신규진입 차단: {r.halt_reason}")
        if r.weights:
            w = ", ".join(f"{s} {v*100:.0f}%" for s, v in r.weights.items())
            lines.append(f"- 목표비중: {w}")
        else:
            lines.append("- 목표비중: 전량 현금")
        for sym, reason in r.exits:
            lines.append(f"- 🔻 보호청산 {sym}: {reason}")
        for fl in r.fills:
            lines.append(f"- {'🟢' if fl['side']=='BUY' else '🔴'} {fl['side']} "
                         f"{fl['symbol']} {fl['quantity']:.4f}@${fl['price']:.2f} "
                         f"(비용 ${fl['cost']:.3f})")
        if not r.fills and not r.exits:
            lines.append("- 매매 없음")
        for e in r.errors:
            lines.append(f"- ⚠️ {e}")
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
