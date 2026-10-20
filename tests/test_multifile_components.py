"""Model-free checks for retrospective component-stage attribution."""
import hashlib
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diagnose_multifile_components as diagnostic
from diagnose_multifile import STAGES


def window(name):
    return {"id": name, "path": name + ".py", "start_line": 1, "end_line": 2}


def gold(name):
    return [{"path": name + ".py", "evidence_lines": [1, 2]}]


def test_absent_component_merge_loss_and_dependency_recovery_are_distinct():
    dense, clause, recovered = window("dense"), window("clause"), window("recovered")
    stages = {name: [] for name in STAGES}
    for name in ("retrieved", "ranked", "deduplicated", "eligible", "anchors", "expanded"):
        stages[name] = [clause]
    for name in ("expanded", "character_packed", "token_packed"):
        stages[name].append(recovered)
    result = diagnostic.component_groups([dense], [clause], [clause], stages,
                                         [gold("absent"), gold("dense"), gold("clause"), gold("recovered")])
    assert [row["outcome"] for row in result] == [
        "absent_from_all_components", "discarded_by_merge", "downstream_character_clipping", "delivered"]
    assert result[1]["ranks"]["dense"] == 1 and result[1]["ranks"]["merged"] is None
    assert result[3]["ranks"]["dense"] is None and result[3]["ranks"]["delivered"] == 1


@pytest.mark.parametrize("last_stage,loss", [
    ("ranked", "deduplication"), ("deduplicated", "result_cap"),
    ("eligible", "dependency_displacement"), ("expanded", "character_clipping"),
    ("character_packed", "token_clipping"),
])
def test_actual_downstream_loss_is_preserved(last_stage, loss):
    item = window("required")
    stop = STAGES.index(last_stage)
    stages = {stage: [item] if index <= stop else [] for index, stage in enumerate(STAGES)}
    result = diagnostic.component_groups([], [item], [item], stages, [gold("required")])[0]
    assert result["outcome"] == "downstream_" + loss
    assert result["actual_stage_outcome"] == loss


def test_component_credit_requires_one_complete_evidence_span():
    partial = {**window("required"), "end_line": 1}
    assert diagnostic.ranks([partial], [gold("required")]) == [None]
    assert diagnostic.ranks([window("required")], [gold("required")]) == [1]


def test_saved_query_vector_is_float32_and_sha_bound_without_inference():
    vector = np.asarray([.6, .8], dtype=np.float32)
    row = {"query_vector": vector.tolist(), "query_vector_sha256": hashlib.sha256(vector.tobytes()).hexdigest()}
    assert diagnostic.query_vector(row).dtype == np.float32
    np.testing.assert_array_equal(diagnostic.query_vector(row), vector)
    with pytest.raises(ValueError, match="SHA"):
        diagnostic.query_vector({**row, "query_vector": [.8, .6]})
    with pytest.raises(ValueError, match="SHA"):
        diagnostic.query_vector({**row, "query_vector": [[.6, .8]]})


def test_component_source_uses_exact_saved_physical_bytes_and_bounds():
    text = "first();\r\nsecond();\r"
    sources = {"required.py": ["first();\r\n", "second();\r"]}
    row = {**window("required"), "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()}
    diagnostic.verify_window(row, sources)
    diagnostic.verify_window({**window("required"), "source": text, "source_hash": row["content_hash"]}, sources)
    for changed in ({"start_line": 0}, {"end_line": 3}, {"text": "changed"}, {"content_hash": "bad"}):
        with pytest.raises(ValueError):
            diagnostic.verify_window({**row, **changed}, sources)
