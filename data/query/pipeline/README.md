# Query Pipeline

This directory documents the query-generation stages and keeps only scripts that are actively used in this repo.

## Stages

1. **80 -> 240 (LLM prompt stage)**
- Prompt file: `prompt/prompt_80_to_240_en.txt`
- Input: 80 seed queries (`query seeds.csv` in the original workflow)
- Output: 240 structured queries (saved as `filtered_queries_240.json` in this repo)

2. **240 -> 960 (Codex generation stage)**
- Generated directly by Codex for this repository iteration.
- Output committed as `filtered_queries_960.json`.
- There is intentionally no local reproduction script for this step in `pipeline/scripts`.

3. **240 + 960 -> 1200 (merge stage)**
- Script: `scripts/build_1200_queries.py`
- Output: `filtered_queries_1200.json`

## Workflow

1. Use `filtered_queries_80.json` as the raw seed set.
2. Produce `filtered_queries_240.json` from the 80->240 prompt stage.
3. Produce `filtered_queries_960.json` via Codex, with overlap checks against the original 240 set.
4. Run `scripts/build_1200_queries.py` to merge `240 + 960` and reindex IDs into `filtered_queries_1200.json`.

## Notes

- Scripts unrelated to active query-generation flow in this repo were removed.
- This repo ships generated JSON results directly:
  - `filtered_queries_80.json`
  - `filtered_queries_240.json`
  - `filtered_queries_960.json`
  - `filtered_queries_1200.json`
- Dataset relationship:
  - `80` and `240` are overlapped (`80` is contained within `240`).
  - `960` is exclusive relative to the original `240`.
  - Final total is `1200` unique queries.
- Codex processing:
  - Overlap checking for the 960-query set was handled by Codex.
  - Query ID reordering/normalization was handled by Codex.
