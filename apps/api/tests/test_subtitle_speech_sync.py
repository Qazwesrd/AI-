import unittest

from app.routers.media import subtitle_cue_duration


class SubtitleSpeechSyncTests(unittest.TestCase):
    def test_missing_speech_duration_uses_full_shot(self):
        self.assertEqual(subtitle_cue_duration(5.0, None), 5.0)

    def test_shorter_speech_ends_subtitle_with_dialogue(self):
        self.assertEqual(subtitle_cue_duration(5.0, 3.2), 3.2)

    def test_faster_speech_is_aligned_to_adjusted_clip_duration(self):
        self.assertEqual(subtitle_cue_duration(5.0, 6.5), 5.0)

    def test_speech_within_supported_tempo_limit_fills_clip(self):
        self.assertEqual(subtitle_cue_duration(5.0, 7.0), 5.0)

    def test_very_short_dialogue_keeps_minimum_readability_without_exceeding_shot(self):
        self.assertEqual(subtitle_cue_duration(5.0, 0.2), 0.8)
        self.assertEqual(subtitle_cue_duration(0.4, 0.1), 0.4)

    def test_invalid_clip_duration_returns_zero(self):
        self.assertEqual(subtitle_cue_duration(0, 2.0), 0.0)


if __name__ == "__main__":
    unittest.main()
