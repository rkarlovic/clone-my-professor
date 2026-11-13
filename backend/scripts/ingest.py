"""Ingest a subject folder (data/<subject>/) into a new draft kb_version.

    uv run python -m scripts.ingest --subject <folder> [--context] [--ocr]
                                    [--min-tokens 25] [--max-tokens 800]
                                    [--duplicate-similarity 0.85 | --keep-duplicates]

Writes a report with the settings used to eval/reports/<subject>/ingest-v<N>.json.
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict

from core.settings import REPO_ROOT
from ingestion.chunking import ChunkSettings
from ingestion.pipeline import IngestOptions, ingest_subject


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--context", action="store_true", help="add `context` role sentence per chunk")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--min-tokens", type=int, default=ChunkSettings.min_tokens)
    ap.add_argument("--max-tokens", type=int, default=ChunkSettings.max_tokens)
    ap.add_argument(
        "--duplicate-similarity",
        type=float,
        default=ChunkSettings.duplicate_similarity,
        help="word overlap at which a repeated page is folded into its first occurrence",
    )
    ap.add_argument("--keep-duplicates", action="store_true", help="do not fold repeated pages")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    for noisy in ("LiteLLM", "httpx", "docling", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    options = IngestOptions(
        chunking=ChunkSettings(
            min_tokens=args.min_tokens,
            max_tokens=args.max_tokens,
            duplicate_similarity=None if args.keep_duplicates else args.duplicate_similarity,
        ),
        ocr=args.ocr,
        with_context=args.context,
    )
    report = ingest_subject(REPO_ROOT / "data" / args.subject, options)

    out = REPO_ROOT / "eval" / "reports" / args.subject / f"ingest-v{report.version}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
