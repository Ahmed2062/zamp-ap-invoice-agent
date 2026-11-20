"""Runtime configuration (environment variables only - no secrets in code)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _load_dotenv() -> None:
    """Minimal .env loader for local development (does not override real env vars)."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    reference_dir: Path
    samples_dir: Path
    gemini_api_key: str | None
    gemini_model: str
    ai_mode: str
    stage_delay_ms: int
    max_upload_mb: int
    admin_token: str | None

    @property
    def ai_enabled(self) -> bool:
        return self.ai_mode != "off" and bool(self.gemini_api_key)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ap_agent.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(_env("DATA_DIR", str(ROOT / "data" / "runtime"))),
        reference_dir=Path(_env("REFERENCE_DIR", str(ROOT / "data" / "reference"))),
        samples_dir=Path(_env("SAMPLES_DIR", str(ROOT / "samples"))),
        gemini_api_key=_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"),
        gemini_model=_env("GEMINI_MODEL", "gemini-3.8-flash"),
        ai_mode=(_env("AI_MODE", "auto") or "auto").lower(),
        stage_delay_ms=int(_env("STAGE_DELAY_MS", "650")),
        max_upload_mb=int(_env("MAX_UPLOAD_MB", "10")),
        admin_token=_env("ADMIN_TOKEN"),
    )


settings = load_settings()
