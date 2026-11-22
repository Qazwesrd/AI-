import unittest
from unittest.mock import patch

from app.services.local_tts import (
    LocalTtsUnavailable,
    list_local_sapi_voices,
    synthesize_local_sapi_cues,
)


class LocalTtsTests(unittest.TestCase):
    def test_lists_installed_sapi_voices_from_json(self):
        payload = '[{"name":"Microsoft Huihui Desktop","culture":"zh-CN","gender":"Female","age":"Adult"}]'
        with patch("app.services.local_tts._powershell", return_value=payload):
            voices = list_local_sapi_voices()
        self.assertEqual(voices[0]["name"], "Microsoft Huihui Desktop")
        self.assertEqual(voices[0]["culture"], "zh-CN")

    def test_empty_voice_inventory_is_an_empty_list(self):
        with patch("app.services.local_tts._powershell", return_value="[]"):
            self.assertEqual(list_local_sapi_voices(), [])

    def test_invalid_voice_is_rejected_before_audio_generation(self):
        with (
            patch("app.services.local_tts.list_local_sapi_voices", return_value=[]),
            patch("app.services.local_tts._powershell") as powershell,
        ):
            with self.assertRaises(LocalTtsUnavailable):
                synthesize_local_sapi_cues(
                    [{"text": "你好"}], "Missing voice", 0, __import__("pathlib").Path(".")
                )
        powershell.assert_not_called()


if __name__ == "__main__":
    unittest.main()
