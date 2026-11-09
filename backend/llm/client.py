"""The single entry point for model calls. Everything goes through LiteLLM, addressed by role.

Only the OpenAI-compatible schema is used, so any LiteLLM-supported backend can serve a role.
"""

from typing import Any

import litellm

from llm.config import ModelConfig, Role, get_model_config


def _cfg(config: ModelConfig | None) -> ModelConfig:
    return config or get_model_config()


def trace_metadata(role: Role, config: ModelConfig | None = None) -> dict[str, str]:
    """What every trace/log must carry about the model (plus kb_version / persona at call site)."""
    cfg = _cfg(config)
    return {"role": role, "model_id": cfg.role(role).model, "model_config_version": cfg.version}


def complete(
    role: Role, messages: list[dict[str, Any]], config: ModelConfig | None = None, **overrides: Any
) -> str:
    rc = _cfg(config).role(role)
    if rc.kind != "chat":
        raise ValueError(f"Role '{role}' is {rc.kind}, not chat")
    resp = litellm.completion(
        model=rc.model,
        messages=messages,
        api_base=rc.resolved_api_base(),
        **{**rc.params, **overrides},
    )
    return resp.choices[0].message.content or ""


def embed(texts: list[str], config: ModelConfig | None = None) -> list[list[float]]:
    rc = _cfg(config).role("embedding")
    resp = litellm.embedding(model=rc.model, input=texts, api_base=rc.resolved_api_base())
    vectors = [item["embedding"] for item in resp.data]
    if rc.dimensions and any(len(v) != rc.dimensions for v in vectors):
        raise ValueError(f"Embedding model returned wrong dimension (expected {rc.dimensions})")
    return vectors


def rerank(
    query: str, documents: list[str], top_n: int | None = None, config: ModelConfig | None = None
) -> list[tuple[int, float]]:
    """Return (document index, relevance score), best first."""
    rc = _cfg(config).role("reranker")
    resp = litellm.rerank(
        model=rc.model,
        query=query,
        documents=documents,
        top_n=top_n,
        api_base=rc.resolved_api_base(),
    )
    return [(r["index"], r["relevance_score"]) for r in resp.results]
