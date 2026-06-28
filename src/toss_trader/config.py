"""설정 로딩. 외부 패키지 없이 .env를 직접 파싱한다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://openapi.tossinvest.com"


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """.env 파일을 읽어 os.environ에 주입(기존 환경변수는 덮어쓰지 않음)."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    account_seq: str          # X-Tossinvest-Account (계좌/주문에 필요)
    base_url: str
    trading_mode: str         # "paper" | "live"
    gemini_api_key: str       # 비어있으면 LLM 보조 비활성

    @property
    def is_live(self) -> bool:
        return self.trading_mode.lower() == "live"

    @property
    def has_account(self) -> bool:
        return bool(self.account_seq)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_key)

    def require_credentials(self) -> None:
        """시세 조회에도 필요한 최소 자격증명 검증."""
        missing = [n for n, v in (("API_KEY(client_id)", self.client_id),
                                  ("SECRET_KEY(client_secret)", self.client_secret)) if not v]
        if missing:
            raise RuntimeError(
                f".env에 {', '.join(missing)} 가 필요합니다. .env.example을 참고하세요."
            )

    def require_account(self) -> None:
        """계좌/주문 호출 전 검증."""
        self.require_credentials()
        if not self.account_seq:
            raise RuntimeError(
                "계좌/주문 호출에는 TOSS_ACCOUNT_SEQ가 필요합니다. "
                "client.get_accounts()로 조회한 값을 .env에 넣으세요."
            )


def get_settings(env_path: str | os.PathLike = ".env") -> Settings:
    load_dotenv(env_path)
    mode = os.environ.get("TRADING_MODE", "paper").strip().lower()
    if mode not in ("paper", "live"):
        raise RuntimeError(f"TRADING_MODE는 paper 또는 live여야 합니다 (현재: {mode!r}).")
    # 자격증명 환경변수 이름은 두 관례를 모두 지원한다:
    #   - 토스 접두 표준: TOSS_CLIENT_ID / TOSS_CLIENT_SECRET / TOSS_ACCOUNT_SEQ
    #   - 일반 표기(.env에 이렇게 저장됨): API_KEY / SECRET_KEY / ACCOUNT_SEQ
    def _env(*names: str, default: str = "") -> str:
        for n in names:
            v = os.environ.get(n)
            if v is not None and v.strip():
                return v.strip()
        return default

    return Settings(
        client_id=_env("TOSS_CLIENT_ID", "API_KEY"),
        client_secret=_env("TOSS_CLIENT_SECRET", "SECRET_KEY"),
        account_seq=_env("TOSS_ACCOUNT_SEQ", "ACCOUNT_SEQ"),
        base_url=_env("TOSS_BASE_URL", default=DEFAULT_BASE_URL).rstrip("/"),
        trading_mode=mode,
        gemini_api_key=_env("GEMINI_API_KEY"),
    )
