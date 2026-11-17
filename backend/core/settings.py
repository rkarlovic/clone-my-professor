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
