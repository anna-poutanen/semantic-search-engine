import copy
import json
from pathlib import Path
import tempfile
import unittest

from semantic_search.matching import MatchPolicy, calibrate_policy


METADATA = {"id": "Qwen/Qwen3-Embedding-0.6B", "revision": "a" * 40,
            "query_prompt": "Instruct: Given a description of code behavior, retrieve the code implementation that performs it.\nQuery: {query}",
            "pooling": "last_token", "normalization": "l2", "dimensions": 1024}


def calibration_rows():
    scores = [("positive-low", "present", .35), ("positive-high", "present", .7),
              ("negative-low", "absent", .1), ("negative-overlap", "absent", .4)]
    return [{"query_id": identifier, "kind": kind, "top_cosine": score,
             "split": "calibration", "role": "development", "dataset_sha256": "b" * 64}
            for identifier, kind, score in scores]


class MatchingTests(unittest.TestCase):
    def test_threshold_retains_every_calibration_positive_without_confidence_claim(self):
        rows = calibration_rows()
        record = calibrate_policy(rows, METADATA)
        self.assertEqual(record["threshold"], .35)
        self.assertEqual(record["calibration"]["retained_positives"], 2)
        self.assertEqual(record["calibration"]["rejected_absent"], 1)
        self.assertEqual(record["calibration"]["dataset_sha256"], ["b" * 64])
        policy = MatchPolicy(record)
        for row in rows:
            if row["kind"] == "present":
                self.assertFalse(policy.rejects(row["top_cosine"], METADATA))
        self.assertFalse(policy.rejects(.35, METADATA))
        self.assertTrue(policy.rejects(.34, METADATA))
        self.assertIn("does not prove", record["meaning"])
        self.assertFalse(any(key in record for key in ("confidence", "probability", "guarantee")))

    def test_only_declared_calibration_rows_can_fit_policy(self):
        for changed in ({"split": "development"}, {"split": "test"},
                        {"split": "repository_holdout"}, {"role": "repository_holdout"},
                        {"split": None}):
            rows = calibration_rows()
            rows[0].update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                calibrate_policy(rows, METADATA)

    def test_both_classes_and_finite_valid_cosine_scores_required(self):
        for rows in ([], calibration_rows()[:2], calibration_rows()[2:]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                calibrate_policy(rows, METADATA)
        for score in (float("nan"), float("inf"), -1.01, 1.01, 10 ** 1000, True, "0.4"):
            rows = calibration_rows()
            rows[0]["top_cosine"] = score
            with self.subTest(score=score), self.assertRaises(ValueError):
                calibrate_policy(rows, METADATA)

    def test_complete_embedding_metadata_required_and_mismatch_rejected(self):
        for key in METADATA:
            incomplete = {name: value for name, value in METADATA.items() if name != key}
            with self.subTest(missing=key), self.assertRaises(ValueError):
                calibrate_policy(calibration_rows(), incomplete)
        record = calibrate_policy(calibration_rows(), METADATA)
        for key in METADATA:
            changed = {**METADATA, key: 2048 if key == "dimensions" else "different"}
            with self.subTest(changed=key), self.assertRaises(ValueError):
                MatchPolicy(record).rejects(.2, changed)
        extended = {**METADATA, "max_tokens": 8192, "runtime": "mlx-embeddings"}
        policy = MatchPolicy(calibrate_policy(calibration_rows(), extended))
        with self.assertRaises(ValueError):
            policy.rejects(.2, {**extended, "max_tokens": 4096})

    def test_dataset_and_query_provenance_are_validated(self):
        changes = [{"dataset_sha256": "not-a-hash"}, {"dataset_sha256": ""},
                   {"dataset_sha256": 123}, {"query_id": ""}, {"query_id": None},
                   {"kind": "unknown"}]
        for changed in changes:
            rows = calibration_rows()
            rows[0].update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                calibrate_policy(rows, METADATA)
        rows = calibration_rows()
        rows[1]["query_id"] = rows[0]["query_id"]
        with self.assertRaises(ValueError):
            calibrate_policy(rows, METADATA)

    def test_policy_requires_calibration_provenance_and_rejects_invalid_scores(self):
        record = calibrate_policy(calibration_rows(), METADATA)
        incomplete = copy.deepcopy(record)
        incomplete.pop("calibration")
        with self.assertRaises(ValueError):
            MatchPolicy(incomplete)
        for score in (float("nan"), float("inf"), -1.01, 1.01, True, "0.4"):
            with self.subTest(score=score), self.assertRaises(ValueError):
                MatchPolicy(record).rejects(score, METADATA)

    def test_policy_count_hash_and_id_consistency_are_checked_on_load(self):
        for changed in ({"positive_queries": True}, {"positive_queries": 0},
                        {"retained_positives": 1}, {"rejected_absent": 3},
                        {"query_ids": ["duplicated"] * 4}, {"query_ids": ["too-few"]},
                        {"dataset_sha256": ["invalid"]}, {"role": "repository_holdout"},
                        {"split": "test"}):
            record = calibrate_policy(calibration_rows(), METADATA)
            record["calibration"].update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                MatchPolicy(record)

    def test_frozen_policy_can_be_loaded_from_json_without_retuning(self):
        record = calibrate_policy(calibration_rows(), METADATA)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(record))
            loaded = MatchPolicy(path)
            self.assertEqual(loaded.threshold, record["threshold"])
            self.assertEqual(loaded.record, record)
            self.assertFalse(loaded.rejects(.5, METADATA))


if __name__ == "__main__":
    unittest.main()
