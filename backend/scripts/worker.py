"""Run a background worker (ingestion jobs).

    uv run python -m scripts.worker [--concurrency 1]

Needs the ingest dependency group (Docling): uv sync --group ingest. On Windows, psycopg's
async mode needs the selector event loop, so this script sets it before starting.
"""

import argparse
import asyncio
import logging
import sys

from jobs.app import INGEST_QUEUE, app


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=1, help="jobs at once (ingest is heavy)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    for noisy in ("LiteLLM", "httpx", "docling", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    app.run_worker(queues=[INGEST_QUEUE], concurrency=args.concurrency)


if __name__ == "__main__":
    main()
