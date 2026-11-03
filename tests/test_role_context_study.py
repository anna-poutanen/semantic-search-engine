"""Invented CPU role-study contracts; no observed guard/model outcomes."""
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from semantic_search.context import apply_token_budget, compact_result, serialize_context


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("role_study_test", ROOT / "scripts/run_role_context.py")
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)
QUERY = "Find invented allocation and publish invented values"


class Counter:
    def encode(self, text, **kwargs):
        return list(text)


def row(name):
    source = f"def {name}():\n    return None\n"
    return {"id": name, "path": f"invented/{name}.py", "symbol": name,
            "start_line": 1, "end_line": 2, "window_end_line": 2, "source": source,
            "content_hash": study.digest(source.encode()), "source_hash": study.digest(source.encode()),
            "truncated": False, "origin": "retrieval", "related_to": None,
            "retrieval_score": 0.5, "h2o_score": None, "qwen_score": None}


def response(rows):
    with patch("semantic_search.context.encoding", return_value=Counter()):
        return apply_token_budget({"query": QUERY, "results": rows, "match_status": "candidates",
            "total_source_chars": sum(len(value["source"]) for value in rows), "budget_exhausted": False}, 4096)


def fixture():
    cases, proposals, controls, complementary = [], [], [], []
    for index, condition in enumerate(("dense", "hybrid")):
        reference, proposal = response([row("a")]), response([row("a"), row("b")])
        identity = {"subset": "invented", "dataset": "invented", "query_id": "two_parts", "condition": condition}
        key = [identity[name] for name in ("subset", "dataset", "query_id", "condition")]
        refs = {name: {"path": "invented/controls.jsonl", "line_number": 2 * index + step,
                    "record_sha256": "a" * 64, "response_sha256": "b" * 64, "compact_sha256": "c" * 64}
                for step, name in enumerate(("legacy", "coverage_control"), 1)}
        case = {"case_id": f"invented_case_{index}", "key": key, **identity, "query": QUERY,
            "kind": "present", "multi_file": True, "qualified": False,
            "groups": [[{"path": "invented/a.py", "evidence_lines": [1, 2]}],
                       [{"path": "invented/b.py", "evidence_lines": [1, 2]}]],
            "reference_response": reference, "legacy_control": deepcopy(reference),
            "raw_rows": [row("a"), row("b")], "precoalesce_rows": [row("a")],
            "definition_keys": {name: [f"invented/{name}.py", name, 1, 2] for name in ("a", "b")},
            "limits": dict(study.LIMITS), "control_references": refs}
        payload = serialize_context(compact_result(proposal))
        saved = {"case_id": case["case_id"], "key": key, **identity, "reference_response": deepcopy(reference),
            "definition_keys": deepcopy(case["definition_keys"]), "limits": dict(study.LIMITS), "response": proposal,
            "compact_payload": payload, "compact_sha256": study.digest(payload.encode())}
        cases.append(case)
        proposals.append(saved)
        controls.append({"case_id": case["case_id"], **identity, "controls": refs})
        complementary.append({**identity, "response": deepcopy(reference), "compact_sha256": "d" * 64})
    negatives = [{"path": "invented/old-negative.jsonl", "line_number": 1, "condition": "invented_negative",
                  "record_sha256": "e" * 64, "response_sha256": "f" * 64, "compact_sha256": "0" * 64}]
    return cases, proposals, controls, negatives, complementary


def prepared_fixture():
    cases, proposals, controls, negatives, complementary = fixture()
    documents = study.build_inputs(cases, proposals, controls, negatives, complementary,
        expected_rows=2, expected_units=1, expected_old_negatives=1)
    return {"cases": cases, "parent_proposals": {row["case_id"]: row for row in proposals},
            "proposal_references": {row["case_id"]: row for row in documents["parent-outcome-references.json"]},
            "input_manifest": documents["input-manifest.json"], "controls": controls,
            "negatives": documents["negative-references.json"]}


def accepted(reference, proposal, **kwargs):
    return deepcopy(proposal), {"accepted": True, "fallback_reason": None}


