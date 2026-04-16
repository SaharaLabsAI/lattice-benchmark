#!/usr/bin/env python3
"""Build `data/benchmark.json` for the site from `data/scores_*.json`."""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "data" / "scores_1200.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "benchmark.json"

DIM_LABELS = {
    "D1": "Intent Fidelity",
    "D2": "Mechanism Clarity",
    "D3": "Uncertainty Handling",
    "D4": "Actionability",
    "D5": "Evidence Coverage",
    "D6": "Response Structure",
    "T16-1": "Boundary Recognition and Clean Refusal",
}

CATEGORY_ORDER = [
    "C1 Basic",
    "C2 Comparative",
    "C3 Constrained",
    "C4 Decision",
    "C5 Ambiguous",
]

WEIGHTS = {
    "C1 Basic": {"D1": 15, "D2": 25, "D3": 15, "D4": 15, "D5": 15, "D6": 15},
    "C2 Comparative": {"D1": 15, "D2": 20, "D3": 20, "D4": 15, "D5": 15, "D6": 15},
    "C3 Constrained": {"D1": 20, "D2": 15, "D3": 20, "D4": 20, "D5": 10, "D6": 15},
    "C4 Decision": {"D1": 15, "D2": 10, "D3": 30, "D4": 20, "D5": 10, "D6": 15},
    "C5 Ambiguous": {"D1": 25, "D2": 15, "D3": 25, "D4": 10, "D5": 10, "D6": 15},
}


def avg(nums):
    if not nums:
        return 0.0
    return sum(nums) / len(nums)


def stddev(nums):
    if len(nums) <= 1:
        return 0.0
    mean = avg(nums)
    return math.sqrt(sum((x - mean) ** 2 for x in nums) / len(nums))


def round2(value):
    return round(value + 1e-8, 2)


def _manifest_agent_files(agent_files: dict) -> dict:
    return {k: f"./response/{Path(v).name}" for k, v in sorted(agent_files.items())}


def _resolve_repo_path(path_str: str) -> Path:
    """Resolve metadata paths from scores JSON."""
    raw = Path(path_str)
    name = raw.name
    if raw.is_absolute():
        return raw.expanduser().resolve()
    rel = path_str.replace("\\", "/").lstrip("./")
    candidates = [
        REPO_ROOT / raw,
        REPO_ROOT / rel,
        REPO_ROOT / "data" / rel,
        REPO_ROOT / "data" / name,
        REPO_ROOT / "data" / "query" / name,
        REPO_ROOT / "data" / "response" / name,
        REPO_ROOT / "scoring" / "prompt" / name,
    ]
    for c in candidates:
        resolved = c.resolve()
        if resolved.exists():
            return resolved
    return (REPO_ROOT / raw).resolve()


def _manifest_path(path_str: str) -> str:
    if not path_str:
        return path_str
    try:
        resolved = _resolve_repo_path(path_str)
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return path_str.replace("\\", "/")


def _task_sort_key(task_id: str) -> tuple[int, str]:
    """Sort by numeric T-prefix when available, else lexicographically."""
    if not task_id:
        return (10**9, "")
    head = str(task_id).split()[0].upper()
    if head.startswith("T"):
        suffix = head[1:]
        if suffix.isdigit():
            return (int(suffix), str(task_id))
    return (10**9, str(task_id))


