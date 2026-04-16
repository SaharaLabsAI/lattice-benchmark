#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch scoring script: evaluate multiple agent responses with an LLM judge."""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import OpenAI

try:
    from scoring.scoring_helpers import (
        OUTPUT_SCHEMA_T1_T15,
        OUTPUT_SCHEMA_T16,
        LOGGER,
        build_answers_for_query,
        compute_t16_score,
        compute_weighted_scores,
        discover_agent_files,
        dump_json,
        extract_first_json_object,
        get_query_fields,
        is_task_16,
        load_agent_answers,
        load_json,
        parse_usage,
        setup_logging,
    )
except ModuleNotFoundError as e:
    # Allow `python batch.py` from inside `scoring/` (cwd on sys.path, no package parent).
    if e.name not in {"scoring", "scoring.scoring_helpers", "scoring_helpers"}:
        raise
    from scoring_helpers import (
        OUTPUT_SCHEMA_T1_T15,
        OUTPUT_SCHEMA_T16,
        LOGGER,
        build_answers_for_query,
        compute_t16_score,
        compute_weighted_scores,
        discover_agent_files,
        dump_json,
        extract_first_json_object,
        get_query_fields,
        is_task_16,
        load_agent_answers,
        load_json,
        parse_usage,
        setup_logging,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]

load_dotenv()
openai_client: Optional[OpenAI] = None

ENGLISH_RATIONALE_POLICY = (
    "Language policy: all rationale/reason fields in the JSON output must be in English only. "
    "Do not use Chinese or mixed Chinese-English in rationale text."
)


# ============================================================================
# Core judging logic
# ============================================================================


def build_agents_block(answers: List[Dict[str, Any]]) -> str:
    """Build the text block containing agent answers."""
    return "\n\n".join(
        f"[Agent]\nname: {a['agent']}\nanswer:\n<<<\n{a['final_answer']}\n>>>"
        for a in answers
    )


def get_openai_client() -> OpenAI:
    """Lazily create the OpenAI client so `--help` works without API key."""
    global openai_client
    if openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        openai_client = OpenAI(api_key=api_key)
    return openai_client


