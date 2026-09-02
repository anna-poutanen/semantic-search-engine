"""Conservative development-calibrated rejection of weak semantic matches."""
import copy
import json
import math
from pathlib import Path
import re

EMBEDDING_KEYS = ("id", "revision", "query_prompt", "pooling", "normalization", "dimensions")
MEANING = "Insufficient semantic evidence; this does not prove the behavior is absent."


def _score(value):
    if type(value) not in (float, int) or not -1 <= value <= 1 or not math.isfinite(value):
        raise ValueError("Cosine score must be finite and between -1 and 1")
    return float(value)


def _embedding(metadata):
    if not isinstance(metadata, dict) or any(key not in metadata for key in EMBEDDING_KEYS):
        raise ValueError("Policy requires complete embedding model/settings metadata")
    if any(not isinstance(metadata[key], str) or not metadata[key].strip()
           for key in EMBEDDING_KEYS[:-1]):
        raise ValueError("Embedding model/settings must be nonblank strings")
    if type(metadata["dimensions"]) is not int or metadata["dimensions"] < 1:
        raise ValueError("Embedding dimensions must be a positive integer")
    try:
        # Preserve additional runtime/truncation settings so changes cannot reuse
        # a threshold calibrated with a different embedding configuration.
        return json.loads(json.dumps(metadata, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Embedding metadata must contain finite JSON data") from error


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Calibration query IDs must be nonblank strings of at most 256 characters")


def _dataset_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Calibration dataset provenance must be a lowercase SHA-256 hash")


def _count(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"Calibration {label} must be an integer of at least {minimum}")
    return value


def _provenance(calibration):
    if (not isinstance(calibration, dict) or calibration.get("split") != "calibration"
            or calibration.get("role") != "development"):
        raise ValueError("Policy must declare development calibration provenance")
    present = _count(calibration.get("positive_queries"), "positive_queries", 1)
    absent = _count(calibration.get("absent_queries"), "absent_queries", 1)
    retained = _count(calibration.get("retained_positives"), "retained_positives")
    rejected = _count(calibration.get("rejected_absent"), "rejected_absent")
    if retained != present or rejected > absent:
        raise ValueError("Policy must retain all calibration positives and have valid rejection counts")
    identifiers, hashes = calibration.get("query_ids"), calibration.get("dataset_sha256")
    if not isinstance(identifiers, list) or len(identifiers) != present + absent:
        raise ValueError("Policy calibration query IDs must match its measured query counts")
    for identifier in identifiers:
        _identifier(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Calibration query IDs must be unique")
    if not isinstance(hashes, list) or not hashes:
        raise ValueError("Policy must declare calibration dataset hashes")
    for digest in hashes:
        _dataset_hash(digest)
    if len(set(hashes)) != len(hashes):
        raise ValueError("Calibration dataset hashes must be unique")


class MatchPolicy:
    def __init__(self, record):
        if isinstance(record, (str, Path)):
            try:
                record = json.loads(Path(record).read_text())
            except (ValueError, UnicodeError) as error:
                raise ValueError("Invalid abstention policy JSON") from error
        if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1:
            raise ValueError("Unsupported abstention policy")
        threshold = _score(record.get("threshold"))
        _embedding(record.get("embedding"))
        _provenance(record.get("calibration"))
        self.record, self.threshold = copy.deepcopy(record), threshold

    def rejects(self, score, metadata):
        score = _score(score)
        if _embedding(metadata) != _embedding(self.record["embedding"]):
            raise ValueError("Abstention policy does not match the embedding model/settings")
        return score < self.threshold


def calibrate_policy(rows, metadata):
    """Reject the most calibration negatives while retaining every positive.

    Calibration rows must be declared development/calibration. Repository holdout
    evidence cannot select this threshold. The result is an empirical decision
    policy, not a confidence or absence guarantee.
    """
    embedding = _embedding(metadata)
    rows = list(rows)
    if any(not isinstance(row, dict) or row.get("split") != "calibration"
           or row.get("role") != "development" for row in rows):
        raise ValueError("Only declared development calibration rows may fit a policy")
    identifiers = []
    for row in rows:
        if row.get("kind") not in ("present", "absent"):
            raise ValueError("Calibration query kind must be present or absent")
        _score(row.get("top_cosine"))
        _identifier(row.get("query_id"))
        _dataset_hash(row.get("dataset_sha256"))
        identifiers.append(row["query_id"])
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Calibration query IDs must be unique")
    present = [row for row in rows if row["kind"] == "present"]
    absent = [row for row in rows if row["kind"] == "absent"]
    if not present or not absent:
        raise ValueError("Calibration needs both present and absent queries")
    threshold = min(row["top_cosine"] for row in present)
    rejected = sum(row["top_cosine"] < threshold for row in absent)
    return {"version": 1, "threshold": threshold, "embedding": embedding,
            "calibration": {"role": "development", "split": "calibration",
                            "positive_queries": len(present), "absent_queries": len(absent),
                            "retained_positives": len(present), "rejected_absent": rejected,
                            "query_ids": identifiers,
                            "dataset_sha256": sorted({row["dataset_sha256"] for row in rows})},
            "meaning": MEANING}
