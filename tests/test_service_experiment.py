import hashlib
import unittest

from semantic_search.experiment import matches
from semantic_search.service_experiment import returned_snippets, render_service_report


class ServiceExperimentTests(unittest.TestCase):
    def test_truncated_delivery_cannot_inherit_full_window_evidence(self):
        source = "def handle():\n"
        response = {"results": [{"id": "handle:1-4", "path": "src/code.py", "symbol": "handle",
            "start_line": 1, "end_line": 1, "window_end_line": 4, "source": source,
            "source_hash": hashlib.sha256(source.encode()).hexdigest()}]}
        target = {"path": "src/code.py", "symbol": "handle", "evidence_lines": [2, 4]}
        delivered = returned_snippets(response)[0]
        self.assertEqual(delivered.end_line, 1)
        self.assertFalse(matches(delivered, target))
        self.assertEqual(delivered.text, source)

    def test_report_uses_dynamic_quality_denominators_and_latency_samples(self):
        result = {"summary": {"dense:0": {
            "all": {"queries": 16, "hit_at_1": 13/16, "hit_at_5": 1,
                    "median_s": .09, "p95_s": .12},
            "test": {"queries": 8, "hit_at_1": 6/8, "hit_at_5": 1,
                     "median_s": .09, "p95_s": .12}}},
            "setup_s": 15, "memory": {"processes": {}, "note": "Including Metal"}, "limits": []}
        report = render_service_report(result)
        self.assertIn("| dense:0 | all | 16 | 13/16 | 16/16 |", report)
        self.assertIn("| dense:0 | test | 8 | 6/8 | 8/8 |", report)
        self.assertIn("Quality denominators contain only present queries", report)
        self.assertIn("Within returned results (≤5)", report)


if __name__ == "__main__":
    unittest.main()
