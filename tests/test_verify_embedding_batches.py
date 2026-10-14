"""CPU-only contracts for real-forward qualification; no local model loading."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from semantic_search.corpus import Snippet
from semantic_search.embeddings import DIMENSIONS
from semantic_search.index import document_text

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_embedding_batches_under_test", ROOT / "scripts/verify_embedding_batches.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def snippet(index, text):
    return Snippet(id=str(index), path=f"django/source{index}.py", symbol="behavior", start_line=1,
                   end_line=1, text=text, content_hash=hashlib.sha256(text.encode()).hexdigest())


def chosen():
    lengths = [3500, 1000, 3800, 1100]
    return [(snippet(index, f"source{index}\n"), [index + 1] * length) for index, length in enumerate(lengths)]


class Tokenizer:
    def __init__(self, windows):
        self.mapping = {document_text(item): ids for item, ids in windows}
        self.requests = []

    def __call__(self, texts, **kwargs):
        self.requests.append((texts, kwargs))
        return {"input_ids": [self.mapping[text] for text in texts]}


def test_real_length_selection_keeps_original_text_and_interleaves_lengths():
    windows = chosen()
    tokenizer = Tokenizer(windows)
    selected, summary = driver.choose_windows([item for item, _ in windows], tokenizer, candidate_limit=8)
    assert [len(ids) for _, ids in selected] == [3800, 1000, 3500, 1100]
    assert [item.text for item, _ in selected] == [windows[index][0].text for index in [2, 1, 0, 3]]
    assert summary["tokenized_candidates"] == 4
    assert tokenizer.requests[0][1] == {"padding": False, "truncation": True, "max_length": 8192, "add_special_tokens": True}


def test_selection_refuses_a_short_sample_that_would_not_exercise_variable_batches():
    windows = [(snippet(index, f"source{index}\n"), [index + 1] * 1000) for index in range(4)]
    with pytest.raises(ValueError, match="lacks"):
        driver.choose_windows([item for item, _ in windows], Tokenizer(windows), candidate_limit=8)


def test_physical_source_check_reads_exact_saved_crlf_and_hash(tmp_path):
    source = "return value\r\n"
    item = snippet(0, source)
    path = tmp_path / item.path
    path.parent.mkdir()
    path.write_bytes(source.encode())
    driver.physical_source_check(tmp_path, item)
    path.write_bytes(b"return another_value\r\n")
    with pytest.raises(ValueError, match="changed"):
        driver.physical_source_check(tmp_path, item)


class FakeEmbedder:
    def __init__(self, windows, *, oversized=False, wrong_order=False, changed_ids=False):
        self._tokenizer = Tokenizer(windows)
        self.clears = self.model_calls = 0
        self._mx = SimpleNamespace(clear_cache=self.clear)
        self.oversized = oversized
        self.wrong_order = wrong_order
        self.changed_ids = changed_ids

    def clear(self):
        self.clears += 1

    def _ensure_loaded(self):
        pass

    def _embed_batch(self, ids):
        self.model_calls += 1
        rows = np.zeros((len(ids), DIMENSIONS), dtype=np.float32)
        for index, tokens in enumerate(ids):
            rows[index, tokens[0]] = 1
        return rows

    def embed_documents(self, texts):
        ids = self._tokenizer(texts)["input_ids"]
        if self.changed_ids:
            ids = [[token + 10 for token in row] for row in ids]
        order = sorted(range(len(ids)), key=lambda index: (len(ids[index]), index))
        groups, group = [], []
        for index in order:
            if group and not self.oversized and max(len(ids[index]), max(len(ids[item]) for item in group)) * (len(group) + 1) > 8192:
                groups.append(group)
                group = []
            group.append(index)
        if group:
            groups.append(group)
        output = np.empty((len(ids), DIMENSIONS), dtype=np.float32)
        for indices in groups:
            output[indices] = self._embed_batch([ids[index] for index in indices])
        if self.wrong_order and len(texts) > 1:
            output = output[::-1]
        return output


def test_forward_observer_checks_real_inputs_groups_and_four_fresh_singletons():
    windows = chosen()
    embedder = FakeEmbedder(windows)
    original = embedder._embed_batch
    receipt = driver.model_parity(embedder, windows)
    assert receipt["adaptive_forward_count"] == 2 and receipt["singleton_forward_count"] == 4
    assert embedder.model_calls == 6 and embedder.clears == 3
    assert [row["input_positions"] for row in receipt["actual_forward_batches"]] == [[1, 3], [0, 2], [0], [1], [2], [3]]
    assert all(row["padded_tokens"] <= 8192 for row in receipt["actual_forward_batches"])
    assert receipt["comparison"]["minimum_cosine"] == 1
    assert receipt["comparison"]["maximum_absolute_difference"] == 0
    assert embedder._embed_batch == original


@pytest.mark.parametrize("kind", ["oversized", "wrong_order", "changed_ids"])
def test_false_batch_or_input_or_output_order_proofs_fail_and_restore_the_observer(kind):
    windows = chosen()
    embedder = FakeEmbedder(windows, **{kind: True})
    original = embedder._embed_batch
    with pytest.raises(ValueError):
        driver.model_parity(embedder, windows)
    assert embedder._embed_batch == original
    if kind in ("oversized", "changed_ids"):
        assert embedder.model_calls == 0


@pytest.mark.parametrize("kind", ["nonfinite", "zero", "dimension", "difference", "range"])
def test_parity_comparison_requires_finite_unit_vectors_dimensions_and_close_values(kind):
    adaptive = np.zeros((4, DIMENSIONS), dtype=np.float32)
    adaptive[:, 0] = 1
    singleton = adaptive.copy()
    if kind == "nonfinite":
        singleton[0, 0] = np.nan
    elif kind == "zero":
        singleton[0] = 0
    elif kind == "dimension":
        singleton = singleton[:, :2]
    elif kind == "difference":
        singleton[0, 0] = 0
        singleton[0, 1] = 1
    elif kind == "range":
        singleton[0, 0] = 1.1
    with pytest.raises(ValueError):
        driver.compare_vectors(adaptive, singleton)
