import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from app.routers.media import (
    SpeechInput, speech_fingerprint, speech_task_is_in_flight,
    resolve_episode_shot_voice,
)


class SpeechSubmissionSafetyTests(unittest.TestCase):
    def test_ambiguous_speech_states_are_not_safe_to_resubmit(self):
        for status in ("queued", "submitting", "submission_unknown", "interrupted_unknown"):
            with self.subTest(status=status):
                task = SimpleNamespace(status=status)
                self.assertTrue(speech_task_is_in_flight(task))

    def test_pending_failed_and_completed_speech_states_are_not_in_flight(self):
        for status in ("pending", "failed", "cancelled", "completed"):
            with self.subTest(status=status):
                task = SimpleNamespace(status=status)
                self.assertFalse(speech_task_is_in_flight(task))

    def test_speech_fingerprint_is_stable_for_identical_request(self):
        first = speech_fingerprint("openai", "tts-1", "你好", "alloy", 1.0)
        second = speech_fingerprint("openai", "tts-1", "你好", "alloy", 1.0)
        self.assertEqual(first, second)

    def test_speech_fingerprint_changes_when_voice_or_text_changes(self):
        baseline = speech_fingerprint("openai", "tts-1", "你好", "alloy", 1.0)
        self.assertNotEqual(baseline, speech_fingerprint("openai", "tts-1", "你好", "nova", 1.0))
        self.assertNotEqual(baseline, speech_fingerprint("openai", "tts-1", "你好呀", "alloy", 1.0))

    def test_episode_voice_resolution_uses_character_profile_for_single_character_shot(self):
        shot = SimpleNamespace(character_ids=[42])
        self.assertEqual(
            resolve_episode_shot_voice(shot, {"42": "nova"}, "alloy"),
            "nova",
        )
        self.assertEqual(
            resolve_episode_shot_voice(shot, {}, "alloy"),
            "alloy",
        )

    def test_episode_voice_resolution_falls_back_for_multi_character_shot(self):
        shot = SimpleNamespace(character_ids=[42, 43])
        self.assertEqual(
            resolve_episode_shot_voice(shot, {"42": "nova", "43": "echo"}, "alloy"),
            "alloy",
        )

    def test_episode_voice_resolution_uses_manually_selected_speaker(self):
        shot = SimpleNamespace(character_ids=[42, 43])
        self.assertEqual(
            resolve_episode_shot_voice(
                shot, {"42": "nova", "43": "echo"}, "alloy", speaker_character_id=43
            ),
            "echo",
        )

    def test_episode_voice_resolution_rejects_speaker_not_in_shot(self):
        shot = SimpleNamespace(character_ids=[42, 43])
        with self.assertRaises(ValueError):
            resolve_episode_shot_voice(
                shot, {"42": "nova", "43": "echo"}, "alloy", speaker_character_id=99
            )

    def test_output_format_cannot_inject_a_file_path(self):
        with self.assertRaises(ValidationError):
            SpeechInput(
                provider="openai", model="tts-1", text="你好",
                response_format="../outside.wav",
            )


if __name__ == "__main__":
    unittest.main()
