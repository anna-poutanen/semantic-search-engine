import copy
import hashlib
import unittest

from semantic_search.context import ENCODING, apply_token_budget, compact_result, encoding, serialize_context
from semantic_search.source import physical_lines


def response(source, query="Find implementation 日本語 😀"):
    return {"query": query, "match_status": "candidates", "budget_exhausted": False,
            "results": [{"path": "src/café.py", "symbol": "compile", "start_line": 30,
                         "end_line": 29 + len(physical_lines(source)), "source": source,
                         "source_hash": hashlib.sha256(source.encode()).hexdigest(),
                         "content_hash": "original-full-window", "truncated": False,
                         "origin": "retrieval", "related_to": None}]}


class ContextTests(unittest.TestCase):
    def exact_count(self, result):
        text = serialize_context(compact_result(result))
        actual = len(encoding().encode(text, disallowed_special=()))
        self.assertEqual(actual, result["token_usage"]["tokens"])
        self.assertEqual(result["token_usage"]["encoding"], ENCODING)
        self.assertLessEqual(actual, result["token_usage"]["budget"])
        return actual

    def test_full_unicode_json_envelope_count_includes_query_and_result_metadata(self):
        original = response('def compile():\n    return "日本語😀\\n"\n')
        unchanged = copy.deepcopy(original)
        result = apply_token_budget(original, 1000)
        count = self.exact_count(result)
        source_tokens = len(encoding().encode(original["results"][0]["source"], disallowed_special=()))
        self.assertGreater(count, source_tokens)
        self.assertEqual(original, unchanged)
        self.assertEqual(result["results"], original["results"])
        self.assertFalse(result["token_usage"]["truncated"])
        self.assertFalse(result["budget_exhausted"])
        self.assertIn("日本語", serialize_context(compact_result(result)))

    def test_trimming_preserves_physical_unicode_crlf_lines_truthful_bounds_and_hash(self):
        source = 'value = "first\u2028second\u2029third😀"\r\n' + 'return "日本語" * 10\r\n' * 35
        original = response(source)
        result = apply_token_budget(original, 250)
        self.exact_count(result)
        row = result["results"][0]
        self.assertTrue(row["truncated"])
        self.assertTrue(result["budget_exhausted"])
        self.assertTrue(row["source"].startswith('value = "first\u2028second\u2029third😀"\r\n'))
        self.assertEqual(row["source"], "".join(physical_lines(source)[:len(physical_lines(row["source"]))]))
        self.assertEqual(row["end_line"], row["start_line"] + len(physical_lines(row["source"])) - 1)
        self.assertEqual(row["source_hash"], hashlib.sha256(row["source"].encode()).hexdigest())
        self.assertEqual(row["content_hash"], "original-full-window")
        self.assertEqual(result["total_source_chars"], len(row["source"]))

    def test_zero_source_space_returns_empty_candidates_and_oversized_query_errors(self):
        original = response("unbreakable = '" + "日本語😀" * 500 + "'\n")
        result = apply_token_budget(original, 150)
        self.exact_count(result)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["match_status"], "candidates")
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(result["total_source_chars"], 0)
        with self.assertRaisesRegex(ValueError, "query and result metadata"):
            apply_token_budget(response("", query="長いクエリ😀" * 500), 512)

    def test_empty_input_retains_match_status_and_exact_metadata_budget(self):
        original = response("")
        original["results"] = []
        initial = apply_token_budget(original, 1000)
        measured = self.exact_count(initial)
        exact = apply_token_budget(original, measured)
        self.exact_count(exact)
        self.assertEqual(exact["match_status"], "candidates")
        self.assertFalse(exact["budget_exhausted"])


if __name__ == "__main__":
    unittest.main()
