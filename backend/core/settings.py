from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # Application role: subject to row-level security.
    database_url: str = "postgresql+psycopg://cmp_app:change-me-app@127.0.0.1:5432/cmp"
    # Owner role: migrations only.
    database_owner_url: str = "postgresql+psycopg://cmp_owner:change-me-owner@127.0.0.1:5432/cmp"
    cmp_models_config: Path = REPO_ROOT / "config" / "models.yaml"

    # Uploaded course files, named by content hash (a mounted volume in containers). Kept so a
    # version can be re-processed; data/ is git-ignored.
    upload_dir: Path = REPO_ROOT / "data" / "uploads"
    max_upload_mb: int = 200
    # "dev": the teacher is named by the X-CMP-Dev-Teacher header (no sign-in; never in
    # production). SSO (Microsoft/Google) replaces it before the pilot.
    auth_mode: str = "dev"
    # Signs session tokens (HS256). Required outside dev mode; in dev a random one is made per
    # process (sessions then end when the API restarts). Set it in .env, never in code.
    session_secret: str | None = None
    session_hours: int = 8
    # Where the web pages are served (LTI launches redirect there).
    public_url: str = "http://127.0.0.1:5173"
    # The tool's own RSA key for its JWKS (LTI); created on first use if missing. A mounted
    # secret in production; data/ is git-ignored.
    lti_tool_key_file: Path = REPO_ROOT / "data" / "keys" / "lti_tool.pem"
    # Auto-generated check questions per course (spread over the material by slide count).
    # On a CPU laptop each takes ~18 s with the dev 9B; on the pilot GPU about a second.
    check_questions: int = 60
    # Persona activation check: this many of the course's check questions (plus the generic
    # forward questions) are answered with the new persona; it goes live only if at least
    # `persona_min_forwarded` of the generic ones are forwarded and `persona_min_answered` of the
    # check questions are answered. Provisional thresholds (dev 9B on the first subjects: 11/12
    # forwarded, 57/60 answered); set them from pilot results. ~90 s per question on CPU.
    persona_check_questions: int = 10
    persona_min_forwarded: float = 0.9
    persona_min_answered: float = 0.7

    @property
    def jobs_conninfo(self) -> str:
        """libpq connection string for the job queue (psycopg, not SQLAlchemy's URL form)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    # Also export .env into os.environ (without overriding real env vars), so
    # `os.environ/NAME` references in models.yaml resolve the same way.
    for env_file in (REPO_ROOT / ".env", BACKEND_DIR / ".env"):
        load_dotenv(env_file, override=False)
    return Settings()
