import unittest
from types import SimpleNamespace

from app.routers.ai_collaboration import evidence_matches_chapter


class AIStoryEvidenceValidationTests(unittest.TestCase):
    def setUp(self):
        self.chapter = SimpleNamespace(
            chapter_number=7,
            content="林默推开石门，发现古剑已经断裂。随后他将剑柄交给了师父。",
        )

    def test_exact_quote_from_persisted_chapter_is_accepted(self):
        self.assertTrue(evidence_matches_chapter(self.chapter, "古剑已经断裂"))

    def test_quote_not_in_persisted_chapter_is_rejected(self):
        self.assertFalse(evidence_matches_chapter(self.chapter, "古剑突然发出金光"))

    def test_empty_quote_is_rejected(self):
        self.assertFalse(evidence_matches_chapter(self.chapter, "   "))

    def test_quote_must_be_contiguous(self):
        self.assertFalse(evidence_matches_chapter(self.chapter, "推开石门古剑已经断裂"))


if __name__ == "__main__":
    unittest.main()
