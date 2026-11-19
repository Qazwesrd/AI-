import unittest
from fastapi import HTTPException

from app.routers.ai_collaboration import (
    RESULT_FILE_TYPES,
    validate_result_file_signature,
)


class AIResultAttachmentIntakeTests(unittest.TestCase):
    def test_supported_media_types_have_safe_size_limits(self):
        self.assertEqual(RESULT_FILE_TYPES[".png"][0], "image")
        self.assertEqual(RESULT_FILE_TYPES[".mp4"][0], "video")
        self.assertEqual(RESULT_FILE_TYPES[".mp3"][0], "audio")
        self.assertEqual(RESULT_FILE_TYPES[".srt"][0], "subtitle")
        self.assertEqual(RESULT_FILE_TYPES[".pdf"][0], "document")
        self.assertLessEqual(RESULT_FILE_TYPES[".srt"][2], 20 * 1024 * 1024)

    def test_png_signature_is_accepted(self):
        validate_result_file_signature(".png", b"\x89PNG\r\n\x1a\n" + b"data")

    def test_mp4_signature_is_accepted(self):
        validate_result_file_signature(".mp4", b"\x00\x00\x00\x18ftypisom")

    def test_mismatched_extension_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            validate_result_file_signature(".png", b"MZ executable")
        self.assertEqual(context.exception.status_code, 415)

    def test_empty_text_file_is_rejected(self):
        with self.assertRaises(HTTPException):
            validate_result_file_signature(".txt", b"")


if __name__ == "__main__":
    unittest.main()
