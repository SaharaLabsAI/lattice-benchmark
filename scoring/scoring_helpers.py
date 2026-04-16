#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helper module: constants, JSON schema, and utility functions."""

import glob
import hashlib
import json
import logging
import os
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

# ============================================================================
# Configuration constants
# ============================================================================

# Per-dimension score weights by category.
WEIGHTS_BY_CATEGORY: Dict[str, Dict[str, int]] = {
    "C1 Basic":       {"D1": 15, "D2": 25, "D3": 15, "D4": 15, "D5": 15, "D6": 15},
    "C2 Comparative": {"D1": 15, "D2": 20, "D3": 20, "D4": 15, "D5": 15, "D6": 15},
    "C3 Constrained": {"D1": 20, "D2": 15, "D3": 20, "D4": 20, "D5": 10, "D6": 15},
    "C4 Decision":    {"D1": 15, "D2": 10, "D3": 30, "D4": 20, "D5": 10, "D6": 15},
    "C5 Ambiguous":   {"D1": 25, "D2": 15, "D3": 25, "D4": 10, "D5": 10, "D6": 15},
}


# ============================================================================
# JSON Schema definitions
# ============================================================================

def _make_dimension_schema() -> Dict[str, Any]:
    """Create schema for a single dimension."""
    return {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 10},
            "reason": {"type": "string"},
        },
        "required": ["score", "reason"],
        "additionalProperties": False,
    }


def make_output_schema(dimensions: List[str]) -> Dict[str, Any]:
    """Create the complete output schema."""
    dim_schema = {dim: _make_dimension_schema() for dim in dimensions}
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string"},
                        "dimensions": {
                            "type": "object",
                            "properties": dim_schema,
                            "required": dimensions,
                            "additionalProperties": False,
                        },
                    },
                    "required": ["agent", "dimensions"],
                    "additionalProperties": False,
                },
                "minItems": 1,
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }


OUTPUT_SCHEMA_T1_T15 = make_output_schema(["D1", "D2", "D3", "D4", "D5", "D6"])
OUTPUT_SCHEMA_T16 = make_output_schema(["T16-1"])


# ============================================================================
# Logging
# ============================================================================

LOGGER = logging.getLogger("scoring")


def setup_logging(log_path: str) -> None:
    """Configure logger output."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if LOGGER.handlers:
        return
    
    LOGGER.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    
    for handler in [
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]:
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)


# ============================================================================
# JSON / JSONL IO
# ============================================================================

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# Global lock for concurrent JSONL writes.
_jsonl_write_lock = threading.Lock()


def append_jsonl(path: str, obj: Any) -> None:
    """Append one line to a JSONL file (thread-safe)."""
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with _jsonl_write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def load_jsonl_keys(path: str) -> Set[str]:
    """Load existing key set from a JSONL file."""
    keys = set()
    if not os.path.exists(path):
        return keys
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if "key" in obj:
                        keys.add(obj["key"])
                except json.JSONDecodeError:
                    continue
    return keys


def load_jsonl_as_list(
    path: str, 
    filter_instructions: Optional[str] = None, 
    filter_model: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Load a JSONL file into a list.
    Optional filters: only return rows matching instructions_path and model.
    """
    results = []
    if not os.path.exists(path):
        return results
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Filtering.
            if filter_instructions and obj.get("instructions_path") != filter_instructions:
                continue
            if filter_model and obj.get("model") != filter_model:
                continue
            results.append(obj)
    return results


# ============================================================================
# Utility functions
# ============================================================================

def make_cache_key(instructions_path: str, model: str, qid: Any, agents: List[str]) -> str:
    """Generate cache key from instructions_path, model, qid, and agents."""
    content = f"{instructions_path}|{model}|{qid}|{','.join(sorted(agents))}"
    return hashlib.sha256(content.encode()).hexdigest()


def extract_first_json_object(text: str) -> str:
    """Extract the first complete JSON object from text."""
    start = text.find("{")
    if start == -1:
        return text.strip()
    
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text.strip()


