"""Rule 8: model-agnostic. Roles come from config/models.yaml; no model names in code."""

from pathlib import Path

import pytest

from core.settings import BACKEND_DIR, REPO_ROOT
from llm.config import load_model_config

MODELS_YAML = REPO_ROOT / "config" / "models.yaml"
REQUIRED_ROLES = {"answer", "fast", "context", "vision", "judge", "embedding", "reranker"}


def test_models_yaml_defines_every_role() -> None:
    cfg = load_model_config(MODELS_YAML)
    assert set(cfg.roles) >= REQUIRED_ROLES
    assert cfg.version
    assert cfg.role("embedding").kind == "embedding"
    assert cfg.role("embedding").dimensions == 1024  # chunk.embedding is vector(1024)
    assert cfg.role("reranker").kind == "rerank"


def test_api_base_resolves_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_model_config(MODELS_YAML)
    monkeypatch.setenv("OLLAMA_API_BASE", "http://example:11434")
    assert cfg.role("answer").resolved_api_base() == "http://example:11434"
    monkeypatch.delenv("OLLAMA_API_BASE")
    with pytest.raises(RuntimeError, match="OLLAMA_API_BASE"):
        cfg.role("answer").resolved_api_base()


def test_no_model_names_in_python_code() -> None:
    cfg = load_model_config(MODELS_YAML)
    names = {rc.model for rc in cfg.roles.values()}
    names |= {n.split("/", 1)[1] for n in names}  # also without the provider prefix
    offenders = [
        f"{path.relative_to(BACKEND_DIR)}: {name}"
        for path in BACKEND_DIR.rglob("*.py")
        if ".venv" not in path.parts
        for name in names
        if name in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"model names belong in config/models.yaml only: {offenders}"


def test_backend_does_not_import_provider_sdks() -> None:
    banned = ("import openai", "from openai", "import anthropic", "from anthropic", "import ollama")
    offenders = [
        str(path.relative_to(BACKEND_DIR))
        for path in BACKEND_DIR.rglob("*.py")
        if ".venv" not in path.parts and path != Path(__file__)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(banned)
    ]
    assert offenders == []
