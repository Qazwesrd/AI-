import unittest

from app.routers.story import normalize_shot_durations


class EpisodeDurationTests(unittest.TestCase):
    def test_scales_normal_shots_to_target_duration(self):
        shots = [{"duration_seconds": 5} for _ in range(10)]
        durations = normalize_shot_durations(shots, 120)
        self.assertEqual(round(sum(durations), 2), 120.0)
        self.assertTrue(all(1 <= value <= 60 for value in durations))

    def test_respects_per_shot_video_limit_when_weights_are_unbalanced(self):
        shots = [{"duration_seconds": 1000}] + [{"duration_seconds": 1} for _ in range(9)]
        durations = normalize_shot_durations(shots, 120)
        self.assertEqual(round(sum(durations), 2), 120.0)
        self.assertLessEqual(max(durations), 60.0)
        self.assertGreaterEqual(min(durations), 1.0)

    def test_rejects_too_few_shots_for_target_duration(self):
        with self.assertRaises(ValueError):
            normalize_shot_durations([{"duration_seconds": 5}], 120)

    def test_rejects_episode_target_outside_product_range(self):
        with self.assertRaises(ValueError):
            normalize_shot_durations([{"duration_seconds": 5}] * 10, 180)


if __name__ == "__main__":
    unittest.main()
