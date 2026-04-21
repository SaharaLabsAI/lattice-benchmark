#!/usr/bin/env python3
"""Build a 1200-query benchmark by concatenating 240 + 960 query sets.

Input format:
- JSON array of objects with keys: id, task, category, query

Output format:
- Same schema, with ids reindexed from 0..1199
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
QUERY_DIR = SCRIPT_DIR.parent.parent
DEFAULT_INPUT_240 = QUERY_DIR / "filtered_queries_240.json"
DEFAULT_INPUT_960 = QUERY_DIR / "filtered_queries_960.json"
DEFAULT_OUTPUT = QUERY_DIR / "filtered_queries_1200.json"
REQUIRED_KEYS = {"id", "task", "category", "query"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build 1200-query dataset from 240 and 960 subsets")
    parser.add_argument("--input-240", type=Path, default=DEFAULT_INPUT_240)
    parser.add_argument("--input-960", type=Path, default=DEFAULT_INPUT_960)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def load_queries(path: Path, expected_len: int) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path} must be a JSON array")
    if len(rows) != expected_len:
        raise ValueError(f"{path} expected {expected_len} rows, got {len(rows)}")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{path} row {i} is not an object")
        missing = REQUIRED_KEYS - set(row.keys())
        if missing:
            raise ValueError(f"{path} row {i} missing keys: {sorted(missing)}")
    return rows


def main() -> None:
    args = parse_args()

    rows_240 = load_queries(args.input_240, expected_len=240)
    rows_960 = load_queries(args.input_960, expected_len=960)

    merged: list[dict] = []
    for row in rows_240 + rows_960:
        merged.append(
            {
                "task": row["task"],
                "category": row["category"],
                "query": row["query"],
            }
        )

    if len(merged) != 1200:
        raise RuntimeError(f"Expected 1200 rows after merge, got {len(merged)}")

    for i, row in enumerate(merged):
        row["id"] = i

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {len(merged)} rows to {args.output}")


if __name__ == "__main__":
    main()
