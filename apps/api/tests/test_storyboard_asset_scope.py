import unittest
from types import SimpleNamespace

from app.routers.ai_collaboration import asset_belongs_to_novel, resolve_asset_state


class StoryboardAssetScopeTests(unittest.TestCase):
    def test_asset_owned_by_same_novel_is_available(self):
        asset = SimpleNamespace(data={"source_novel_id": 12})
        self.assertTrue(asset_belongs_to_novel(asset, 12))

    def test_asset_owned_by_another_novel_is_not_available(self):
        asset = SimpleNamespace(data={"source_novel_id": 13})
        self.assertFalse(asset_belongs_to_novel(asset, 12))

    def test_unscoped_manual_asset_remains_available(self):
        asset = SimpleNamespace(data={}, appearance="黑发", clothing="蓝衣")
        self.assertTrue(asset_belongs_to_novel(asset, 12))

    def test_invalid_owner_is_not_treated_as_unscoped(self):
        asset = SimpleNamespace(data={"source_novel_id": "not-an-id"})
        self.assertFalse(asset_belongs_to_novel(asset, 12))

    def test_effective_state_does_not_include_future_chapters(self):
        asset = SimpleNamespace(
            data={"evolution_history": [
                {"chapter_number": 4, "appearance_after": "有伤疤", "clothing_after": "破损蓝衣"},
                {"chapter_number": 9, "appearance_after": "伤疤愈合", "clothing_after": "黑色轻甲"},
            ]},
            appearance="黑发",
            clothing="普通蓝衣",
        )
        state = resolve_asset_state(asset, 6)
        self.assertEqual(state["appearance"], "有伤疤")
        self.assertEqual(state["clothing"], "破损蓝衣")
        self.assertEqual(state["chapter_number"], 4)


if __name__ == "__main__":
    unittest.main()
