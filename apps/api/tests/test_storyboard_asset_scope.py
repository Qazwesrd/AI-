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

    def test_effective_state_carries_forward_all_character_and_prop_fields(self):
        asset = SimpleNamespace(
            data={"evolution_history": [
                {"chapter_number": 2, "age_after": "少年", "hairstyle_after": "短发", "equipment_after": "木剑", "holder": "主角"},
                {"chapter_number": 5, "injury_after": "左臂受伤", "condition_after": "木剑折断"},
            ]},
            appearance="黑发", clothing="布衣",
        )
        state = resolve_asset_state(asset, 6)
        self.assertEqual(state["age"], "少年")
        self.assertEqual(state["hairstyle"], "短发")
        self.assertEqual(state["equipment"], "木剑")
        self.assertEqual(state["holder"], "主角")
        self.assertEqual(state["injury"], "左臂受伤")
        self.assertEqual(state["condition"], "木剑折断")
        self.assertEqual(state["chapter_number"], 5)

    def test_state_snapshot_at_earlier_chapter_excludes_future_changes(self):
        asset = SimpleNamespace(
            data={"evolution_history": [
                {"chapter_number": 3, "clothing_after": "青色长袍"},
                {"chapter_number": 8, "clothing_after": "银色铠甲", "equipment_after": "长枪"},
            ]},
            appearance="黑发", clothing="布衣",
        )
        state = resolve_asset_state(asset, 5)
        self.assertEqual(state["clothing"], "青色长袍")
        self.assertEqual(state["equipment"], "")
        self.assertEqual(state["chapter_number"], 3)

    def test_unconfirmed_change_is_not_used_for_effective_state(self):
        asset = SimpleNamespace(
            data={"evolution_history": [
                {"chapter_number": 2, "appearance_after": "伤疤", "clothing_after": "破衣", "confirmed": False}
            ]},
            appearance="原始外观", clothing="原始衣服",
        )
        state = resolve_asset_state(asset, 5)
        self.assertEqual(state["appearance"], "原始外观")
        self.assertEqual(state["clothing"], "原始衣服")
        self.assertFalse(state["is_evolved"])


if __name__ == "__main__":
    unittest.main()
