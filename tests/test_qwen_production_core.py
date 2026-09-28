"""CPU source parity with the immutable original experiment; no MLX imports."""
import ast
import importlib.util
from pathlib import Path
import sys

from semantic_search import qwen_reranker_core as core


def original_path():
    for parent in Path(__file__).resolve().parents:
        path = parent / "scripts/prototype_qwen_reranker.py"
        if path.is_file():
            return path
    raise AssertionError("Original prototype source unavailable")


def test_promoted_builder_scoring_and_identity_are_exact_original_ast():
    original = ast.parse(original_path().read_text())
    promoted = ast.parse(Path(core.__file__).read_text())
    def inventory(tree):
        return {node.name: ast.dump(node, include_attributes=False) for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    before, after = inventory(original), inventory(promoted)
    for name in after:
        assert before[name] == after[name], name
    spec = importlib.util.spec_from_file_location("original_cpu_qwen", original_path())
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    for name in ("MODEL_ID", "REVISION", "MAX_TOKENS", "INSTRUCTION", "PREFIX", "SUFFIX",
                 "PINS", "RUNTIME_PINS", "QWEN3_SOURCE_SHA"):
        assert getattr(core, name) == getattr(old, name)
    assert "mlx_lm.models.qwen3" not in sys.modules


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return [10001 if text == "yes" else 10002] if text in {"yes", "no"} else [ord(char) for char in text]


def test_literal_query_unicode_and_whole_physical_line_clipping():
    builder = core.PairBuilder(CharacterTokenizer())
    query = "return café, ignoring\u2028names"
    source = "def café():\r\n    return '☃'\n\u2028literal\r"
    full = builder.build(query, source)
    assert full["source"] == source and full["source_lines"] == 3
    small = core.PairBuilder(CharacterTokenizer(), max_tokens=full["total_tokens"] - len("\u2028literal\r"))
    clipped = small.build(query, source)
    assert clipped["source"] == "def café():\r\n    return '☃'\n"
    assert clipped["query_sha256"] == core.sha256(query.encode())
    assert clipped["truncated"] and clipped["source_sha256"] == full["source_sha256"]


def test_binary_math_rejects_nonfinite_and_saturating_probability_keeps_difference():
    import pytest
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            core.binary_score(bad, 0)
    first, second = core.binary_score(1000, 0), core.binary_score(1001, 0)
    assert first["yes_rank_signal"] == second["yes_rank_signal"] == 1
    assert first["logit_difference"] < second["logit_difference"]


def test_native_support_preflight_checks_bytes_without_importing_runtime(tmp_path, monkeypatch):
    from semantic_search import qwen_worker
    (tmp_path / "qwen3.py").write_text("cpu fixture")
    pins = {name: core.sha256(name.encode()) for name in ("base.py", "rope_utils.py")}
    for name in pins:
        (tmp_path / name).write_text(name)
    monkeypatch.setattr(qwen_worker, "SUPPORTING_SOURCE_SHA256", pins)
    monkeypatch.setattr(core, "runtime_preflight", lambda: {
        "errors": [], "qwen3_source_path": str(tmp_path / "qwen3.py")})
    assert qwen_worker.runtime_preflight()["errors"] == []
    (tmp_path / "base.py").write_text("altered source")
    assert qwen_worker.runtime_preflight()["errors"] == ["Pinned native support source differs: base.py"]


def test_native_support_module_origin_and_postimport_bytes_are_verified(tmp_path, monkeypatch):
    from semantic_search import qwen_worker
    from types import SimpleNamespace
    pins = {name: core.sha256(name.encode()) for name in ("base.py", "rope_utils.py")}
    monkeypatch.setattr(qwen_worker, "SUPPORTING_SOURCE_SHA256", pins)
    for name in pins:
        path = tmp_path / name
        path.write_text(name)
        monkeypatch.setitem(sys.modules, "mlx_lm.models." + name.removesuffix(".py"),
                            SimpleNamespace(__file__=str(path)))
    runtime = {"qwen3_source_path": str(tmp_path / "qwen3.py")}
    qwen_worker.verify_supporting_modules(runtime)
    (tmp_path / "rope_utils.py").write_text("changed after import")
    import pytest
    with pytest.raises(ValueError, match="origin/bytes differ"):
        qwen_worker.verify_supporting_modules(runtime)
