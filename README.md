# LATTICE: Evaluating Decision Support Utility of Crypto Agents

## Overview

**LATTICE** is a benchmark for evaluating the *decision support utility* of crypto agents in realistic user-facing scenarios. Prior crypto agent benchmarks focus primarily on reasoning-based (e.g., factual correctness) or outcome-based (e.g., financial returns) evaluation. Although crypto agents are often used as copilots working directly with humans in the loop, existing benchmarks do not specifically assess agents' ability to assist user decision-making. LATTICE addresses this gap by:

1. Defining **six evaluation dimensions** that capture key decision support properties: Intent Fidelity, Mechanism Clarity, Uncertainty Handling, Actionability, Evidence Coverage, and Response Structure.
2. Proposing **16 task types** that span the end-to-end crypto copilot workflow, including market research, risk analysis, and execution planning.
3. Structuring queries into **five categories** — Basic, Comparative, Constrained, Decision, and Ambiguous — to probe distinct agent competencies.
4. Using **LLM judges** to automatically score agent outputs based on these dimensions and tasks, without relying on ground truth from expert annotators or external data sources.

The dimensions, tasks, and categories are designed to be evaluable at scale using LLM judges. The judge rubrics can be continually audited and updated given new dimensions, tasks, criteria, and human feedback, promoting reliable and extensible evaluation.

Unlike benchmarks that compare foundation models on a shared agent framework, LATTICE evaluates production-level copilots as deployed in real products, reflecting the importance of orchestration and UI/UX in determining agent quality. We evaluate six real-world crypto copilots on 1,200 diverse queries and report breakdowns across dimensions, tasks, and query categories. Most copilots achieve comparable aggregate scores, but differ more significantly at the dimension and task level — suggesting meaningful trade-offs in decision support quality that aggregate rankings alone do not capture.

For more details, please visit our benchmark [website](https://saharaai.com/crypto-agent-benchmark).

## What This Repo Contains

| Path | Description |
|---|---|
| `data/query/filtered_queries_1200.json` | 1,200 benchmark queries |
| `data/response/output/1200_queries/` | Agent response files, one per agent |
| `scoring/` | LLM-judge scoring pipeline |
| `data/scores_1200.json` | Pre-computed evaluation results |
| `rubric/` | Full task and evaluation protocol |

## Scoring Logic

Tasks T1–T15 are scored on six dimensions (D1–D6), each 0–10, then combined into a 0–100 total using category-specific weights defined in `scoring/scoring_helpers.py` (`WEIGHTS_BY_CATEGORY`).

| Dimension | Name |
|---|---|
| D1 | Intent Fidelity |
| D2 | Mechanism Clarity |
| D3 | Uncertainty Handling |
| D4 | Actionability |
| D5 | Evidence Coverage |
| D6 | Response Structure |

Task T16 (boundary recognition) uses a single specialized dimension — Refusal (keyed as `T16-1` in score output) — scaled directly to 0–100.

The judge calls the OpenAI `responses.create` API with strict JSON schema output for structured, parseable results. Scoring runs in real time per query with no caching.

## Setup

```bash
pip install -r requirements.txt
```

Set your API key via environment variable or `.env` file:

```bash
export OPENAI_API_KEY="your-api-key"
```

## Run Scoring

```bash
python3 -m scoring.batch \
  --queries data/query/filtered_queries_1200.json \
  --agents-dir data/response/output/1200_queries \
  --agents-prefix filtered_response_ \
  --model gpt-5.2 \
  --instructions-path scoring/prompt/judge_instructions_en.txt \
  --output data/scores_1200.json \
  --max-workers 12 \
  --retries 3
```

## CLI Arguments

| Argument | Description |
|---|---|
| `--queries` | Path to query JSON file |
| `--agents-dir` | Directory containing agent response files |
| `--agents-prefix` | Filename prefix before agent name (e.g. `filtered_response_`) |
| `--model` | Judge model identifier |
| `--instructions-path` | Path to judge instruction prompt |
| `--output` | Output path for score JSON |
| `--log-dir` | Log directory |
| `--log` | Log filename under `--log-dir` |
| `--max-workers` | Number of parallel scoring threads |
| `--retries` / `--backoff` | Retry count and backoff factor for judge calls |

## Data Formats

### Query file

Each item in the query JSON list has four fields:

```json
{
  "id": 1,
  "query": "What are the main risks of providing liquidity on Uniswap v3?",
  "category": "C1 Basic",
  "task": "T1 Protocol Mechanism Interpretation"
}
```

`category` is one of `C1 Basic`, `C2 Comparative`, `C3 Constrained`, `C4 Decision`, or `C5 Ambiguous`. `task` is one of `T1`–`T16`.

### Agent response file

Each agent's responses are stored as a JSON list, matched to the query by the `query` field:

```json
[
  {
    "query": "What are the main risks of providing liquidity on Uniswap v3?",
    "final_answer": "The primary risks include impermanent loss, which is amplified in v3 due to concentrated liquidity positions..."
  }
]
```

To evaluate your own agent, create a file in this format and place it in the agents directory with the naming convention `<prefix><agent-name>.json`.

### Score output

The output JSON contains three top-level keys:

- **`summary`** — per-agent aggregate: `total_score`, `count`, `errors`, `avg_score`
- **`results`** — per-query, per-agent dimension scores (`D1`–`D6`) and `total_score`
- **metadata** — `instructions_path`, `queries_path`, `agent_files`, `model`, `total_scored`

## Build Benchmark JSON (Optional)

Aggregates raw scores into leaderboard and per-category/per-task summaries:

```bash
python3 -m scoring.build_benchmark \
  --input data/scores_1200.json \
  --output data/benchmark.json
```

## Smoke Check

```bash
python3 -m compileall scoring
python3 -m scoring.batch --help
python3 -m scoring.build_benchmark --help
```

## License

MIT — Copyright (c) 2026 Sahara AI
