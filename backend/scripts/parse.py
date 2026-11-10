"""Parse (and cache) every file of a subject without ingesting it.

uv run python -m scripts.parse --subject <folder under data/> [--ocr]
"""

import argparse
import logging
import sys
import time

from core.settings import REPO_ROOT
from ingestion.parse import SUPPORTED_SUFFIXES, cache_dir_for, parse_file


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--ocr", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    source_dir = REPO_ROOT / "data" / args.subject
    for path in sorted(p for p in source_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES):
        t = time.time()
        doc = parse_file(path, cache_dir_for(source_dir), ocr=args.ocr)
        print(f"{path.name}: {doc.num_pages()} pages in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
