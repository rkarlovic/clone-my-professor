"""BGE-M3 sparse (lexical) weights for hybrid search — the `sparse` role.

EXPERIMENT and a narrow, documented exception to rule 8: no inference server we run returns
sparse vectors and LiteLLM has no sparse type, so this module loads the model named in
models.yaml in-process. Nothing outside `llm/` may load models; callers only get
{token_id: weight} dicts. Keep it only if the golden set shows a gain, then move the model
behind a server the model layer can call.

The weights follow the BGE-M3 reference: ReLU of the model's sparse head over the last hidden
states, the maximum weight per token id, special tokens dropped.
"""

from functools import lru_cache

from llm.config import ModelConfig, get_model_config

SparseVector = dict[int, float]


@lru_cache(maxsize=1)
def _load(model_id: str):
    import torch  # heavy; installed with the ingest dependency group
    from huggingface_hub import hf_hub_download
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModel.from_pretrained(model_id).eval()
    head = torch.nn.Linear(model.config.hidden_size, 1)
    head.load_state_dict(torch.load(hf_hub_download(model_id, "sparse_linear.pt"), "cpu"))
    special = set(tokenizer.all_special_ids)
    return torch, tokenizer, model, head.eval(), special


def sparse_embed(
    texts: list[str], config: ModelConfig | None = None, batch: int = 8
) -> list[SparseVector]:
    rc = (config or get_model_config()).role("sparse")
    if rc.kind != "sparse":
        raise ValueError(f"Role 'sparse' is {rc.kind}, not sparse")
    torch, tokenizer, model, head, special = _load(rc.model)
    max_length = int(rc.params.get("max_length", 8192))
    out: list[SparseVector] = []
    with torch.inference_mode():
        for i in range(0, len(texts), batch):
            enc = tokenizer(
                texts[i : i + batch],
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            hidden = model(**enc).last_hidden_state
            weights = torch.relu(head(hidden)).squeeze(-1)
            for ids, ws, mask in zip(enc["input_ids"], weights, enc["attention_mask"], strict=True):
                vec: SparseVector = {}
                for tid, w, m in zip(ids.tolist(), ws.tolist(), mask.tolist(), strict=True):
                    if m and w > 0 and tid not in special:
                        vec[tid] = max(vec.get(tid, 0.0), w)
                out.append(vec)
    return out
