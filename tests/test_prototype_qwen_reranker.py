"""CPU-only fake model/tokenizer tests; no MLX import, weights or network."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/prototype_qwen_reranker.py"
spec = importlib.util.spec_from_file_location("prototype_reranker", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Tokenizer:
    def __init__(self):
        self.calls = []

    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        self.calls.append(text)
        if text == "yes":
            return [3]
        if text == "no":
            return [4]
        return list(text.encode("utf-8"))


def test_constructor_and_default_preflight_never_import_model_or_network():
    code = (
        "import runpy,sys; sys.argv=['prototype','--preflight']; "
        f"runpy.run_path({str(SCRIPT)!r},run_name='__main__'); "
        "assert 'mlx.core' not in sys.modules and 'mlx_lm' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-B", "-c", code], text=True, capture_output=True, check=True)
    output = json.loads(result.stdout)
    assert output["weights_loaded"] is False and output["network"] is False
    assert output["model_id"] == probe.MODEL_ID
    assert "mlx-lm" in output["runtime"]["packages"]
    before = set(sys.modules)
    probe.PairBuilder(Tokenizer())
    added = set(sys.modules) - before
    assert not any(name.startswith(("mlx", "huggingface")) for name in added)


def test_official_template_no_generation_and_exact_literal_unicode_source():
    tokenizer = Tokenizer()
    source = "\ufefffunction café(){return '🚀';}\r\n//a\u2028b\u2029c\rreturn 2;\n"
    built = probe.PairBuilder(tokenizer).build("Return literal Unicode", source)
    assert tokenizer.calls[-1] == f"<Instruct>: {probe.INSTRUCTION}\n<Query>: Return literal Unicode\n<Document>: {source}"
    assert built["input_ids"][-len(probe.SUFFIX.encode()):] == list(probe.SUFFIX.encode())
    assert built["source"] == source and built["delivered_lines"] == 3
    assert built["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert not built["truncated"] and built["total_tokens"] <= 8192


def test_clipping_retains_complete_exact_physical_prefix_and_all_template_tokens():
    tokenizer = Tokenizer()
    builder = probe.PairBuilder(tokenizer)
    query = "Return a value"
    first = builder.build(query, "one\r\n")
    clipped = probe.PairBuilder(tokenizer, max_tokens=first["total_tokens"]).build(query, "one\r\ntwo🚀\rthree\n")
    assert clipped["source"] == "one\r\n"
    assert clipped["delivered_lines"] == 1 and clipped["source_lines"] == 3
    assert clipped["truncated"]
    assert clipped["total_tokens"] == first["total_tokens"]
    assert clipped["delivered_sha256"] == hashlib.sha256(b"one\r\n").hexdigest()


def test_oversized_single_line_or_query_cannot_silently_become_empty_or_truncated():
    builder = probe.PairBuilder(Tokenizer())
    with pytest.raises(ValueError, match="no complete physical"):
        builder.build("query", "a" * 9000)
    with pytest.raises(ValueError, match="query leaves"):
        builder.build("a" * 9000, "source")
    with pytest.raises(ValueError):
        builder.build("", "source")
    with pytest.raises(UnicodeEncodeError):
        builder.build("query", "\ud800")


def test_nonmonotone_header_join_can_fit_an_actual_complete_source_prefix():
    class JoinTokenizer(Tokenizer):
        def encode(self, text, *, add_special_tokens):
            if text.startswith("<Instruct>"):
                return [9] if text.endswith("code") else [9] * 10000
            return super().encode(text, add_special_tokens=add_special_tokens)

    tokenizer = JoinTokenizer()
    base = probe.PairBuilder(tokenizer)
    cap = len(base.prefix) + len(base.suffix) + 1
    built = probe.PairBuilder(tokenizer, max_tokens=cap).build("q", "code")
    assert built["source"] == "code" and built["total_tokens"] == cap


def test_tokenizer_must_use_distinct_single_yes_no_tokens():
    tokenizer = Tokenizer()
    tokenizer.encode = lambda text, **kwargs: [1, 2]
    with pytest.raises(ValueError, match="single"):
        probe.PairBuilder(tokenizer)


@pytest.mark.parametrize("tied", [False, True])
def test_backbone_then_only_last_hidden_projection_unpadded(tied):
    calls = []

    class Backbone:
        embed_tokens = SimpleNamespace()

        def __call__(self, inputs):
            calls.append(("backbone", inputs.tolist()))
            assert inputs.shape == (1, 3)
            return np.arange(12).reshape(1, 3, 4)

    def project(hidden):
        calls.append(("head", hidden.copy()))
        assert hidden.shape == (1, 1, 4)
        assert hidden.tolist() == [[[8, 9, 10, 11]]]
        return np.array([[[0., 0., 0., 2., -1.]]])

    class Model:
        args = SimpleNamespace(tie_word_embeddings=tied)
        model = Backbone()
        lm_head = staticmethod(project)

        def __call__(self, inputs):
            raise AssertionError("full sequence vocab projection is forbidden")

    model = Model()
    model.model.embed_tokens.as_linear = project
    result = probe.score_pair(model, {"input_ids": [20, 21, 22]}, 3, 4, np.array)
    assert calls[0] == ("backbone", [[20, 21, 22]])
    assert len(calls) == 2 and result["logit_difference"] == 3
    assert result["yes_rank_signal"] == pytest.approx(0.9525741268)


@pytest.mark.parametrize("yes,no,expected", [(1000, -1000, 1), (-1000, 1000, 0), (0, 0, .5)])
def test_stable_binary_signal_without_temperature_threshold(yes, no, expected):
    assert probe.binary_score(yes, no)["yes_rank_signal"] == expected


def test_nonfinite_logits_rejected():
    with pytest.raises(ValueError, match="non-finite"):
        probe.binary_score(float("nan"), 0)
    with pytest.raises(ValueError, match="non-finite"):
        probe.binary_score(1e308, -1e308)


def fixture_checkpoint(tmp_path, monkeypatch):
    files = {"config.json": b'{"model_type":"qwen3"}', "tokenizer.json": b"{}",
             "tokenizer_config.json": b"{}", "model.safetensors": b"FAKE BF16"}
    monkeypatch.setattr(probe, "PINS", {name: (len(data), hashlib.sha256(data).hexdigest())
                                      for name, data in files.items()})
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    return tmp_path


def test_checkpoint_preflight_never_reads_weight_bytes(tmp_path, monkeypatch):
    root = fixture_checkpoint(tmp_path, monkeypatch)
    real_hash = probe.file_hash

    def guard(path):
        assert path.name != "model.safetensors"
        return real_hash(path)

    monkeypatch.setattr(probe, "file_hash", guard)
    result = probe.checkpoint_preflight(root)
    assert result["weight_identity_verified"] is False
    assert result["files"]["model.safetensors"]["weight_bytes_read"] is False


def test_exact_weight_identity_required_and_quantized_extra_refused(tmp_path, monkeypatch):
    root = fixture_checkpoint(tmp_path, monkeypatch)
    assert probe.checkpoint_preflight(root, verify_weights=True)["weight_identity_verified"]
    (root / "model.safetensors").write_bytes(b"BAD! BF16")
    with pytest.raises(ValueError, match="hash mismatch"):
        probe.checkpoint_preflight(root, verify_weights=True)
    (root / "model.safetensors").write_bytes(b"FAKE BF16")
    (root / "quantized.safetensors").write_bytes(b"no")
    with pytest.raises(ValueError, match="original single"):
        probe.checkpoint_preflight(root)


def test_missing_runtime_blocks_run_before_checkpoint_or_model_load(monkeypatch):
    monkeypatch.setattr(probe, "runtime_preflight", lambda: {"errors": ["absent mlx-lm"]})
    monkeypatch.setattr(probe, "checkpoint_preflight", lambda *a, **k: pytest.fail("must not read weights"))
    with pytest.raises(ValueError, match="absent mlx-lm"):
        probe.run_local("not-a-directory", [{"query": "q", "source": "s"}])


def test_cli_run_explicit_and_source_only_input(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        probe.main(["--run"])
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps([{"query": "q", "source": "s", "gold": []}]))
    monkeypatch.setattr(probe, "run_local", lambda *a: pytest.fail("gold input must reject before loading"))
    with pytest.raises(SystemExit):
        probe.main(["--run", "--checkpoint", str(tmp_path), "--pairs", str(pairs)])


def fake_runtime(monkeypatch, *, fail_at=None):
    """Exercise the complete loader/lifecycle using CPU arrays and fake modules."""
    calls = []
    mx = ModuleType("mlx.core")
    mx.array = np.array
    mx.eval = lambda *args: calls.append("evaluate")
    mx.clear_cache = lambda: calls.append("dispose")

    def load(path):
        calls.append("load weights")
        if fail_at == "load":
            raise RuntimeError("fake load failure")
        return {"lm_head.weight": 0, "model.weight": 1}

    mx.load = load
    core_parent = ModuleType("mlx")
    core_parent.core = mx
    qwen = ModuleType("mlx_lm.models.qwen3")
    qwen.ModelArgs = SimpleNamespace(from_dict=lambda config: SimpleNamespace(tie_word_embeddings=True))

    class Backbone:
        def __init__(self):
            self.embed_tokens = SimpleNamespace(as_linear=self.project)

        def __call__(self, inputs):
            calls.append(("forward", inputs.shape))
            if fail_at == "forward":
                raise KeyboardInterrupt("fake interruption")
            return np.ones((1, inputs.shape[1], 4))

        def project(self, hidden):
            assert hidden.shape == (1, 1, 4)
            return np.array([[[0., 0., 0., 2., -1.]]])

    class Model:
        def __init__(self, args):
            self.args = args
            self.model = Backbone()

        def sanitize(self, weights):
            weights.pop("lm_head.weight")
            return weights

        def load_weights(self, weights, *, strict):
            assert strict and weights == [("model.weight", 1)]
            calls.append("load strict")

        def eval(self):
            calls.append("inference mode")

        def parameters(self):
            return []

    qwen.Model = Model
    tokenizers = ModuleType("tokenizers")
    tokenizers.Tokenizer = SimpleNamespace(from_file=lambda path: Tokenizer())
    for name, module in {"mlx": core_parent, "mlx.core": mx,
                         "mlx_lm": ModuleType("mlx_lm"),
                         "mlx_lm.models": ModuleType("mlx_lm.models"),
                         "mlx_lm.models.qwen3": qwen, "tokenizers": tokenizers}.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(probe, "runtime_preflight", lambda: {"errors": [], "packages": {}})
    monkeypatch.setattr(probe, "verified_qwen3_module", lambda runtime: qwen)
    return calls


def test_full_fake_loader_sequential_pairs_exact_source_receipts_and_disposal(tmp_path, monkeypatch):
    checkpoint = fixture_checkpoint(tmp_path, monkeypatch)
    calls = fake_runtime(monkeypatch)
    result = probe.run_local(checkpoint, [{"query": "first behavior", "source": "source\r\n"},
                                          {"query": "second behavior", "source": "另\rline\n"}])
    forwards = [call for call in calls if isinstance(call, tuple)]
    assert len(forwards) == 2 and all(shape[0] == 1 for _, shape in forwards)
    assert calls.count("load weights") == 1 and calls[-1] == "dispose"
    assert result["model"]["weight_identity_verified"] is True
    assert [row["source"] for row in result["results"]] == ["source\r\n", "另\rline\n"]
    assert all("input_ids" not in row for row in result["results"])


@pytest.mark.parametrize("stage,error", [("load", RuntimeError), ("forward", KeyboardInterrupt)])
def test_fake_model_cleanup_on_load_failure_or_interruption(tmp_path, monkeypatch, stage, error):
    checkpoint = fixture_checkpoint(tmp_path, monkeypatch)
    calls = fake_runtime(monkeypatch, fail_at=stage)
    with pytest.raises(error):
        probe.run_local(checkpoint, [{"query": "a behavior", "source": "code"}])
    assert calls[-1] == "dispose"


def test_checkpoint_mutation_after_hash_is_rejected(tmp_path, monkeypatch):
    checkpoint = fixture_checkpoint(tmp_path, monkeypatch)
    identity = probe.checkpoint_preflight(checkpoint, verify_weights=True)
    (checkpoint / "tokenizer.json").write_bytes(b"[]")
    with pytest.raises(ValueError, match="changed after verification"):
        probe.verify_witnesses(identity)


def test_shadowed_package_rejected_before_any_package_code_executes(tmp_path, monkeypatch):
    expected = tmp_path / "installed/mlx_lm/models/qwen3.py"
    expected.parent.mkdir(parents=True)
    expected.write_text("safe = True\n")
    (expected.parent / "__init__.py").write_text("")
    (expected.parent.parent / "__init__.py").write_text("")
    shadow = tmp_path / "shadow/mlx_lm"
    shadow.mkdir(parents=True)
    marker = tmp_path / "executed"
    (shadow / "__init__.py").write_text(f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')")
    monkeypatch.syspath_prepend(str(shadow.parent))
    for name in ("mlx_lm", "mlx_lm.models", "mlx_lm.models.qwen3"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    with pytest.raises(ValueError, match="shadowed runtime module"):
        probe.verified_qwen3_module({"qwen3_source_path": str(expected)})
    assert not marker.exists()


def test_imported_leaf_origin_verified_again_before_model_construction(tmp_path, monkeypatch):
    root = tmp_path / "mlx_lm"
    expected = root / "models/qwen3.py"
    expected.parent.mkdir(parents=True)
    expected.write_text("safe = True\n")
    monkeypatch.setattr(probe, "QWEN3_SOURCE_SHA", hashlib.sha256(expected.read_bytes()).hexdigest())
    (expected.parent / "__init__.py").write_text("")
    (root / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("mlx_lm", "mlx_lm.models", "mlx_lm.models.qwen3"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    wrong = tmp_path / "wrong.py"
    wrong.write_text("bad = True\n")
    monkeypatch.setattr(probe.importlib, "import_module", lambda name: SimpleNamespace(__file__=str(wrong)))
    with pytest.raises(ValueError, match="imported Qwen3"):
        probe.verified_qwen3_module({"qwen3_source_path": str(expected)})
