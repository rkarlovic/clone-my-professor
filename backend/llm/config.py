"""Load config/models.yaml and resolve roles. No model names may appear in code."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from core.settings import get_settings

Role = Literal["answer", "fast", "check", "context", "vision", "judge", "embedding", "reranker"]
_ENV_PREFIX = "os.environ/"


class RoleConfig(BaseModel):
    kind: Literal["chat", "embedding", "rerank"]
    model: str
    api_base: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    dimensions: int | None = None
    tokenizer: str | None = None  # embedding role: tokenizer used to measure chunk sizes
    # embedding / reranker roles: below this best score the clone forwards instead of answering.
    # Scores are on each model's own scale, so the floor belongs to the model, not the code.
    min_score: float | None = None

    def resolved_api_base(self) -> str | None:
        if self.api_base and self.api_base.startswith(_ENV_PREFIX):
            name = self.api_base.removeprefix(_ENV_PREFIX)
            value = os.environ.get(name)
            if not value:
                raise RuntimeError(f"models.yaml references unset environment variable {name}")
            return value
        return self.api_base


class ModelConfig(BaseModel):
    version: str
    # the model a self-hosted inference server (vLLM) must serve for this file's chat roles;
    # the setup scripts read it from here, so model names stay in config (rule 8)
    serve_model: str | None = None
    # how that server starts it (vLLM flags: text only, memory share, longest prompt); also read
    # by the setup scripts, into VLLM_ARGS
    serve_args: str | None = None
    roles: dict[Role, RoleConfig]
    prompt_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)

    def role(self, role: Role) -> RoleConfig:
        try:
            return self.roles[role]
        except KeyError:
            raise KeyError(f"Role '{role}' is not configured in models.yaml") from None


def load_model_config(path: Path) -> ModelConfig:
    get_settings()  # ensures .env is loaded for os.environ/ references
    with path.open(encoding="utf-8") as f:
        return ModelConfig.model_validate(yaml.safe_load(f))


@lru_cache
def get_model_config() -> ModelConfig:
    return load_model_config(get_settings().cmp_models_config)