def main():
    ap = argparse.ArgumentParser(description="Build benchmark.json from a scores_*.json file")
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="Input scores JSON path")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output benchmark JSON path")
    args = ap.parse_args()

    input_path = Path(args.input).expanduser()
    output_path = Path(args.output).expanduser()
    if not input_path.is_absolute():
        input_path = REPO_ROOT / input_path
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path

    if not input_path.exists():
        raise SystemExit(f"Missing input: {input_path}")

    raw = json.loads(input_path.read_text())
    agents = sorted(raw["summary"].keys())

    queries = []
    for row in raw["results"]:
        entry = {
            "qid": row["qid"],
            "task_id": row["task_id"],
            "category": row["category"],
            "query": row["query"],
            "agents": {},
        }
        best_agent = None
        best_score = -1.0
        for agent_result in row["results"]:
            agent = agent_result["agent"]
            total_score = agent_result["total_score"]
            entry["agents"][agent] = {
                "total_score": total_score,
                "dimensions": agent_result["dimensions"],
            }
            if total_score > best_score:
                best_score = total_score
                best_agent = agent
        entry["best"] = best_agent
        queries.append(entry)

    # leaderboard from summary
    leaderboard = []
    for agent, summary in raw["summary"].items():
        leaderboard.append(
            {
                "agent": agent,
                "total_score": round2(summary["total_score"]),
                "avg_score": round2(summary["avg_score"]),
                "count": summary["count"],
                "errors": summary.get("errors", 0),
            }
        )
    leaderboard.sort(key=lambda x: x["avg_score"], reverse=True)

    # per category and per task aggregates
    category_stats = {cat: {"count": 0, "agents": {a: [] for a in agents}} for cat in CATEGORY_ORDER}
    observed_tasks = sorted({q["task_id"] for q in queries}, key=_task_sort_key)
    task_stats = {task: {"count": 0, "agents": {a: [] for a in agents}} for task in observed_tasks}

    for q in queries:
        cat = q["category"]
        task = q["task_id"]
        if cat not in category_stats:
            category_stats[cat] = {"count": 0, "agents": {a: [] for a in agents}}
        if task not in task_stats:
            task_stats[task] = {"count": 0, "agents": {a: [] for a in agents}}
        category_stats[cat]["count"] += 1
        task_stats[task]["count"] += 1
        for agent in agents:
            category_stats[cat]["agents"][agent].append(q["agents"][agent]["total_score"])
            task_stats[task]["agents"][agent].append(q["agents"][agent]["total_score"])

    categories = []
    for cat, info in category_stats.items():
        if info["count"] == 0:
            continue
        categories.append(
            {
                "id": cat,
                "count": info["count"],
                "agents": {a: round2(avg(scores)) for a, scores in info["agents"].items()},
            }
        )

    tasks = []
    for task in sorted(task_stats.keys(), key=_task_sort_key):
        info = task_stats[task]
        if info["count"] == 0:
            continue
        tasks.append(
            {
                "id": task,
                "count": info["count"],
                "agents": {a: round2(avg(scores)) for a, scores in info["agents"].items()},
            }
        )

    # dimension aggregates across all queries
    dim_scores = defaultdict(lambda: {a: [] for a in agents})
    for q in queries:
        for agent in agents:
            dims = q["agents"][agent]["dimensions"]
            for dim, detail in dims.items():
                dim_scores[dim][agent].append(detail["score"])

    dimensions = []
    # keep a stable order
    dim_order = ["D1", "D2", "D3", "D4", "D5", "D6", "T16-1"]
    for dim in dim_order:
        if dim not in dim_scores:
            continue
        dimensions.append(
            {
                "id": dim,
                "label": DIM_LABELS.get(dim, dim),
                "agents": {a: round2(avg(scores)) for a, scores in dim_scores[dim].items()},
            }
        )

    # score distributions per agent
    buckets = {}
    for agent in agents:
        scores = [q["agents"][agent]["total_score"] for q in queries]
        dist = {"0-49": 0, "50-59": 0, "60-69": 0, "70-79": 0, "80-89": 0, "90-100": 0}
        for score in scores:
            if score < 50:
                dist["0-49"] += 1
            elif score < 60:
                dist["50-59"] += 1
            elif score < 70:
                dist["60-69"] += 1
            elif score < 80:
                dist["70-79"] += 1
            elif score < 90:
                dist["80-89"] += 1
            else:
                dist["90-100"] += 1
        buckets[agent] = dist

    # consistency
    consistency = []
    for agent in agents:
        scores = [q["agents"][agent]["total_score"] for q in queries]
        consistency.append(
            {
                "agent": agent,
                "avg_score": round2(avg(scores)),
                "stddev": round2(stddev(scores)),
            }
        )
    consistency.sort(key=lambda x: x["stddev"])

    raw_agents = raw.get("agent_files") or {}
    meta = {
        "model": raw.get("model"),
        "instructions_path": _manifest_path(raw.get("instructions_path", "")),
        "queries_path": _manifest_path(raw.get("queries_path", "")),
        "agent_files": _manifest_agent_files(raw_agents),
        "total_scored": raw.get("total_scored"),
        "agents": agents,
        "query_count": len(queries),
        "task_count": len({q["task_id"] for q in queries}),
        "category_count": len({q["category"] for q in queries}),
        "dimensions": [d["id"] for d in dimensions],
        "dimension_labels": {d["id"]: d["label"] for d in dimensions},
        "weights": WEIGHTS,
        "generated_from": input_path.name,
    }

    output = {
        "meta": meta,
        "agents": agents,
        "leaderboard": leaderboard,
        "categories": categories,
        "tasks": tasks,
        "dimensions": dimensions,
        "buckets": buckets,
        "consistency": consistency,
        "queries": queries,
    }

    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
