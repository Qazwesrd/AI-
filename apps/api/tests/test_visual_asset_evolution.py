import unittest

from app.models.visual import VisualAsset
from app.routers.visual import build_asset_prompt, resolve_asset_state
from app.routers.story import snapshot_asset_state


class VisualAssetEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.asset = VisualAsset(
            id=7,
            category="characters",
            name="主角",
            description="故事主角",
            appearance="黑色短发，少年体型",
            clothing="初始蓝色布衣",
            data={
                "evolution_history": [
                    {
                        "chapter_number": 12,
                        "event": "决战中受伤",
                        "change_type": "受伤",
                        "appearance_after": "左脸有一道伤疤",
                        "clothing_after": "蓝色布衣破损，左袖撕裂",
                        "holder": "",
                        "recorded_at": "2026-01-02T00:00:00",
                    },
                    {
                        "chapter_number": 20,
                        "event": "获得新装备",
                        "change_type": "装备升级",
                        "appearance_after": "左脸保留伤疤",
                        "clothing_after": "换成黑色轻甲，肩部有银色护甲",
                        "holder": "",
                        "recorded_at": "2026-01-03T00:00:00",
                    },
                ]
            },
        )

    def test_state_before_first_change_uses_base_design(self):
        state = resolve_asset_state(self.asset, 5)
        self.assertEqual(state["appearance"], "黑色短发，少年体型")
        self.assertEqual(state["clothing"], "初始蓝色布衣")
        self.assertFalse(state["is_evolved"])

    def test_state_at_chapter_15_uses_only_first_evolution(self):
        state = resolve_asset_state(self.asset, 15)
        self.assertEqual(state["chapter_number"], 12)
        self.assertEqual(state["appearance"], "左脸有一道伤疤")
        self.assertEqual(state["clothing"], "蓝色布衣破损，左袖撕裂")

    def test_state_at_chapter_25_uses_latest_evolution(self):
        state = resolve_asset_state(self.asset, 25)
        self.assertEqual(state["chapter_number"], 20)
        self.assertEqual(state["clothing"], "换成黑色轻甲，肩部有银色护甲")

    def test_chapter_prompt_excludes_future_changes(self):
        prompt = build_asset_prompt(self.asset, chapter_number=15)
        self.assertIn("蓝色布衣破损，左袖撕裂", prompt["positive_prompt"])
        self.assertNotIn("黑色轻甲", prompt["positive_prompt"])
        self.assertEqual(prompt["chapter_number"], 15)

    def test_storyboard_link_snapshot_uses_requested_chapter(self):
        snapshot = snapshot_asset_state(self.asset, 15)
        self.assertEqual(snapshot["chapter_number"], 12)
        self.assertEqual(snapshot["clothing"], "蓝色布衣破损，左袖撕裂")

    def test_unconfirmed_evolution_is_not_used_for_effective_state(self):
        asset = VisualAsset(
            id=8, category="characters", name="测试角色",
            appearance="基础外观", clothing="基础服装",
            data={"evolution_history": [{
                "chapter_number": 3, "event": "AI推断受伤",
                "appearance_after": "新增伤疤", "clothing_after": "衣服破损",
                "confirmed": False,
            }]},
        )
        state = resolve_asset_state(asset, 5)
        self.assertEqual(state["appearance"], "基础外观")
        self.assertEqual(state["clothing"], "基础服装")
        self.assertFalse(state["is_evolved"])


    def test_sparse_later_change_preserves_prior_confirmed_state(self):
        self.asset.data["evolution_history"].append({
            "chapter_number": 30,
            "event": "道具交给同伴",
            "change_type": "持有人变化",
            "holder": "同伴",
            "confirmed": True,
            "recorded_at": "2026-01-04T00:00:00",
        })
        state = resolve_asset_state(self.asset, 30)
        self.assertEqual(state["appearance"], "左脸保留伤疤")
        self.assertEqual(state["clothing"], "换成黑色轻甲，肩部有银色护甲")
        self.assertEqual(state["holder"], "同伴")

    def test_structured_character_and_prop_fields_are_resolved(self):
        self.asset.data["evolution_history"].append({
            "chapter_number": 25,
            "event": "人物成长并获得新装备",
            "age_after": "青年",
            "hairstyle_after": "高马尾",
            "injury_after": "右肩有旧伤",
            "equipment_after": "银色护腕",
            "condition_after": "护符已修复",
            "holder": "主角",
            "confirmed": True,
            "recorded_at": "2026-01-03T12:00:00",
        })
        state = resolve_asset_state(self.asset, 25)
        self.assertEqual(state["age"], "青年")
        self.assertEqual(state["hairstyle"], "高马尾")
        self.assertEqual(state["injury"], "右肩有旧伤")
        self.assertEqual(state["equipment"], "银色护腕")
        self.assertEqual(state["condition"], "护符已修复")

    def test_storyboard_snapshot_carries_forward_sparse_state(self):
        self.asset.data["evolution_history"].append({
            "chapter_number": 30, "event": "转交道具", "holder": "同伴",
            "confirmed": True, "recorded_at": "2026-01-04T00:00:00",
        })
        snapshot = snapshot_asset_state(self.asset, 30)
        self.assertEqual(snapshot["appearance"], "左脸保留伤疤")
        self.assertEqual(snapshot["clothing"], "换成黑色轻甲，肩部有银色护甲")
        self.assertEqual(snapshot["holder"], "同伴")

if __name__ == "__main__":
    unittest.main()
