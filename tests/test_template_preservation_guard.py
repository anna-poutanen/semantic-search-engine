"""Invented source/operand contracts, before any observed template outcomes."""
import ast
import base64
from copy import deepcopy
import hashlib
import json
import re
import unittest
from unittest.mock import patch

from experiments.template_preservation.guard import apply_template_guard


def sha(raw):
    return hashlib.sha256(raw if type(raw) is bytes else raw.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def row(path, symbol, text, low, high):
    return {"id": f"{path}:{symbol}:{low}-{high}", "path": path, "symbol": symbol,
        "start_line": low, "end_line": high, "window_end_line": high, "source": text,
        "source_hash": sha(text), "content_hash": sha(text), "truncated": False,
        "origin": "retrieval", "retrieval_score": 0.4, "source_origin": "saved"}


def reidentify(bundle):
    bundle["id"] = "sources_" + sha(canonical({"dataset": bundle["dataset"],
        "source_sha256": {name: record["sha256"] for name, record in bundle["files"].items()},
        "definition_keys": bundle["definition_keys"]}))


def fixture(source='def render(value):\n    return f"message: {value.finish()}"\n', *,
            symbol="render", low=1, high=None, path="invented.py"):
    source_lines = lines(source)
    high = len(source_lines) if high is None else high
    target = row(path, symbol, "".join(source_lines[low - 1:high]), low, high)
    spare_source, replacement_source = "def spare():\n    return 1\n", "def replacement():\n    return 2\n"
    spare_low = len(source_lines) + 1
    spare = row(path, "spare", spare_source, spare_low, spare_low + 1)
    replacement = row(path, "replacement", replacement_source, spare_low + 2, spare_low + 3)
    module = source + spare_source + replacement_source
    all_rows = (target, spare, replacement)
    keys = {item["id"]: [item[name] for name in ("path", "symbol", "start_line", "end_line")]
            for item in all_rows}
    raw = module.encode("utf-8")
    bundle = {"dataset": "invented", "sources": {path: module}, "files": {path: {
        "raw_base64": base64.b64encode(raw).decode("ascii"), "bytes": len(raw),
        "sha256": sha(raw), "encoding": "utf-8"}}, "definition_keys": keys,
        "inventory": [{"id": item["id"], "path": item["path"], "symbol": item["symbol"],
            "start_line": item["start_line"], "end_line": item["end_line"],
            "text": item["source"], "content_hash": item["content_hash"]} for item in all_rows]}
    reidentify(bundle)
    reference = {"query": "invented task", "results": [target, spare], "timings": {"total": 1.0}}
    proposal = {"query": "invented task", "results": [deepcopy(target), replacement], "timings": {"total": 2.0}}
    return reference, proposal, bundle, deepcopy(keys)


class TemplateGuardTests(unittest.TestCase):
    def apply(self, values, **kwargs):
        reference, proposal, bundle, keys = values
        return apply_template_guard(reference, proposal, source_bundle=bundle, definition_keys=keys, **kwargs)

    def test_message_formatter_qualifies_without_relevance_claim(self):
        values = fixture()
        before = deepcopy(values)
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[1])
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["protected_ids"], [values[0]["results"][0]["id"]])
        self.assertEqual(receipt["reference_facts"][0]["status"], "returned_template")
        self.assertEqual(receipt["schema"], "returned_template_guard_v1")
        self.assertEqual(receipt["mode"], "returned_template_terminal_v1")
        self.assertTrue(all(value == 0 for value in receipt["calls"].values()))
        self.assertEqual(values, before)
        chosen["results"][0]["source"] = "changed output"
        receipt["reference_facts"][0]["definition_key"][0] = "changed receipt"
        self.assertEqual(values, before)

    def test_removed_template_returns_exact_entire_coverage_operand(self):
        values = fixture()
        values[1]["results"].pop(0)
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[0])
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["fallback_reason"], "protected_template_row_changed")
        self.assertEqual(receipt["violated_ids"], [values[0]["results"][0]["id"]])
        self.assertEqual(chosen["timings"], {"total": 1.0})

    def test_any_protected_dictionary_field_type_presence_or_provenance_is_exact(self):
        changes = (("retrieval_score", 0.7), ("retrieval_score", 0), ("source", "changed"),
            ("source_origin", "overlay"), ("extra", None), ("truncated", 0), ("start_line", True),
            ("metadata", {"nested": [1, False]}))
        for name, value in changes:
            with self.subTest(name=name, value=value):
                values = fixture()
                values[1]["results"][0][name] = value
                self.assertEqual(self.apply(values)[0], values[0])
        values = fixture()
        del values[1]["results"][0]["source_origin"]
        self.assertEqual(self.apply(values)[0], values[0])
        values = fixture()
        values[0]["results"][0]["metadata"] = {"nested": [1]}
        values[1]["results"][0]["metadata"] = {"nested": [1.0]}
        self.assertEqual(self.apply(values)[0], values[0])

    def test_signed_zero_metadata_change_is_not_exact_canonical_preservation(self):
        values = fixture()
        values[0]["results"][0]["retrieval_score"] = -0.0
        values[1]["results"][0]["retrieval_score"] = 0.0
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[0])
        self.assertEqual(receipt["fallback_reason"], "protected_template_row_changed")

    def test_reordered_unchanged_protected_rows_and_changed_global_metadata_allowed(self):
        values = fixture()
        values[1]["results"].reverse()
        values[1]["query"] = "already validated parent metadata"
        self.assertEqual(self.apply(values)[0], values[1])

    def test_call_names_attributes_arguments_and_async_names_are_not_privileged(self):
        examples = [
            'def arbitrary(x):\n    return f"{renamed(x)}"\n',
            'def arbitrary(x):\n    return f"{x.any_name(1, named=2)!r:>10}"\n',
            'async def arbitrary(x):\n    return f"{x.finish()}"\n',
            'def arbitrary(x):\n    return f"{(lambda: x)()}"\n',
        ]
        for source in examples:
            with self.subTest(source=source):
                receipt = self.apply(fixture(source, symbol="arbitrary"))[1]
                self.assertEqual(receipt["reference_facts"][0]["status"], "returned_template")

    def test_only_immediate_formatted_call_in_direct_returned_joinedstr_qualifies(self):
        examples = [
            'def render(x):\n    return "call()"\n',
            'def render(x):\n    return f"call(): {x}"\n',
            'def render(x):\n    return f"{x.method}"\n',
            'def render(x):\n    return f"{(lambda: x.finish())}"\n',
            'def render(x):\n    return f"{x + finish()}"\n',
            'def render(x):\n    return f"{x:{finish()}}"\n',
            'def render(x):\n    return "".join([f"{finish()}"])\n',
            'def render(x):\n    text = f"{finish()}"\n    return text\n',
            'def render(x):\n    return (f"{finish()}" if x else "")\n',
            'async def render(x):\n    return f"{await finish()}"\n',
            'def render(x):\n    """return f"{finish()}" """\n    return x\n',
        ]
        for source in examples:
            with self.subTest(source=source):
                values = fixture(source)
                values[1]["results"].pop(0)
                chosen, receipt = self.apply(values)
                self.assertEqual(chosen, values[1])
                self.assertEqual(receipt["reference_facts"][0]["status"], "no_returned_template")
                self.assertEqual(receipt["protected_ids"], [])

    def test_nested_lexical_bodies_do_not_supply_outer_return_evidence(self):
        examples = [
            'def render(x):\n    def nested():\n        return f"{finish()}"\n    return x\n',
            'def render(x):\n    async def nested():\n        return f"{finish()}"\n    return x\n',
            'def render(x):\n    class Nested:\n        def method(self):\n            return f"{finish()}"\n    return x\n',
            'def render(x):\n    maker = lambda: f"{finish()}"\n    return maker\n',
        ]
        for source in examples:
            with self.subTest(source=source):
                receipt = self.apply(fixture(source))[1]
                self.assertEqual(receipt["reference_facts"][0]["status"], "no_returned_template")

    def test_returns_inside_current_lexical_control_flow_qualify(self):
        sources = [
            'def render(x):\n    if x:\n        return f"{finish()}"\n    return ""\n',
            'def render(x):\n    try:\n        return f"{finish()}"\n    except Exception:\n        return ""\n',
            'def render(x):\n    with x:\n        return f"{finish()}"\n',
            'def render(x):\n    for part in x:\n        return f"{finish()}"\n',
        ]
        for source in sources:
            self.assertEqual(self.apply(fixture(source))[1]["reference_facts"][0]["status"], "returned_template")

    def test_byte_witnesses_bind_unicode_crlf_barecr_and_decorated_nested_spans(self):
        for ending in ("\n", "\r\n", "\r"):
            with self.subTest(ending=ending):
                source = ending.join([
                    "class Container:", "    @decorate('é')", "    async def render(self):",
                    "        label = '🌍'", '        return f"é {self.finish(\'🌍\')}"', ""])
                values = fixture(source, symbol="Container.render", low=2, high=5)
                receipt = self.apply(values)[1]
                fact = receipt["reference_facts"][0]
                self.assertEqual(fact["status"], "returned_template")
                witness = fact["witnesses"][0]
                module = values[2]["sources"]["invented.py"].encode()
                for item in witness.values():
                    self.assertEqual(module[item["start_byte"]:item["end_byte"]].decode(), item["source"])
                    self.assertEqual(sha(item["source"]), item["sha256"])
                    self.assertEqual(len(item["source"].encode()), item["end_byte"] - item["start_byte"])
                self.assertEqual(witness["definition"]["source"], values[0]["results"][0]["source"])
                self.assertEqual(witness["call"]["source"], "self.finish('🌍')")
                self.assertEqual(witness["return"]["start_line"], 5)
                self.assertEqual(witness["call"]["end_line"], 5)

    def test_non_utf8_saved_module_has_utf8_witnesses_and_overlay_hash_binds_decoded_buffer(self):
        source = '# coding: latin-1\ndef render(x):\n    return f"é {finish(\'café\')}"\n'
        values = fixture(source, low=2, high=3)
        bundle = values[2]
        module = bundle["sources"]["invented.py"]
        raw = module.encode("latin-1")
        bundle["files"]["invented.py"].update(raw_base64=base64.b64encode(raw).decode("ascii"),
            bytes=len(raw), sha256=sha(raw), encoding="iso-8859-1")
        reidentify(bundle)
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[1])
        self.assertEqual(receipt["reference_facts"][0]["source_file_sha256"], sha(raw))
        self.assertEqual(receipt["reference_facts"][0]["witnesses"][0]["call"]["source"], "finish('café')")
        utf8_hash = sha(module)
        self.assertNotEqual(utf8_hash, sha(raw))
        values[0]["results"][0].update(source_origin="overlay", buffer_version=7, buffer_hash=utf8_hash)
        values[1]["results"][0] = deepcopy(values[0]["results"][0])
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[1])
        self.assertEqual(receipt["reference_facts"][0]["provenance"],
            {"source_origin": "overlay", "buffer_version": 7, "buffer_hash": utf8_hash})
        values[0]["results"][0]["buffer_hash"] = sha(raw)
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[0])
        self.assertEqual(receipt["fallback_reason"], "invalid_reference_facts")

    def test_multiline_template_and_multiple_direct_calls_have_ordered_exact_witnesses(self):
        source = 'def render(x):\n    return f"""first {alpha(x)}\nsecond {beta(gamma(x))}"""\n'
        values = fixture(source)
        witnesses = self.apply(values)[1]["reference_facts"][0]["witnesses"]
        self.assertEqual([item["call"]["source"] for item in witnesses], ["alpha(x)", "beta(gamma(x))"])
        self.assertEqual(witnesses[0]["return"]["end_line"], 3)
        self.assertLess(witnesses[0]["call"]["start_byte"], witnesses[1]["call"]["start_byte"])

    def test_distinct_nested_definition_gets_its_own_lexical_witness(self):
        source = 'def outer():\n    def inner(x):\n        return f"{x.finish()}"\n    return inner\n'
        receipt = self.apply(fixture(source, symbol="outer.inner", low=2, high=3))[1]
        self.assertEqual(receipt["reference_facts"][0]["status"], "returned_template")
        self.assertEqual(receipt["reference_facts"][0]["witnesses"][0]["call"]["start_line"], 3)

    def test_valid_partial_window_does_not_block_an_unrelated_parent_exchange(self):
        values = list(fixture())
        reference, proposal, bundle, keys = values
        target = reference["results"][0]
        target["end_line"] = 1
        target["source"] = lines(target["source"])[0]
        target["source_hash"] = sha(target["source"])
        target["truncated"] = True
        proposal["results"][0] = deepcopy(target)
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, proposal)
        self.assertEqual(receipt["reference_facts"][0]["status"], "partial_definition")
        self.assertEqual(receipt["protected_ids"], [])

    def test_partial_inventory_cannot_claim_complete_canonical_definition(self):
        values = list(fixture())
        reference, proposal, bundle, keys = values
        target = reference["results"][0]
        target.update(end_line=1, window_end_line=1, source=lines(target["source"])[0])
        target.update(source_hash=sha(target["source"]), content_hash=sha(target["source"]))
        bundle["inventory"][0].update(end_line=1, text=target["source"], content_hash=target["content_hash"])
        proposal["results"].pop(0)
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, proposal)
        self.assertEqual(receipt["reference_facts"][0]["status"], "partial_definition")

    def test_other_languages_add_no_template_fact(self):
        values = fixture('function render(x) { return `${finish()}`; }\n', path="invented.js")
        values[1]["results"].pop(0)
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[1])
        self.assertEqual(receipt["reference_facts"][0]["status"], "unsupported_language")
        self.assertEqual(receipt["protected_ids"], [])

    def test_fullcanonical_parse_uncertainty_protects_only_unchanged_row(self):
        values = fixture()
        parse = ast.parse
        calls = 0

        def uncertain_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise SyntaxError("invented parser uncertainty")
            return parse(*args, **kwargs)

        with patch("experiments.template_preservation.guard.ast.parse", side_effect=uncertain_once):
            chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[1])
        self.assertEqual(receipt["reference_facts"][0]["status"], "uncertain_definition")
        values[1]["results"].pop(0)
        calls = 0
        with patch("experiments.template_preservation.guard.ast.parse", side_effect=uncertain_once):
            self.assertEqual(self.apply(values)[0], values[0])

    def test_same_named_definitions_are_bound_by_span_not_name(self):
        source = 'def render(x):\n    return f"{finish()}"\ndef render(x):\n    return x\n'
        values = fixture(source, low=3, high=4)
        values[1]["results"].pop(0)
        chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[1])
        self.assertEqual(receipt["reference_facts"][0]["status"], "no_returned_template")

    def test_source_drift_any_raw_or_inventory_identity_field_declines(self):
        changes = [
            lambda b, k: b["sources"].update({"invented.py": "changed"}),
            lambda b, k: b["files"]["invented.py"].update(sha256="0" * 64),
            lambda b, k: b["files"]["invented.py"].update(bytes=True),
            lambda b, k: b["files"]["invented.py"].update(encoding="latin-1"),
            lambda b, k: b["files"]["invented.py"].update(raw_base64="!?"),
            lambda b, k: b["inventory"][0].update(text="changed"),
            lambda b, k: b["inventory"][0].update(content_hash="0" * 64),
            lambda b, k: b["inventory"][0].update(start_line=True),
            lambda b, k: b["inventory"].append(deepcopy(b["inventory"][0])),
            lambda b, k: b.update(id="sources_" + "0" * 64),
            lambda b, k: k[next(iter(k))].__setitem__(1, "wrong"),
        ]
        for change in changes:
            with self.subTest(change=change):
                values = fixture()
                change(values[2], values[3])
                chosen, receipt = self.apply(values)
                self.assertEqual(chosen, values[0])
                self.assertEqual(receipt["fallback_reason"], "invalid_source_inventory")
                self.assertIsNone(receipt["source_bundle_sha256"])

    def test_row_source_boundaries_hashes_and_provenance_malformed_decline(self):
        changes = [
            lambda r: r.update(source="changed"),
            lambda r: r.update(source_hash="0" * 64),
            lambda r: r.update(content_hash="0" * 64),
            lambda r: r.update(start_line=True),
            lambda r: r.update(window_end_line=99),
            lambda r: r.update(source_origin="remote"),
            lambda r: r.update(buffer_hash="0" * 64),
            lambda r: r.update(source_origin="overlay", buffer_version=True, buffer_hash="0" * 64),
        ]
        for change in changes:
            with self.subTest(change=change):
                values = fixture()
                change(values[0]["results"][0])
                chosen, receipt = self.apply(values)
                self.assertEqual(chosen, values[0])
                self.assertEqual(receipt["fallback_reason"], "invalid_reference_facts")
                self.assertEqual(receipt["protected_ids"], [])

    def test_nonfinite_literals_duplicate_ids_and_invalid_operands_decline(self):
        values = fixture()
        values[0]["results"][0]["score"] = float("nan")
        self.assertEqual(self.apply(values)[1]["fallback_reason"], "invalid_reference")
        values = fixture()
        values[1]["results"].append(deepcopy(values[1]["results"][0]))
        self.assertEqual(self.apply(values)[1]["fallback_reason"], "invalid_proposal")
        for side, value in ((0, None), (1, {})):
            values = list(fixture())
            values[side] = value
            self.assertEqual(self.apply(values)[1]["fallback_reason"],
                             "invalid_reference" if side == 0 else "invalid_proposal")

    def test_bound_source_is_never_executed_and_has_no_io_model_or_network(self):
        source = 'def render(x=explode()):\n    return f"{explode()}"\n'
        values = fixture(source)
        with patch("builtins.exec", side_effect=AssertionError("source executed")), \
                patch("builtins.eval", side_effect=AssertionError("source evaluated")), \
                patch("builtins.open", side_effect=AssertionError("file opened")):
            chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[1])
        self.assertEqual(receipt["reference_facts"][0]["status"], "returned_template")

    def test_every_cancellation_boundary_propagates_original_including_parser_errors(self):
        values = fixture()
        total = 0

        def count():
            nonlocal total
            total += 1

        self.apply(values, cancel_check=count)
        self.assertGreater(total, 20)
        for error_type in (ValueError, SyntaxError, UnicodeError, RuntimeError, KeyboardInterrupt):
            class Cancelled(error_type):
                pass
            for boundary in range(1, total + 1):
                error = Cancelled("original cancellation")
                count_at = 0

                def cancel():
                    nonlocal count_at
                    count_at += 1
                    if count_at == boundary:
                        raise error

                with self.subTest(error_type=error_type, boundary=boundary):
                    with self.assertRaises(error_type) as raised:
                        self.apply(values, cancel_check=cancel)
                    self.assertIs(raised.exception, error)

    def test_parse_failure_is_conservative_without_retrieval_fallback(self):
        values = fixture()
        with patch("experiments.template_preservation.guard.ast.parse", side_effect=RecursionError()):
            chosen, receipt = self.apply(values)
        self.assertEqual(chosen, values[0])  # spare is uncertain and changed in the proposal
        self.assertTrue(all(value == 0 for value in receipt["calls"].values()))

    def test_receipt_inventory_and_source_hashes_bind_complete_inputs(self):
        values = fixture()
        receipt = self.apply(values)[1]
        self.assertEqual(receipt["source_bundle_sha256"], sha(canonical(values[2])))
        self.assertEqual(receipt["source_inventory_sha256"], sha(canonical(values[2]["inventory"])))
        self.assertEqual(receipt["definition_keys_sha256"], sha(canonical(values[3])))
        fact = receipt["reference_facts"][0]
        self.assertEqual(fact["row_sha256"], sha(canonical(values[0]["results"][0])))
        self.assertEqual(fact["source_file_sha256"], values[2]["files"]["invented.py"]["sha256"])
        self.assertEqual(fact["provenance"], {"source_origin": "saved"})

    def test_no_literal_witnesses_come_from_docstrings_comments_or_decorators(self):
        source = '@decorate(f"{finish()}")\ndef render(x):\n    # return f"{finish()}"\n    return x\n'
        values = fixture(source)
        receipt = self.apply(values)[1]
        self.assertEqual(receipt["reference_facts"][0]["status"], "no_returned_template")
        self.assertEqual(receipt["reference_facts"][0]["witnesses"], [])

    def test_witness_calls_are_direct_ast_call_not_nested_call_count(self):
        values = fixture('def render(x):\n    return f"{alpha(beta(x))}"\n')
        witnesses = self.apply(values)[1]["reference_facts"][0]["witnesses"]
        self.assertEqual(len(witnesses), 1)
        self.assertEqual(witnesses[0]["call"]["source"], "alpha(beta(x))")
        self.assertIsInstance(ast.parse(witnesses[0]["call"]["source"], mode="eval").body, ast.Call)
