"""CPU runner regressions for stable calibration metadata and empty selections."""
from contextlib import contextmanager
import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from semantic_search import quality


@contextmanager
def runner_fixture():
    # Reuse the public CPU-only Git/backend fixture, without collecting its test
    # class again or substituting policy fitting itself.
    path = Path(__file__).with_name("test_quality.py")
    spec = importlib.util.spec_from_file_location("quality_cpu_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case = module.QualityRunnerTests()
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def test_calibration_record_retains_actual_runtime_metadata_before_any_holdout_query():
    with runner_fixture() as case:
        original = quality.RecordingEmbedder
        metadata = {**copy.deepcopy(original.metadata), "runtime": "synthetic-cpu",
                    "runtime_settings": {"batch_size": 4, "truncation": "right"}}

        class Embedder(original):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.metadata = copy.deepcopy(metadata)

        with patch.object(quality, "RecordingEmbedder", Embedder):
            result = case.run_fixture()
        assert result["policy"]["embedding"] == metadata == result["embedding"]
        assert result["policy"]["threshold"] == .75
        assert result["policy"]["calibration"]["query_ids"] == ["calib-positive", "calib-absent"]
        assert all(not identifier.startswith("holdout") for identifier in result["policy"]["calibration"]["query_ids"])
        assert json.loads((case.output / "abstention-policy.json").read_text()) == result["policy"]
        assert case.events.index("held-out backend initialized after policy freeze") > 0


def test_changed_embedding_settings_stop_before_heldout_backend_and_close_unused_embedder():
    with runner_fixture() as case:
        original = quality.RecordingEmbedder

        class Embedder(original):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.closed = False
                self.metadata = copy.deepcopy(original.metadata)
                if len(case.embedders) > 1:
                    self.metadata["query_prompt"] = "A different model instruction"

            def close(self):
                self.closed = True

        with patch.object(quality, "RecordingEmbedder", Embedder), pytest.raises(ValueError, match="changed between quality datasets"):
            case.run_fixture()
        assert [backend.repo.name for backend in case.backends] == ["development"]
        assert case.backends[0].closed
        assert case.embedders[-1].closed and case.embedders[-1].queries == []
        assert all(repo != "holdout" for repo, _, _ in case.calls)
        assert (case.output / "abstention-policy.json").is_file()
        assert not (case.output / "results.json").exists()


def test_no_selected_datasets_has_clear_error_without_model_or_result():
    with runner_fixture() as case, pytest.raises(ValueError, match="No datasets selected"):
        try:
            case.run_fixture(limit_role="undeclared-role")
        finally:
            assert case.embedders == [] and case.backends == [] and case.calls == []
            assert not (case.output / "results.json").exists()
            assert not (case.output / "abstention-policy.json").exists()
