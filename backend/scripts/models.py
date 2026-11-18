"""Which models the active config uses, and download the missing ones.

    uv run python -m scripts.models            # list roles, models and whether each is present
    uv run python -m scripts.models --pull     # also download missing Ollama models
    [--config ../config/models.vllm.yaml]      # another models file (default: CMP_MODELS_CONFIG)

Ollama models (`ollama/…`, `ollama_chat/…`) are pulled through Ollama's own API. A vLLM server
serves one model, `serve_model` in the file, which it downloads from Hugging Face itself on
start (the setup scripts pass it on); this command only warns when a role asks vLLM for another
model. Infinity (reranker) downloads its model on start too. The `vision` role is skipped unless
asked for (`--vision`): nothing uses it yet.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

from core.settings import get_settings
from llm.config import ModelConfig, load_model_config

OLLAMA = ("ollama/", "ollama_chat/")
VLLM = "hosted_vllm/"


def ollama_name(model: str) -> str | None:
    for prefix in OLLAMA:
        if model.startswith(prefix):
            return model.removeprefix(prefix)
    return None


def plan(cfg: ModelConfig, vision: bool = False) -> dict:
    """{"ollama": {name: api_base}, "vllm": [(role, model)], "other": [(role, model)]}"""
    out: dict = {"ollama": {}, "vllm": [], "other": []}
    for role, rc in cfg.roles.items():
        if role == "vision" and not vision:
            continue
        name = ollama_name(rc.model)
        if name:
            out["ollama"].setdefault(name, rc.resolved_api_base())
        elif rc.model.startswith(VLLM):
            out["vllm"].append((role, rc.model.removeprefix(VLLM)))
        else:
            out["other"].append((role, rc.model))
    return out


def present(api_base: str) -> set[str]:
    tags = httpx.get(f"{api_base.rstrip('/')}/api/tags", timeout=10).raise_for_status().json()
    names = {m["name"] for m in tags.get("models", [])}
    return names | {n.removesuffix(":latest") for n in names}


def pull(api_base: str, name: str) -> None:
    last = ""
    with httpx.stream(
        "POST", f"{api_base.rstrip('/')}/api/pull", json={"model": name}, timeout=None
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            msg = json.loads(line)
            if "error" in msg:
                raise RuntimeError(f"{name}: {msg['error']}")
            status = msg.get("status", "")
            if msg.get("total") and msg.get("completed"):
                status += f" {100 * msg['completed'] // msg['total']} %"
            if status != last:
                print(f"    {name}: {status}", flush=True)
                last = status


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path)
    ap.add_argument("--pull", action="store_true", help="download missing Ollama models")
    ap.add_argument("--vision", action="store_true", help="include the vision role")
    args = ap.parse_args()
    path = args.config or get_settings().cmp_models_config
    cfg = load_model_config(path)
    print(f"{path.name} (version {cfg.version})")
    for role, rc in cfg.roles.items():
        print(f"  {role:<10} {rc.model}")
    p = plan(cfg, args.vision)
    missing = 0
    for name, base in p["ollama"].items():
        have = present(base)
        if name in have:
            print(f"  ok       {name}")
            continue
        if args.pull:
            print(f"  pulling  {name} (once; can take a while)")
            pull(base, name)
        else:
            print(f"  MISSING  {name}  (run with --pull)")
            missing += 1
    for role, model in p["vllm"]:
        if model != cfg.serve_model:
            print(f"  WARNING  {role} asks vLLM for {model}, but it serves {cfg.serve_model}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
