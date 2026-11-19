import unittest

from app.routers.web_ai_tools import ConfirmResultClassification


class WebAIResultClassificationTests(unittest.TestCase):
    def test_all_visual_asset_categories_are_supported(self):
        for category in ("character", "scene", "prop", "weapon", "costume", "building", "special_item"):
            payload = ConfirmResultClassification(classification=category, assetName="素材", novelId=1)
            self.assertEqual(payload.classification, category)

    def test_novel_binding_is_optional_for_legacy_result_imports(self):
        payload = ConfirmResultClassification(classification="image")
        self.assertIsNone(payload.novelId)


if __name__ == "__main__":
    unittest.main()