def judge_many(
    model: str,
    query: str,
    answers: List[Dict[str, Any]],
    task: str,
    category: str,
    instructions: str,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Call the LLM judge."""
    user_payload = (
        f"[Task]\n<<<\n{task}\n>>>\n\n"
        f"[Category]\n<<<\n{category}\n>>>\n\n"
        f"[Query]\n<<<\n{query}\n>>>\n\n"
        f"[Agent Answers]\n<<<\n{build_agents_block(answers)}\n>>>"
    )

    LOGGER.info("Judging: task=%s, category=%s, agents=%s", task, category, [a["agent"] for a in answers])

    schema = OUTPUT_SCHEMA_T16 if is_task_16(task) else OUTPUT_SCHEMA_T1_T15
    merged_instructions = f"{instructions.strip()}\n\n# Mandatory language policy\n{ENGLISH_RATIONALE_POLICY}"

    resp = get_openai_client().responses.create(
        model=model,
        instructions=merged_instructions,
        input=[{"role": "user", "content": user_payload}],
        text={"format": {"type": "json_schema", "name": "scoring_result", "strict": True, "schema": schema}},
    )

    raw = (resp.output_text or "").strip()
    if not raw:
        raise RuntimeError("Empty model output")

    try:
        return json.loads(extract_first_json_object(raw)), parse_usage(getattr(resp, "usage", None))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON: {e}. Raw={raw[:500]}") from e


def judge_with_retry(
    model: str,
    query: str,
    answers: List[Dict[str, Any]],
    task: str,
    category: str,
    instructions: str,
    retries: int,
    backoff: float,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Judge with retries."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return judge_many(model, query, answers, task, category, instructions)
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"Judge failed after {retries} attempts") from last_error


# ============================================================================
# Per-query scoring
# ============================================================================


def score_one_query(
    query: str,
    category: str,
    task: str,
    answers: List[Dict[str, Any]],
    model: str,
    instructions: str,
    retries: int,
    backoff: float,
) -> Dict[str, Any]:
    """Score a single query."""
    results = []
    judge_inputs = []

    # Split valid and invalid answers.
    for item in answers:
        agent = item.get("agent", "")
        if item.get("error") or not item.get("final_answer"):
            results.append({"agent": agent, "error": item.get("error", "empty answer")})
        else:
            judge_inputs.append({"agent": agent, "final_answer": item["final_answer"]})

    # Run LLM judging.
    usage = None
    judged_map = {}

    if judge_inputs:
        try:
            judged, usage = judge_with_retry(
                model, query, judge_inputs, task, category, instructions, retries, backoff
            )
            LOGGER.info("Judge result: %s", json.dumps(judged, indent=2, ensure_ascii=False))
            judged_map = {r["agent"]: r for r in judged.get("results", []) if r.get("agent")}
        except Exception as e:
            for item in judge_inputs:
                results.append({"agent": item["agent"], "error": str(e)})

    # Post-process weighted results.
    for item in judge_inputs:
        agent = item["agent"]
        judged_item = judged_map.get(agent)
        if not judged_item:
            results.append({"agent": agent, "error": "missing judge output"})
            continue
        try:
            if is_task_16(task):
                weighted = compute_t16_score(judged_item["dimensions"])
            else:
                weighted = compute_weighted_scores(category, judged_item["dimensions"])
            results.append({"agent": agent, "dimensions": weighted["dimensions"], "total_score": weighted["total_score"]})
        except Exception as e:
            results.append({"agent": agent, "error": str(e)})

    out = {"task": task, "category": category, "query": query, "results": results}
    if usage:
        out["usage"] = usage
    return out


# ============================================================================
# Batch scoring
# ============================================================================


def score_all_queries(
    queries_path: str,
    agent_files: Dict[str, str],
    output_path: str,
    model: str,
    retries: int,
    backoff: float,
    instructions_path: str,
    max_workers: int,
) -> None:
    """Score all queries in a dataset."""
    queries = load_json(queries_path)
    if not isinstance(queries, list):
        raise ValueError("Queries file must be a list")

    agent_maps = {agent: load_agent_answers(path) for agent, path in agent_files.items()}
    agents = list(agent_maps.keys())
    LOGGER.info("Loaded agents: %s", agents)

    # Collect tasks to score (always real-time, no cache dependency).
    tasks_to_score = []
    for item in queries:
        qid, query, category, task = get_query_fields(item)
        if not query:
            LOGGER.warning("Skipping empty query: %s", item)
            continue
        tasks_to_score.append({"qid": qid, "query": query, "category": category, "task": task})

    LOGGER.info("Total queries: %d, to score (real-time): %d", len(queries), len(tasks_to_score))

    with open(instructions_path, "r", encoding="utf-8") as f:
        instructions = f.read()

    # Worker function.
    def do_score(t: Dict) -> Dict[str, Any]:
        answers = build_answers_for_query(t["query"], agent_maps)
        result = score_one_query(t["query"], t["category"], t["task"], answers, model, instructions, retries, backoff)
        entry: Dict[str, Any] = {
            "qid": t["qid"],
            "task_id": t["task"],
            "category": t["category"],
            "query": t["query"],
            "results": result["results"],
        }
        if "usage" in result:
            entry["usage"] = result["usage"]
        return entry

    # Execute scoring.
    all_results: List[Dict[str, Any]] = []
    if max_workers > 1 and len(tasks_to_score) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(do_score, t): t for t in tasks_to_score}
            for future in as_completed(futures):
                try:
                    result_entry = future.result()
                    all_results.append(result_entry)
                    LOGGER.info("Scored in real-time: qid=%s", result_entry["qid"])
                except Exception as e:
                    failed_task = futures[future]
                    LOGGER.error("Failed to score qid=%s: %s", failed_task["qid"], e)
                    all_results.append(
                        {
                            "qid": failed_task["qid"],
                            "task_id": failed_task["task"],
                            "category": failed_task["category"],
                            "query": failed_task["query"],
                            "results": [{"agent": agent, "error": str(e)} for agent in agents],
                        }
                    )
    else:
        for t in tasks_to_score:
            try:
                result_entry = do_score(t)
                all_results.append(result_entry)
                LOGGER.info("Scored in real-time: qid=%s", t["qid"])
            except Exception as e:
                LOGGER.error("Failed to score qid=%s: %s", t["qid"], e)
                all_results.append(
                    {
                        "qid": t["qid"],
                        "task_id": t["task"],
                        "category": t["category"],
                        "query": t["query"],
                        "results": [{"agent": agent, "error": str(e)} for agent in agents],
                    }
                )

    # Write summary output.
    _write_summary(
        instructions_path,
        queries_path,
        agent_files,
        model,
        output_path,
        agents,
        all_results,
    )


def _path_for_manifest(path: str) -> str:
    """Serialize paths relative to repo root when possible (for scores JSON / site)."""
    if not path:
        return path
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        resolved = p.expanduser().resolve()
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return path.replace("\\", "/")


def _agent_files_for_manifest(agent_files: Dict[str, str]) -> Dict[str, str]:
    """Paths under `data/response/` for static site fetch (`BASE_URL` + path)."""
    return {agent: f"./response/{os.path.basename(p)}" for agent, p in sorted(agent_files.items())}


def _resolve_existing_input_path(path_str: str) -> str:
    """Resolve input path robustly.

    Priority:
    1) absolute path as-is
    2) relative to current working directory (if exists)
    3) relative to REPO_ROOT
    """
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return str(p)
    cwd_candidate = (Path.cwd() / p)
    if cwd_candidate.exists():
        return str(cwd_candidate)
    return str(REPO_ROOT / p)


def _resolve_output_like_path(path_str: str) -> str:
    """Resolve output/log/instructions paths.

    For relative paths, prefer REPO_ROOT semantics so commands behave the same
    no matter where `python -m batch` is launched from.
    """
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return str(p)
    return str(REPO_ROOT / p)


def _write_summary(
    instructions_path: str,
    queries_path: str,
    agent_files: Dict[str, str],
    model: str,
    output_path: str,
    agents: List[str],
    all_results: List[Dict[str, Any]],
) -> None:
    """Write summary JSON."""
    LOGGER.info("Summary scope: real-time-results=%d", len(all_results))

    # Compute aggregate metrics.
    summary = {agent: {"total_score": 0.0, "count": 0, "errors": 0} for agent in agents}
    for entry in all_results:
        for r in entry.get("results", []):
            agent = r.get("agent", "")
            if agent not in summary:
                continue
            if "total_score" in r:
                summary[agent]["total_score"] += r["total_score"]
                summary[agent]["count"] += 1
            elif "error" in r:
                summary[agent]["errors"] += 1

    for s in summary.values():
        s["avg_score"] = round(s["total_score"] / s["count"], 4) if s["count"] > 0 else None

    dump_json(
        output_path,
        {
            "instructions_path": _path_for_manifest(instructions_path),
            "queries_path": _path_for_manifest(queries_path),
            "agent_files": _agent_files_for_manifest(agent_files),
            "model": model,
            "total_scored": len(all_results),
            "summary": summary,
            "results": all_results,
        },
    )
    LOGGER.info("Wrote summary: %s", output_path)


# ============================================================================
# CLI entrypoint
# ============================================================================


def main() -> None:
    data_dir = REPO_ROOT / "data"
    query_dir = data_dir / "query"
    response_dir = data_dir / "response"
    prompt_dir = REPO_ROOT / "scoring" / "prompt"
    default_queries = str(query_dir / "filtered_queries_1200.json")
    default_instructions = str(prompt_dir / "judge_instructions_en.txt")
    default_output = str(data_dir / "scores_1200.json")
    default_agents_dir = str(response_dir / "output" / "1200_queries")

    ap = argparse.ArgumentParser(description="Batch scoring script")

    ap.add_argument("--queries", default=default_queries, help="Query list JSON")
    ap.add_argument("--agents-dir", default=default_agents_dir, help="Directory containing agent response files")
    ap.add_argument("--agents-prefix", default="filtered_response_", help="Agent filename prefix")

    ap.add_argument("--log-dir", default=str(REPO_ROOT / "logs"), help="Log directory")
    ap.add_argument("--output", "-o", default=default_output, help="Output score JSON")
    ap.add_argument("--log", default="scoring.log", help="Log filename")

    ap.add_argument("--model", "-m", default="gpt-5.2", help="Judge model")
    ap.add_argument("--instructions-path", default=default_instructions, help="Judge instructions file")
    ap.add_argument("--retries", type=int, default=3, help="Retry attempts")
    ap.add_argument("--backoff", type=float, default=2.0, help="Retry backoff factor")
    ap.add_argument("--max-workers", type=int, default=5, help="Parallel worker count")

    args = ap.parse_args()

    # Normalize CLI paths so running from repo root or from ./scoring (`python -m batch`) behaves consistently.
    args.queries = _resolve_existing_input_path(args.queries)
    args.agents_dir = _resolve_existing_input_path(args.agents_dir)
    args.instructions_path = _resolve_existing_input_path(args.instructions_path)
    args.log_dir = _resolve_output_like_path(args.log_dir)
    args.output = _resolve_output_like_path(args.output)

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    # Auto-discover agent response files.
    agent_files = discover_agent_files(args.agents_dir, args.agents_prefix)
    if not agent_files:
        print(f"ERROR: No agent files found with prefix '{args.agents_prefix}' in '{args.agents_dir}'", file=sys.stderr)
        sys.exit(1)

    print(f"Discovered agents: {list(agent_files.keys())}")

    # Setup logging.
    os.makedirs(args.log_dir, exist_ok=True)
    setup_logging(os.path.join(args.log_dir, args.log))

    # Run scoring.
    score_all_queries(
        queries_path=args.queries,
        agent_files=agent_files,
        output_path=args.output,
        model=args.model,
        retries=max(1, args.retries),
        backoff=max(0.1, args.backoff),
        instructions_path=args.instructions_path,
        max_workers=max(1, args.max_workers),
    )


if __name__ == "__main__":
    main()
