"""Budget the exact compact tool text with a named, reproducible tokenizer."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

from semantic_search.source import physical_lines

ENCODING = "cl100k_base"
INDEX_MEMBERSHIP = "saved implementations; Git-tracked files only in Git projects"
MAX_SCOPE_EXCLUSIONS = 4
MAX_SCOPE_EXCLUSION_CHARS = 80


def serialize_context(envelope):
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@lru_cache(maxsize=1)
def encoding():
    import tiktoken
    cache = Path(__file__).resolve().parents[2] / ".cache/tokenizers"
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(cache))
    return tiktoken.get_encoding(ENCODING)


def indexed_scope(source_root, source_exclusions=()):
    """Bound the scope summary without turning shortened text into new globs."""
    displayed = [pattern for pattern in source_exclusions
                 if len(pattern) <= MAX_SCOPE_EXCLUSION_CHARS][:MAX_SCOPE_EXCLUSIONS]
    return {"source_root": source_root, "membership": INDEX_MEMBERSHIP,
            "source_exclusions": displayed,
            "exclusions_omitted": len(source_exclusions) - len(displayed)}


def compact_result(response):
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    rows = [{**{key: row[key] for key in fields},
             "origin": row.get("origin", "retrieval"), "related_to": row.get("related_to")}
            for row in response["results"]]
    result = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
              "results": rows, "budget_exhausted": response.get("budget_exhausted", False),
              "token_usage": response.get("token_usage", {"encoding": ENCODING, "tokens": None,
                                                          "budget": None, "truncated": False})}
    if "scope" in response:
        result["scope"] = response["scope"]
    return result


def count_context(response):
    """Count the serialized payload, including its self-reported token count."""
    counter = encoding()
    usage = response["token_usage"]
    usage["tokens"] = 0
    # Including the token count in the payload requires a fixed point: a change
    # in its digit length can itself change the serialized payload's token count.
    for _ in range(12):
        count = len(counter.encode(serialize_context(compact_result(response)), disallowed_special=()))
        if count == usage["tokens"]:
            return count
        usage["tokens"] = count
    raise RuntimeError("Serialized token count did not stabilize")


def apply_token_budget(response, budget):
    """Trim source by complete lines and keep truthful locations and hashes."""
    result = copy.deepcopy(response)
    result["token_usage"] = {"encoding": ENCODING, "tokens": 0, "budget": budget, "truncated": False}
    original = result["results"]
    result["results"] = []
    if count_context(result) > budget:
        raise ValueError("token_budget cannot contain the query and result metadata; shorten the query or raise the budget")
    for row in original:
        result["results"].append(row)
        if count_context(result) <= budget:
            continue
        result["budget_exhausted"] = result["token_usage"]["truncated"] = True
        lines = physical_lines(row["source"])
        full = row["source"]
        # Tokenization is not monotonic across concatenation boundaries, so
        # verify every candidate rather than assume a binary-search boundary.
        while lines:
            lines.pop()
            row["source"] = "".join(lines)
            row["end_line"] = row["start_line"] + len(lines) - 1
            row["source_hash"] = hashlib.sha256(row["source"].encode()).hexdigest()
            row["truncated"] = row["source"] != full
            if lines and count_context(result) <= budget:
                break
        if not lines:
            result["results"].pop()
        if count_context(result) > budget:
            raise ValueError("token_budget cannot contain result metadata")
    result["total_source_chars"] = sum(len(r["source"]) for r in result["results"])
    count_context(result)
    return result
