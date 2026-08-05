#!/usr/bin/env python3
"""Build SQLite FTS5 index from processed JSON corpora."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.index import build_index
from petrarch_search.paths import CANZONIERE_JSON, COMMENTARIES_DIR, DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canzoniere", type=Path, default=CANZONIERE_JSON)
    parser.add_argument("--commentaries", type=Path, default=COMMENTARIES_DIR)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    result = build_index(
        canzoniere_path=args.canzoniere,
        commentaries_dir=args.commentaries,
        db_path=args.db,
    )
    print(
        f"Index built: {result['poems']} poems, "
        f"{result['commentaries']} commentaries, {result['segments']} segments"
    )


if __name__ == "__main__":
    main()
