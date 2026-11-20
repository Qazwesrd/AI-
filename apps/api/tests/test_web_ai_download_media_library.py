import tempfile
import unittest
from pathlib import Path

from app.routers.web_ai_tools import (
    MEDIA_LIBRARY_TYPES,
    validate_downloaded_media_signature,
)


class WebAIDownloadMediaLibraryTests(unittest.TestCase):
    def _check(self, extension: str, payload: bytes) -> bool:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("result" + extension)
            path.write_bytes(payload)
            return validate_downloaded_media_signature(path, extension)

    def test_mp4_signature_is_accepted(self):
        self.assertTrue(self._check(".mp4", b"\\x00\\x00\\x00\\x18ftypisom"))

    def test_mismatched_mp4_signature_is_rejected(self):
        self.assertFalse(self._check(".mp4", b"not a video file"))

    def test_webm_signature_is_accepted(self):
        self.assertTrue(self._check(".webm", b"\\x1a\\x45\\xdf\\xa3" + b"\\x00" * 12))

    def test_mp3_signature_is_accepted(self):
        self.assertTrue(self._check(".mp3", b"ID3" + b"\\x00" * 16))

    def test_vtt_signature_is_accepted(self):
        self.assertTrue(self._check(".vtt", b"WEBVTT\\n\\n00:00:01.000 --> 00:00:02.000\\nHello"))

    def test_srt_text_is_accepted_but_binary_is_rejected(self):
        self.assertTrue(self._check(".srt", b"1\\n00:00:01,000 --> 00:00:02,000\\nHello"))
        self.assertFalse(self._check(".srt", b"1\\x00\\x02"))

    def test_only_editable_media_types_are_registered(self):
        self.assertEqual(MEDIA_LIBRARY_TYPES[".mp4"][0], "video")
        self.assertEqual(MEDIA_LIBRARY_TYPES[".wav"][0], "audio")
        self.assertEqual(MEDIA_LIBRARY_TYPES[".srt"][0], "other")


if __name__ == "__main__":
    unittest.main()