class RoleContextStudyTests(unittest.TestCase):
    def test_complete_nonoverlapping_control_proposal_and_negative_references(self):
        values = fixture()
        before = deepcopy(values)
        docs = study.build_inputs(*values, expected_rows=2, expected_units=1, expected_old_negatives=1)
        self.assertEqual(values, before)
        self.assertEqual(len(docs["negative-references.json"]), 5)
        self.assertEqual(len(docs["control-references.json"]), 2)
        self.assertEqual(len(docs["parent-outcome-references.json"]), 2)
        self.assertEqual(docs["input-manifest.json"]["new_guard_outcomes_computed"], 0)
        for case, proposal, binding in zip(values[0], values[1], docs["input-manifest.json"]["cases"]):
            self.assertEqual(binding["case_sha256"], study.digest(study.canonical(case)))
            self.assertEqual(binding["saved_v1_proposal_sha256"], study.digest(study.canonical(proposal["response"])))

    def test_missing_or_changed_original_matrix_rejected(self):
        for wrong in ("case", "proposal", "control", "negative", "complementary", "compact", "identity"):
            with self.subTest(wrong=wrong):
                cases, proposals, controls, negatives, complementary = fixture()
                if wrong == "case":
                    cases.pop()
                elif wrong == "proposal":
                    proposals[1]["case_id"] = proposals[0]["case_id"]
                elif wrong == "control":
                    controls[0]["controls"] = {"legacy": {}}
                elif wrong == "negative":
                    negatives.clear()
                elif wrong == "complementary":
                    complementary[0]["condition"] = "changed"
                elif wrong == "compact":
                    proposals[0]["compact_sha256"] = "0" * 64
                else:
                    proposals[0]["key"] = ["changed"]
                with self.assertRaises(ValueError):
                    study.build_inputs(cases, proposals, controls, negatives, complementary,
                        expected_rows=2, expected_units=1, expected_old_negatives=1)

    def test_duplicate_negative_source_locations_rejected(self):
        cases, proposals, controls, negatives, complementary = fixture()
        negatives[0].update(path=study.COMPLEMENTARY + "/replay.jsonl.gz", line_number=1)
        with self.assertRaisesRegex(ValueError, "overlap"):
            study.build_inputs(cases, proposals, controls, negatives, complementary,
                expected_rows=2, expected_units=1, expected_old_negatives=1)

    def test_replay_calls_terminal_guard_once_and_reuses_original_proposal(self):
        prepared = prepared_fixture()
        before = deepcopy(prepared)
        calls = []

        def guard(reference, proposal, *, definition_keys):
            calls.append((deepcopy(reference), deepcopy(proposal), deepcopy(definition_keys)))
            reference["query"] = "mutated local copy"
            return accepted(reference, proposal)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "replay"
            summary = study.replay(prepared, output, binding={}, counter=Counter(), guard=guard)
            rows = study.jsonl(output / "outcomes.jsonl.gz")
            self.assertEqual(len(calls), 2)
            self.assertEqual(prepared, before)
            self.assertEqual(summary["candidate_phase_calls"], {"role_guard": 2, **{name: 0 for name in
                ("current_v1", "model", "retrieval", "index", "coalesce", "backfill")}})
            self.assertTrue(summary["quality_gate"]["qualified"])
            self.assertEqual(summary["quality_gate"]["additional_distinct_complete_multi_queries_vs_coverage"],
                             [["invented", "two_parts"]])
            self.assertEqual(summary["role_guard_activity"]["fallback_reasons"], {"accepted": 2})
            self.assertEqual(summary["artifact_sha256"]["outcomes.jsonl.gz"], study.file_sha(output / "outcomes.jsonl.gz"))
            for case, row in zip(prepared["cases"], rows):
                self.assertEqual(row["parent_proposal_ref"], prepared["proposal_references"][case["case_id"]])
                self.assertEqual(row["input_sha256"]["case"], study.digest(study.canonical(case)))
                self.assertNotIn("features", row)
                self.assertEqual(row["comparisons"]["semantic_final_v1"]["group_losses"], [])

    def test_exact_reference_fallback_does_not_claim_novel_complete_gain(self):
        prepared = prepared_fixture()

        def guard(reference, proposal, **kwargs):
            return reference, {"accepted": False, "fallback_reason": "protected_role_row_changed"}

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "replay"
            summary = study.replay(prepared, output, binding={}, counter=Counter(), guard=guard)
            self.assertFalse(summary["quality_gate"]["qualified"])
            for case, row in zip(prepared["cases"], study.jsonl(output / "outcomes.jsonl.gz")):
                self.assertEqual(row["response"], case["reference_response"])
                self.assertTrue(row["comparisons"]["semantic_final_v1"]["complete_loss"])

    def test_new_third_response_is_rejected_even_if_still_within_budget(self):
        def guard(reference, proposal, **kwargs):
            proposal["query"] = "unbound invented mutation"
            return accepted(reference, proposal)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "replay"
            with self.assertRaisesRegex(ValueError, "exact frozen operand"):
                study.replay(prepared_fixture(), output, binding={}, counter=Counter(), guard=guard)
            self.assertFalse((output / "summary.json").exists())
            self.assertEqual(study.jsonl(output / "outcomes.jsonl.gz"), [])

    def test_failure_preserves_durable_partial_gzip_and_original_error(self):
        seen = []
        original = RuntimeError("invented second audit failure")

        def audit(case, saved, row):
            seen.append(row["case_id"])
            if len(seen) == 2:
                raise original

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "replay"
            with self.assertRaises(RuntimeError) as actual:
                study.replay(prepared_fixture(), output, binding={}, counter=Counter(), guard=accepted, audit_hook=audit)
            self.assertIs(actual.exception, original)
            failure = study.read(output / "failure.json")
            self.assertEqual(failure["stored_rows"], 1)
            self.assertEqual(failure["outcomes_sha256"], study.file_sha(output / "outcomes.jsonl.gz"))
            self.assertEqual(len(study.jsonl(output / "outcomes.jsonl.gz")), 1)

    def test_post_replay_source_check_failure_cannot_publish_success(self):
        def finish():
            raise RuntimeError("invented source changed")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "replay"
            with self.assertRaisesRegex(RuntimeError, "source changed"):
                study.replay(prepared_fixture(), output, binding={}, counter=Counter(), guard=accepted, finish=finish)
            self.assertFalse((output / "summary.json").exists())
            self.assertEqual(study.read(output / "failure.json")["stored_rows"], 2)

    def test_existing_attempt_is_unchanged_and_never_calls_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "old"
            output.mkdir()
            (output / "original").write_text("keep")
            with patch.object(study, "module", wraps=study.module):
                with self.assertRaises(FileExistsError):
                    study.replay(prepared_fixture(), output, binding={}, counter=Counter(), guard=lambda *a, **k: self.fail())
            self.assertEqual(list(output.iterdir()), [output / "original"])

    def test_parent_directory_sync_precedes_every_proposal(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            expected = parent.stat()
            synced = []
            actual_fsync = os.fsync

            def fsync(descriptor):
                value = os.fstat(descriptor)
                if stat.S_ISDIR(value.st_mode) and (value.st_dev, value.st_ino) == (expected.st_dev, expected.st_ino):
                    synced.append(True)
                actual_fsync(descriptor)

            def guard(*args, **kwargs):
                self.assertTrue(synced)
                return accepted(*args, **kwargs)

            with patch.object(study.os, "fsync", side_effect=fsync):
                study.replay(prepared_fixture(), parent / "replay", binding={}, counter=Counter(), guard=guard)
            self.assertEqual(synced, [True])

    def test_complete_new_source_set_excludes_all_old_semantic_globs(self):
        self.assertEqual(len(study.SOURCES), 7)
        self.assertTrue(all(not Path(name).name.startswith(("semantic", "audit_semantic", "test_semantic"))
                            and not name.startswith("src/semantic_search/") for name in study.SOURCES))

    def test_old_parent_cannot_be_self_rebound_after_publication(self):
        original = b"original published parent bytes"
        new_report = b"new explicit parent recheck"
        bound = {"old/plan.json": study.digest(original), study.RECHECK: study.digest(new_report)}
        calls = []

        def published(root, *args):
            calls.append(args)
            self.assertEqual(args, ("show", study.PUBLICATION + ":old/plan.json"))
            return original

        with patch.object(study, "git", side_effect=published):
            study.verify_published_parents(ROOT, bound)
            bound["old/plan.json"] = study.digest(b"changed and self-rebound")
            with self.assertRaisesRegex(ValueError, "original public publication"):
                study.verify_published_parents(ROOT, bound)
        self.assertEqual(len(calls), 2)

    def test_actual_guard_and_independent_auditor_reconstruct_invented_outcomes(self):
        prepared = prepared_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "replay"
            summary = study.replay(prepared, output, binding={}, counter=Counter())
            auditor = study.module("audit_role_context")
            gate = auditor.audit_outcomes(prepared["cases"], study.jsonl(output / "outcomes.jsonl.gz"),
                prepared["parent_proposals"], prepared["proposal_references"], counter=Counter(), expected_rows=2, expected_units=1)
            self.assertEqual(gate["qualified"], summary["quality_gate"]["qualified"])

    def test_actual_state_write_guard_fallback_is_independently_verified(self):
        cases, proposals, controls, negatives, complementary = fixture()
        for case, proposal in zip(cases, proposals):
            writer = row("a")
            writer["source"] = "def a(self):\n    self.state = 1\n"
            writer["source_hash"] = writer["content_hash"] = study.digest(writer["source"].encode())
            reference = response([writer])
            candidate = response([row("b")])
            case.update(reference_response=reference, legacy_control=deepcopy(reference),
                        raw_rows=[deepcopy(writer), row("b")], precoalesce_rows=[deepcopy(writer)])
            payload = serialize_context(compact_result(candidate))
            proposal.update(reference_response=deepcopy(reference), response=candidate,
                            compact_payload=payload, compact_sha256=study.digest(payload.encode()))
        docs = study.build_inputs(cases, proposals, controls, negatives, complementary,
            expected_rows=2, expected_units=1, expected_old_negatives=1)
        prepared = {"cases": cases, "parent_proposals": {row["case_id"]: row for row in proposals},
            "proposal_references": {row["case_id"]: row for row in docs["parent-outcome-references.json"]},
            "negatives": docs["negative-references.json"]}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "replay"
            summary = study.replay(prepared, output, binding={}, counter=Counter())
            outcomes = study.jsonl(output / "outcomes.jsonl.gz")
            self.assertTrue(all(not row["role_guard_receipt"]["accepted"] for row in outcomes))
            auditor = study.module("audit_role_context")
            gate = auditor.audit_outcomes(cases, outcomes, prepared["parent_proposals"], prepared["proposal_references"],
                                         counter=Counter(), expected_rows=2, expected_units=1)
            self.assertFalse(gate["qualified"])
            self.assertFalse(summary["quality_gate"]["qualified"])

    def test_prospective_freeze_requires_clean_exact_bytes_and_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for name in study.SOURCES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("invented source\n")
            folder = root / "prepared"
            folder.mkdir()
            for name in study.ARTIFACTS:
                (folder / name).write_text("{}\n")
            plan = {"source_sha256": study.source_bindings(root), "runtime": study.runtime(),
                    "parent_binding": {"sha256": {}},
                    "artifact_sha256": {name: study.file_sha(folder / name) for name in study.ARTIFACTS}}
            study.write_new(folder / "frozen-plan.json", plan)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Invented Test", "-c", "user.email=invented@example.invalid",
                            "commit", "-qm", "invented prospective inputs"], check=True)
            frozen = study.git(root, "rev-parse", "HEAD").decode().strip()
            receipt = study.verify_frozen(folder, frozen, root=root)
            self.assertEqual(receipt["commit"], frozen)
            self.assertEqual(receipt["bound_blob_count"], 12)
            with self.assertRaisesRegex(ValueError, "full immutable"):
                study.verify_frozen(folder, frozen[:7], root=root)
            with self.assertRaisesRegex(ValueError, "precede"):
                study.verify_frozen(folder, frozen, root=root, started_at="2000-01-01T00:00:00+00:00")
            (root / study.SOURCES[0]).write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "source/runtime changed"):
                study.verify_frozen(folder, frozen, root=root)


if __name__ == "__main__":
    unittest.main()
