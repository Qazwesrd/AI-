import unittest
from pydantic import ValidationError
from app.routers.ai_collaboration import ProposalInput, ImportPayload


class AIStoryCollaborationSchemaTests(unittest.TestCase):
    def test_proposal_requires_evidence(self):
        with self.assertRaises(ValidationError):
            ProposalInput(chapter_number=1, asset_name="主角", event="受伤")

    def test_confidence_is_bounded(self):
        with self.assertRaises(ValidationError):
            ProposalInput(chapter_number=1, asset_name="主角", event="受伤", evidence="原文证据", confidence=1.5)

    def test_import_batch_is_bounded(self):
        with self.assertRaises(ValidationError):
            ImportPayload(novel_id=1, source_text="原文", proposals=[])


if __name__ == "__main__":
    unittest.main()
