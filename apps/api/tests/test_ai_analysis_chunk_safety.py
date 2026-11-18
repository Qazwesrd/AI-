import unittest

from app.routers.ai_novel_analysis import (
    merge_chunk_state,
    split_chunk_state,
    unknown_chunk_keys,
)


class AnalysisChunkSafetyTests(unittest.TestCase):
    def test_chunk_state_metadata_does_not_mix_with_analysis_results(self):
        original = {
            "1": [{"category": "character", "name": "主角"}],
            "__chunk_states__": {"1": "completed", "2": "submission_unknown"},
        }

        results, states = split_chunk_state(original)

        self.assertEqual(results["1"][0]["name"], "主角")
        self.assertNotIn("__chunk_states__", results)
        self.assertEqual(states["2"], "submission_unknown")

        merged = merge_chunk_state(results, states)
        self.assertEqual(merged["1"], results["1"])
        self.assertEqual(merged["__chunk_states__"]["2"], "submission_unknown")

    def test_unknown_chunk_detection_includes_submitting_and_unknown_states(self):
        states = {
            "1": "completed",
            "2": "submitting",
            "3": "submission_unknown",
            "4": "interrupted_unknown",
            "5": "pending",
        }
        self.assertEqual(unknown_chunk_keys(states), ["2", "3", "4"])


if __name__ == "__main__":
    unittest.main()
