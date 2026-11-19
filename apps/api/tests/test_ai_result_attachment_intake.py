import unittest
from fastapi import HTTPException

from app.routers.ai_collaboration import (
    RESULT_FILE_TYPES,
    validate_result_file_signature,
    ProposalInput,
)


class AIResultAttachmentIntakeTests(unittest.TestCase):
    def test_supported_media_types_have_safe_size_limits(self):
        self.assertEqual(RESULT_FILE_TYPES[".png"][0], "image")
        self.assertEqual(RESULT_FILE_TYPES[".mp4"][0], "video")
        self.assertEqual(RESULT_FILE_TYPES[".mp3"][0], "audio")
        self.assertEqual(RESULT_FILE_TYPES[".srt"][0], "subtitle")
        self.assertEqual(RESULT_FILE_TYPES[".pdf"][0], "document")
        self.assertLessEqual(RESULT_FILE_TYPES[".srt"][2], 20 * 1024 * 1024)

    def test_ai_proposal_accepts_structured_character_and_prop_states(self):
        proposal = ProposalInput(
            chapter_number=8,
            asset_name="主角",
            event="主角获得新装备",
            evidence="他把银色护腕戴在手上。",
            age_after="青年",
            hairstyle_after="高马尾",
            injury_after="右肩旧伤",
            equipment_after="银色护腕",
            condition_after="护符已修复",
        )
        self.assertEqual(proposal.age_after, "青年")
        self.assertEqual(proposal.hairstyle_after, "高马尾")
        self.assertEqual(proposal.equipment_after, "银色护腕")

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
