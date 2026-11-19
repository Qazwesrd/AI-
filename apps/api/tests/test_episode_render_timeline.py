import unittest

from app.routers.media import build_episode_video_filter


class EpisodeRenderTimelineFilterTests(unittest.TestCase):
    def test_all_cuts_use_single_concat_group(self):
        filters, output, duration = build_episode_video_filter(
            [1, 2, 3], [5.0, 6.0, 7.0], []
        )
        self.assertEqual(output, "vg0")
        self.assertEqual(duration, 18.0)
        self.assertEqual(len(filters), 1)
        self.assertIn("concat=n=3:v=1:a=0", filters[0])

    def test_fade_transition_shortens_total_duration(self):
        filters, output, duration = build_episode_video_filter(
            [1, 2], [10.0, 12.0],
            [{"after_shot_id": 1, "transition": "fade", "duration_seconds": 1.5}],
        )
        self.assertEqual(output, "vx1")
        self.assertEqual(duration, 20.5)
        self.assertTrue(any("xfade=transition=fade" in item for item in filters))

    def test_dissolve_transition_uses_dissolve_filter(self):
        filters, _, duration = build_episode_video_filter(
            [1, 2, 3], [10.0, 10.0, 10.0],
            [
                {"after_shot_id": 1, "transition": "dissolve", "duration_seconds": 1.0},
                {"after_shot_id": 2, "transition": "dissolve", "duration_seconds": 1.0},
            ],
        )
        self.assertEqual(duration, 28.0)
        self.assertEqual(sum("xfade=transition=dissolve" in item for item in filters), 2)

    def test_cut_between_dissolves_remains_a_hard_cut(self):
        filters, _, duration = build_episode_video_filter(
            [1, 2, 3], [8.0, 8.0, 8.0],
            [{"after_shot_id": 1, "transition": "fade", "duration_seconds": 1.0}],
        )
        self.assertEqual(duration, 23.0)
        self.assertTrue(any("concat=n=2:v=1:a=0" in item for item in filters))
        self.assertTrue(any("xfade=transition=fade" in item for item in filters))

    def test_rejects_transition_that_is_too_long(self):
        with self.assertRaises(ValueError):
            build_episode_video_filter(
                [1, 2], [4.0, 6.0],
                [{"after_shot_id": 1, "transition": "fade", "duration_seconds": 2.0}],
            )


if __name__ == "__main__":
    unittest.main()
