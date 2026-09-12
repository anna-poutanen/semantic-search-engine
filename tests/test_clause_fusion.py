"""Source-only selector and exact production packer checks; no real models/network."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    "clause_development", Path(__file__).parents[1] / "scripts/run_clause_fusion.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_literal_full_query_and_first_three_eligible_clauses_only():
    query = "  Find one valid alpha implementation and then locate the second related routine while preserve all stable source bytes before confirm the final error behavior then inspect yet another unsupported part  "
    assert probe.clause_inputs(query) == [
        query,
        "Find one valid alpha implementation",
        "locate the second related routine",
        "preserve all stable source bytes",
    ]


def test_boundary_longest_separator_and_no_arbitrary_and_split():
    query = "Find source_that_then_stays stable and return complete content beforehand"
    assert probe.clause_inputs(query) == [query]
    query = (
        "Find all saved implementations AND THEN inspect each captured source header"
    )
    assert probe.clause_inputs(query) == [
        query,
        "Find all saved implementations",
        "inspect each captured source header",
    ]
    assert all(
        not clause.startswith("AND ") for clause in probe.clause_inputs(query)[1:]
    )


def test_deduplication_short_empty_clauses_and_casefold():
    query = "find all saved source while FIND   ALL saved SOURCE then too short before then find all saved source"
    assert probe.clause_inputs(query) == [query, "find all saved source"]
    assert probe.clause_inputs("four ordinary query words") == [
        "four ordinary query words"
    ]
    with pytest.raises(ValueError):
        probe.clause_inputs("  ")


def rows(*ids):
    return [
        {
            "id": identifier,
            "source": "literal implementation",
            "path": "unrelated/file.ts",
        }
        for identifier in ids
    ]


def test_equal_rrf_exact_rational_ties_prefer_full_rank_then_stable_id():
    result = probe.fuse_rankings([rows("z", "a"), rows("a", "z")])
    assert [row["id"] for row in result] == ["z", "a"]
    assert result[0]["fusion_exact"] == str(Fraction(1, 61) + Fraction(1, 62))
    assert result[0]["fusion_priority"] == result[1]["fusion_priority"]
    result = probe.fuse_rankings([[], rows("z", "a"), rows("a", "z")])
    assert [row["id"] for row in result] == ["a", "z"]


def test_global_cap_uses_all_lists_without_per_clause_allocations():
    full = rows(*(f"full-{number:02}" for number in range(50)))
    second = rows(*(f"other-{number:02}" for number in range(50)))
    third = rows("other-49", *[f"new-{number:02}" for number in range(49)])
    merged = probe.fuse_rankings([full, second, third])
    assert len(merged) == 50
    assert merged[0]["id"] == "other-49"  # Two ranks beat one, no gold/name rule.
    with pytest.raises(ValueError, match="depth"):
        probe.fuse_rankings([rows(*(str(number) for number in range(51)))])
    with pytest.raises(ValueError, match="duplicate"):
        probe.fuse_rankings([rows("duplicate", "duplicate")])
    with pytest.raises(ValueError):
        probe.fuse_rankings([[]] * 5)


def test_gold_and_raw_cosine_metamorphisms_cannot_change_selector_order():
    raw = [rows("a", "b"), rows("b", "c")]
    mutated = deepcopy(raw)
    for ranklist in mutated:
        for index, row in enumerate(ranklist):
            row.update(
                kind="absent" if index else "present",
                targets=[{"path": row["id"]}],
                evidence_rank=0,
                retrieval_score=1e99 if index else -1e99,
                source="NO TARGET HERE",
            )

    def key(result):
        return [(row["id"], row["fusion_exact"]) for row in result]

    assert key(probe.fuse_rankings(raw)) == key(probe.fuse_rankings(mutated))

    class TextOnly(dict):
        def __getitem__(self, key):
            assert key in ("id", "query"), "Gold/role accessed before selector"
            return super().__getitem__(key)

    query = TextOnly(
        id="query",
        query="find a saved source implementation",
        kind="absent",
        target_groups="POISON",
    )
    entry = TextOnly(id="fixture", role="POISON")
    catalog = probe.query_catalog([(entry, {"queries": [query]}, {"secret": "source"})])
    assert set(catalog[0]) == {"dataset", "query_id", "query", "control_inputs"}


def test_frozen_schedule_has_all_sixty_unique_identities_alternating_order():
    catalog = [
        {"dataset": "express" if number < 15 else "zod", "query_id": str(number)}
        for number in range(30)
    ]
    schedule = probe.schedule(catalog)
    assert len(schedule) == len({tuple(row.values()) for row in schedule}) == 60
    assert schedule[0]["condition"] == schedule[3]["condition"] == "full_query_dense"
    assert all(row["dataset"] == "express" for row in schedule[:30])


def test_freeze_metadata_can_survive_plan_commit_but_invariant_edits_cannot():
    current = {
        "settings": deepcopy(probe.SETTINGS),
        "source_sha256": {"file.py": "abc"},
    }
    frozen = {
        **deepcopy(current),
        "source_commit": "a" * 40,
        "frozen_at_utc": "2026-10-13T12:00:00+00:00",
    }
    probe.validate_plan(current, frozen)
    frozen["settings"]["max_results"] = 9
    with pytest.raises(ValueError, match="settings"):
        probe.validate_plan(current, frozen)


def test_offline_tokenizer_missing_and_corrupt_guards(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        probe.tokenizer_cache(tmp_path)
    (tmp_path / probe.CACHE_KEY).write_bytes(b"wrong cache")
    with pytest.raises(ValueError, match="corrupt"):
        probe.tokenizer_cache(tmp_path)


@pytest.fixture
def verified_cache(tmp_path):
    original = Path(__file__).parents[1] / ".cache/tokenizers" / probe.CACHE_KEY
    if not original.is_file():
        pytest.skip(
            "Offline exact-encoder test requires preverified cl100k cache; no downloads or prior-test dependency"
        )
    raw = original.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == probe.CACHE_SHA256
    target = tmp_path / "private-tokenizer-cache"
    target.mkdir()
    (target / probe.CACHE_KEY).write_bytes(raw)
    return target


def test_tokenizer_capture_restores_reader_and_rejects_unexpected_request_without_real_cache(
    monkeypatch,
):
    import semantic_search.context
    import tiktoken.load

    raw = b"CPU sentinel bytes, no real encoder"
    monkeypatch.setattr(
        probe, "tokenizer_cache", lambda cache=None: (raw, {"fixture": True})
    )

    class SentinelEncoding:
        def cache_clear(self):
            pass

        def __call__(self):
            assert (
                tiktoken.load.read_file_cached(probe.CACHE_URL, probe.CACHE_SHA256)
                == raw
            )

    monkeypatch.setattr(semantic_search.context, "encoding", SentinelEncoding())
    original = tiktoken.load.read_file_cached
    with probe.pinned_tokenizer():
        assert (
            tiktoken.load.read_file_cached(probe.CACHE_URL, probe.CACHE_SHA256) == raw
        )
        with pytest.raises(ValueError, match="Unexpected"):
            tiktoken.load.read_file_cached(
                "https://example.invalid", probe.CACHE_SHA256
            )
        with pytest.raises(ValueError, match="Unexpected"):
            tiktoken.load.read_file_cached(probe.CACHE_URL, "0" * 64)
    assert tiktoken.load.read_file_cached is original


def test_real_private_cache_capture_is_immutable_and_independent_of_inherited_directory(
    verified_cache, monkeypatch
):
    import tiktoken.load

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(verified_cache / "missing-inherited"))
    raw = (verified_cache / probe.CACHE_KEY).read_bytes()
    with probe.pinned_tokenizer(verified_cache):
        (verified_cache / probe.CACHE_KEY).write_bytes(
            b"changed privately after capture"
        )
        assert (
            tiktoken.load.read_file_cached(probe.CACHE_URL, probe.CACHE_SHA256) == raw
        )


def test_cpu_checkpoint_inventory_never_reads_weight_bytes(tmp_path, monkeypatch):
    import semantic_search.embeddings

    monkeypatch.setattr(semantic_search.embeddings, "DEFAULT_MODEL_PATH", tmp_path)
    (tmp_path / "model.safetensors").write_bytes(b"CPU fake weights")
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "semantic_search_checkpoint.json").write_text(
        json.dumps({"weights_etag": "a" * 64})
    )
    original = Path.read_bytes

    def reader(path):
        assert path.suffix != ".safetensors", "Weight bytes read during CPU freeze"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reader)
    inventory = probe.checkpoint_inventory()
    assert inventory["model.safetensors"]["expected_sha256_from_provenance"] == "a" * 64
    assert inventory["model.safetensors"]["bytes"] == 16


class FakeEmbedder:
    metadata = {"id": "clause-cpu", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self.documents, self.queries = [], []
        self.last = None

    def embed_documents(self, texts):
        self.documents.append(list(texts))
        return np.asarray([[1.0, 0.1 + ("func_8" in text)] for text in texts])

    def embed_query(self, text):
        self.queries.append(text)
        self.last = np.asarray([1.0, 0.0])
        return self.last

    def counts(self):
        return {
            "completed_query_calls": len(self.queries),
            "completed_document_calls": len(self.documents),
            "observed_query_inputs": list(self.queries),
        }

    def close(self):
        pass


@pytest.fixture
def backend(tmp_path, verified_cache):
    from semantic_search.backend import SearchBackend

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    source = "\n".join(
        f"def func_{number}():\n"
        + "".join(
            f'    item_{line} = "word {line} phrase code number {number}"\n'
            for line in range(35)
        )
        + f"    return {number}\n"
        for number in range(10)
    )
    (repo / "src/code.py").write_text(source)
    embedder = FakeEmbedder()
    instance = SearchBackend(repo, "src", tmp_path / "index.sqlite3", embedder=embedder)
    with probe.pinned_tokenizer(verified_cache):
        instance.refresh()
        try:
            yield instance, embedder, {"src/code.py": source}
        finally:
            instance.close()


def test_precomputed_pack_matches_unmodified_backend_and_exact_shared_caps(backend):
    from semantic_search.context import compact_result, encoding, serialize_context

    instance, embedder, _ = backend
    ordinary = instance.search(
        "find all related source implementations", retrieval="dense", **probe.SETTINGS
    )
    vector = embedder.last
    pairs = instance._dense.search(vector, 50)
    before = embedder.counts()
    packed = probe.pack_candidates(
        instance,
        ordinary["query"],
        vector,
        pairs,
        top_full_cosine=pairs[0][1],
        score_kind="cosine",
    )
    assert compact_result(packed) == compact_result(ordinary)
    assert embedder.counts() == before
    assert len(packed["results"]) <= 8
    assert packed["total_source_chars"] <= 12000
    actual = len(
        encoding().encode(
            serialize_context(compact_result(packed)), disallowed_special=()
        )
    )
    assert actual == packed["token_usage"]["tokens"] <= 4096
    assert instance._embedder is embedder


def test_fresh_calls_vector_reuse_and_immutable_observed_snapshots(backend):
    instance, embedder, sources = backend
    helper = probe.language_helpers()
    query = "find the first stable source behavior then inspect the second related source behavior"
    baseline, control = {}, {}
    probe.run_condition(
        instance, embedder, query, "full_query_dense", helper, sources, baseline
    )
    after_baseline = deepcopy(baseline["counts_after"])
    probe.run_condition(
        instance, embedder, query, "clause_rrf", helper, sources, control
    )
    assert baseline["counts_after"] == after_baseline
    assert (
        control["counts_after"]["completed_query_calls"]
        - control["counts_before"]["completed_query_calls"]
        == 3
    )
    assert embedder.queries == [query, *probe.clause_inputs(query)]
    assert len(embedder.documents) == 1
    assert len(control["raw_ranked_source_lists"]) == 3
    assert (
        control["response"]["match_evidence"]["ranking_score_kind"]
        == "equal_weight_rrf_priority_k60"
    )
    assert (
        control["response"]["match_evidence"]["top_cosine"]
        > control["response"]["results"][0]["retrieval_score"]
    )
    assert control["source_and_shared_caps_verified"]


def test_pack_restores_backend_adapters_after_source_change_failure(backend):
    instance, embedder, _ = backend
    vector = embedder.embed_query("find saved source")
    pairs = instance._dense.search(vector, 50)
    original_dense = instance._dense
    (instance.repo / "src/code.py").write_text("def changed():\n    return 0\n")
    with pytest.raises(ValueError, match="document inference"):
        probe.pack_candidates(
            instance,
            "find saved source",
            vector,
            pairs,
            top_full_cosine=0.9,
            score_kind="cosine",
        )
    assert instance._embedder is embedder and instance._dense is original_dense


def test_partial_summary_keeps_failure_without_inflating_completed_quality():
    frozen = {
        "classification": "development",
        "limitations": [],
        "queries": [{"dataset": "express", "query_id": "q"}],
    }
    row = {
        "identity": {"dataset": "express", "query_id": "q", "condition": "clause_rrf"},
        "status": "failed",
    }
    result = probe.summarize([row], frozen)
    assert (
        not result["complete"] and result["attempted"] == 1 and result["completed"] == 0
    )
    assert result["unique_query_denominator"] == 30 and result["failed"] == [
        row["identity"]
    ]
    assert result["datasets"]["express"]["clause_rrf"]["present_queries"] == 0


def test_interrupt_retains_attempted_prefix_actual_counters_and_prevents_retry(
    tmp_path, monkeypatch
):
    from contextlib import nullcontext
    import semantic_search.embeddings

    helper = probe.language_helpers()
    repo = tmp_path / "repository"
    (repo / "src").mkdir(parents=True)
    (repo / "src/code.py").write_text("def alpha():\n    return 1\n")
    output = tmp_path / "output"
    output.mkdir()
    query = {
        "id": "q",
        "query": "find one authored source behavior",
        "kind": "present",
        "targets": [],
    }
    entry = {
        "id": "express",
        "corpus": str(repo),
        "source_root": "src",
        "source_exclusions": [],
    }
    identity = {"dataset": "express", "query_id": "q", "condition": "full_query_dense"}
    current = {
        "source_sha256": {"fixture": "bound"},
        "checkpoint": {},
        "schedule": [identity],
        "classification": "CPU interruption fixture",
        "limitations": [],
        "queries": [{"dataset": "express", "query_id": "q"}],
    }
    frozen = {
        **deepcopy(current),
        "frozen_at_utc": "2026-10-13T12:00:00+00:00",
        "source_commit": "a" * 40,
    }
    (output / "frozen-plan.json").write_text(json.dumps(frozen))
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "language_helpers", lambda: helper)
    monkeypatch.setattr(
        probe,
        "freeze_inputs",
        lambda: ([(entry, {"queries": [query]}, {})], {}, deepcopy(current)),
    )
    monkeypatch.setattr(probe, "module_origins", lambda: {})
    monkeypatch.setattr(probe, "source_hashes", lambda: {"fixture": "bound"})
    monkeypatch.setattr(
        probe, "tokenizer_cache", lambda cache=None: (b"sentinel", {"fixture": True})
    )
    monkeypatch.setattr(probe, "pinned_tokenizer", lambda: nullcontext())
    monkeypatch.setattr(helper, "footprint", lambda: {"fixture": True})

    def document_fixture(self, texts, *, cancel_check=None):
        return np.ones((len(texts), 1024), dtype=np.float32) / 32

    def interrupted_query(self, text):
        raise KeyboardInterrupt("CPU fake interruption before any query vector")

    monkeypatch.setattr(
        semantic_search.embeddings.QwenEmbedder, "embed_documents", document_fixture
    )
    monkeypatch.setattr(
        semantic_search.embeddings.QwenEmbedder, "embed_query", interrupted_query
    )
    with pytest.raises(KeyboardInterrupt):
        probe.run(output)
    row = json.loads((output / "attempts.jsonl").read_text())
    assert row["status"] == "failed" and row["error_type"] == "KeyboardInterrupt"
    assert row["counts_before"]["attempted_query_calls"] == 0
    assert row["counts_after"]["attempted_query_calls"] == 1
    assert row["counts_after"]["completed_query_calls"] == 0
    assert row["counts_after"]["observed_query_inputs"] == [query["query"]]
    assert json.loads((output / "failure.json").read_text())["retry"] is False
    assert (
        json.loads((output / "setups.json").read_text())[0]["after_close"][
            "weights_resident"
        ]
        is False
    )
    with pytest.raises(ValueError, match="retry"):
        probe.run(output)
