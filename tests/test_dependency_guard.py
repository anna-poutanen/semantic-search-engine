"""Invented terminal pair contracts; no observed selector outcomes."""
from copy import deepcopy
import hashlib
import unittest

from experiments.dependency_context.guard import apply_dependency_guard


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def row(name, source, start=1, path="invented.py"):
    end = start + len(source.splitlines()) - 1
    return {"id": f"{path}:{name}:{start}-{end}", "path": path, "symbol": name,
            "start_line": start, "end_line": end, "window_end_line": end,
            "source": source, "source_hash": sha(source), "content_hash": sha(source),
            "truncated": False, "origin": "retrieval", "retrieval_score": 0.4}


def fixture():
    from experiments.dependency_context.annotated_dependencies import SCHEMA

    caller = row("render", "def render(value: Writer):\n    return value.finish()\n", 10)
    callee = row("Writer.finish", "    def finish(self):\n        return 'finished'\n", 3)
    spare = row("unrelated", "def unrelated():\n    return 1\n", 20)
    replacement = row("replacement", "def replacement():\n    return 2\n", 23)
    keys = {item["id"]: [item[name] for name in ("path", "symbol", "start_line", "end_line")]
            for item in (caller, callee, spare, replacement)}

    def endpoint(item):
        return {"path": item["path"], "symbol": item["symbol"],
                "definition_key": keys[item["id"]], "definition_sha256": item["source_hash"],
                "source_sha256": item["source_hash"], "source": item["source"], "full_definition": True}

    def witness(text, line):
        return {"path": "invented.py", "start_line": line, "end_line": line,
                "start_byte": 0, "end_byte": len(text.encode()), "source": text, "sha256": sha(text)}

    facts = {"schema": SCHEMA, "accepted": [{"caller": endpoint(caller), "callee": endpoint(callee),
        "call": witness("value.finish()", 11), "annotation": witness("Writer", 10)}]}
    reference = {"query": "invented request", "results": [caller, callee, spare]}
    proposal = {"query": "invented request", "results": [deepcopy(caller), deepcopy(callee), replacement]}
    return reference, proposal, facts, keys


class DependencyGuardTests(unittest.TestCase):
    def apply(self, reference, proposal, facts, keys, **kwargs):
        return apply_dependency_guard(reference, proposal, dependencies=facts, definition_keys=keys, **kwargs)

    def test_preserves_unrelated_exchange_and_returns_defensive_exact_operand(self):
        values = fixture()
        before = deepcopy(values)
        chosen, receipt = self.apply(*values)
        self.assertEqual(chosen, values[1])
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["protected_ids"], [row["id"] for row in values[0]["results"][:2]])
        self.assertEqual(values, before)
        chosen["results"][0]["source"] = "mutated result"
        self.assertEqual(values, before)
        self.assertTrue(all(value == 0 for value in receipt["calls"].values()))

    def test_caller_callee_or_both_removed_reject_whole_proposal(self):
        for positions in ((0,), (1,), (0, 1)):
            with self.subTest(positions=positions):
                reference, proposal, facts, keys = fixture()
                proposal["results"] = [row for index, row in enumerate(proposal["results"]) if index not in positions]
                chosen, receipt = self.apply(reference, proposal, facts, keys)
                self.assertEqual(chosen, reference)
                self.assertFalse(receipt["accepted"])
                self.assertEqual(receipt["fallback_reason"], "delivered_dependency_pair_changed")
                self.assertEqual(receipt["violated_ids"], [reference["results"][i]["id"] for i in positions])

    def test_protected_dictionaries_include_scores_source_and_provenance(self):
        for key, value in (("retrieval_score", 0.7), ("source", "different"), ("source_origin", "overlay")):
            with self.subTest(key=key):
                reference, proposal, facts, keys = fixture()
                proposal["results"][1][key] = value
                chosen, receipt = self.apply(reference, proposal, facts, keys)
                self.assertEqual(chosen, reference)
                self.assertEqual(receipt["violated_ids"], [reference["results"][1]["id"]])

    def test_reordered_exact_rows_preserve_pairs(self):
        reference, proposal, facts, keys = fixture()
        proposal["results"].reverse()
        chosen, receipt = self.apply(reference, proposal, facts, keys)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(chosen, proposal)

    def test_no_new_dependency_is_admitted_when_only_one_endpoint_delivered(self):
        reference, proposal, facts, keys = fixture()
        reference["results"].pop(1)
        proposal["results"].pop(1)
        chosen, receipt = self.apply(reference, proposal, facts, keys)
        self.assertEqual(chosen, proposal)
        self.assertEqual(receipt["pairs"], [])

    def test_call_or_annotation_outside_delivered_interval_does_not_form_pair(self):
        for name in ("call", "annotation"):
            reference, proposal, facts, keys = fixture()
            facts["accepted"][0][name].update(start_line=99, end_line=99)
            chosen, receipt = self.apply(reference, proposal, facts, keys)
            self.assertEqual(chosen, proposal)
            self.assertEqual(receipt["pairs"], [])

    def test_malformed_receipts_and_nonfinite_metadata_fail_closed(self):
        for wrong in (None, {}, {"schema": "wrong", "accepted": []},
                      {"accepted": [None]}, {"accepted": [], "nan": float("nan")}):
            reference, proposal, facts, keys = fixture()
            if isinstance(wrong, dict) and wrong and "schema" not in wrong:
                wrong = {**facts, **wrong}
            chosen, receipt = self.apply(reference, proposal, wrong, keys)
            self.assertEqual(chosen, reference)
            self.assertEqual(receipt["fallback_reason"], "invalid_dependency_facts")

    def test_broken_witness_hash_rejected(self):
        reference, proposal, facts, keys = fixture()
        facts["accepted"][0]["call"]["sha256"] = "0" * 64
        chosen, receipt = self.apply(reference, proposal, facts, keys)
        self.assertEqual(chosen, reference)
        self.assertEqual(receipt["fallback_reason"], "invalid_dependency_facts")

    def test_uncertain_or_conflicting_endpoint_facts_fail_closed(self):
        for field, value in (("full_definition", False), ("source", "changed"), ("source_sha256", "0"*64)):
            reference, proposal, facts, keys = fixture()
            facts["accepted"][0]["callee"][field] = value
            chosen, receipt = self.apply(reference, proposal, facts, keys)
            self.assertEqual(chosen, reference)
            self.assertEqual(receipt["fallback_reason"], "invalid_dependency_facts")

    def test_invalid_reference_and_duplicate_proposal_ids_rejected(self):
        reference, proposal, facts, keys = fixture()
        proposal["results"].append(deepcopy(proposal["results"][0]))
        chosen, receipt = self.apply(reference, proposal, facts, keys)
        self.assertEqual(chosen, reference)
        self.assertEqual(receipt["fallback_reason"], "invalid_proposal")
        reference["results"] = None
        self.assertEqual(self.apply(reference, proposal, facts, keys)[1]["fallback_reason"], "invalid_reference")

    def test_every_cancellation_boundary_propagates_original_exception(self):
        for exception_type in (ValueError, TypeError, RuntimeError, KeyboardInterrupt):
            for stop in (1, 2, 3):
                values, error, calls = fixture(), exception_type("stop"), []

                def cancel():
                    calls.append(True)
                    if len(calls) == stop:
                        raise error

                with self.subTest(kind=exception_type, stop=stop), self.assertRaises(exception_type) as caught:
                    self.apply(*values, cancel_check=cancel)
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