def is_task_16(task: str) -> bool:
    return "T16" in task.upper()


def parse_usage(usage_obj: Any) -> Dict[str, int]:
    """Parse token usage from API response usage object."""
    if not usage_obj:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    if isinstance(usage_obj, dict):
        input_t = usage_obj.get("input_tokens") or usage_obj.get("prompt_tokens") or 0
        output_t = usage_obj.get("output_tokens") or usage_obj.get("completion_tokens") or 0
    else:
        input_t = getattr(usage_obj, "input_tokens", None) or getattr(usage_obj, "prompt_tokens", 0)
        output_t = getattr(usage_obj, "output_tokens", None) or getattr(usage_obj, "completion_tokens", 0)
    
    return {
        "input_tokens": int(input_t or 0),
        "output_tokens": int(output_t or 0),
        "total_tokens": int((input_t or 0) + (output_t or 0)),
    }


# ============================================================================
# Agent file discovery
# ============================================================================

def discover_agent_files(directory: str = ".", prefix: str = "filtered_response_") -> Dict[str, str]:
    """
    Auto-discover JSON files with a given prefix, returning {agent_name: file_path}.
    file_path uses the glob result path (usually relative to cwd).
    """
    pattern = os.path.join(directory, f"{prefix}*.json")
    agent_files = {}
    
    for filepath in sorted(glob.glob(pattern)):
        filename = os.path.basename(filepath)
        # Extract agent name: filtered_response_XXX.json -> XXX
        if filename.startswith(prefix) and filename.endswith(".json"):
            agent_name = filename[len(prefix):-5]  # remove prefix and ".json"
            if agent_name:
                agent_files[agent_name] = filepath
    
    return agent_files


# ============================================================================
# Data loading
# ============================================================================

def load_agent_answers(path: str) -> Dict[str, str]:
    """Load an agent answer file, returning {query: final_answer}."""
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"Agent file must be a list: {path}")
    
    answers = {}
    for item in data:
        query = (item.get("query") or item.get("Query") or "").strip()
        if query:
            answers[query] = item.get("final_answer") or item.get("response") or ""
    return answers


def get_query_fields(item: Dict[str, Any]) -> Tuple[Any, str, str, str]:
    """Extract id, query, category, and task from one query item."""
    qid = item.get("id")
    query = (item.get("query") or item.get("Query") or "").strip()
    category = (item.get("category") or item.get("Category") or "").strip()
    task = (item.get("task") or item.get("Task") or "").strip()
    return qid, query, category, task


def build_answers_for_query(query: str, agent_maps: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    """Build all agent answers for one query."""
    answers = []
    for agent, mapping in agent_maps.items():
        final_answer = mapping.get(query.strip(), "")
        if final_answer:
            answers.append({"agent": agent, "final_answer": final_answer})
        else:
            answers.append({"agent": agent, "error": "missing answer", "final_answer": ""})
    return answers


# ============================================================================
# Score computation
# ============================================================================

def compute_weighted_scores(category: str, dimensions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Compute weighted scores for T1-T15 tasks."""
    if category not in WEIGHTS_BY_CATEGORY:
        raise ValueError(f"Unknown category: {category}")
    
    weights = WEIGHTS_BY_CATEGORY[category]
    weighted_dims = {}
    total = 0.0
    
    for dim, weight in weights.items():
        score = dimensions[dim]["score"]
        weighted_score = round((score / 10.0) * weight, 4)
        weighted_dims[dim] = {
            "score": score,
            "weighted_score": weighted_score,
            "reason": dimensions[dim]["reason"],
        }
        total += weighted_score
    
    return {"dimensions": weighted_dims, "total_score": round(total, 4)}


def compute_t16_score(dimensions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Compute score for T16 tasks."""
    score = dimensions["T16-1"]["score"]
    weighted_score = round((score / 10.0) * 100, 4)
    return {
        "dimensions": {
            "T16-1": {"score": score, "weighted_score": weighted_score, "reason": dimensions["T16-1"]["reason"]}
        },
        "total_score": weighted_score,
    }
