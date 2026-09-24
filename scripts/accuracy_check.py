#!/usr/bin/env python3
"""Run the extractor over the labelled set and print a scorecard.

    python scripts/accuracy_check.py [--model claude-sonnet-5]

Costs a few cents: 20 articles in batches of 8.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.funding_radar.accuracy import load_fixtures, score, to_articles
from src.funding_radar.extract import BATCH_SIZE, extract_batch
from src.funding_radar.settings import settings
from src.funding_radar.sources.base import load_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None, help="override the extraction model")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    fixtures = load_fixtures()
    articles = to_articles(fixtures)
    sectors = load_config().get("sectors", [])

    results: dict[int, object] = {}
    for start in range(0, len(articles), BATCH_SIZE):
        batch = articles[start:start + BATCH_SIZE]
        for offset, value in extract_batch(batch, sectors, model=args.model).items():
            results[start + offset] = value

    report = score(fixtures, results)
    print(f"\nLabelled set: {len(fixtures)} articles, model {args.model or settings.EXTRACT_MODEL}\n")
    print(report.format())
    correct, scored = report.overall
    return 0 if scored and correct == scored else 1


if __name__ == "__main__":
    sys.exit(main())
