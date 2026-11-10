"""Token counting with the embedding model's own tokenizer (named in models.yaml)."""

from functools import lru_cache

from llm.config import ModelConfig, get_model_config


@lru_cache
def _tokenizer(name: str):
    from transformers import AutoTokenizer  # heavy import; installed with the ingest group

    return AutoTokenizer.from_pretrained(name)


def embedding_token_counter(config: ModelConfig | None = None):
    rc = (config or get_model_config()).role("embedding")
    if not rc.tokenizer:
        raise RuntimeError("models.yaml: the embedding role needs a `tokenizer` entry")
    tok = _tokenizer(rc.tokenizer)
    return lambda text: len(tok.encode(text, add_special_tokens=False))
