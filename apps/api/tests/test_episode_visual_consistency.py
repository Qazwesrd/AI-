import unittest

from app.routers.story import build_episode_asset_consistency_report


class EpisodeVisualConsistencyTests(unittest.TestCase):
    def test_clean_confirmed_scene_and_character_links_are_ready(self):
        report = build_episode_asset_consistency_report([{
            "id": 1, "shot_number": 1, "scene_name": "圣域", "novel_id": 9,
            "links": [
                {"asset_id": 10, "role": "scene", "state_snapshot": {"appearance": "圣域"},
                 "newer_state_available": False,
                 "asset": {"name": "圣域", "category": "scenes", "confirmed": True, "data": {"source_novel_id": 9}}},
                {"asset_id": 11, "role": "character", "state_snapshot": {"appearance": "黑发"},
                 "newer_state_available": False,
                 "asset": {"name": "星矢", "category": "characters", "confirmed": True, "data": {"source_novel_id": 9}}},
            ],
        }])
        self.assertTrue(report["ready"])
        self.assertEqual(report["checked_link_count"], 2)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["warning_count"], 0)

    def test_missing_scene_link_and_unconfirmed_asset_are_warnings(self):
        report = build_episode_asset_consistency_report([{
            "id": 2, "shot_number": 3, "scene_name": "十二宫", "novel_id": 9,
            "links": [{
                "asset_id": 12, "role": "character", "state_snapshot": {"appearance": "红发"},
                "newer_state_available": False,
                "asset": {"name": "角色A", "category": "characters", "confirmed": False, "data": {}},
            }],
        }])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("missing_scene_asset", codes)
        self.assertIn("asset_not_confirmed", codes)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["warning_count"], 2)
        self.assertFalse(report["ready"])

    def test_wrong_role_category_and_foreign_novel_are_errors(self):
        report = build_episode_asset_consistency_report([{
            "id": 3, "shot_number": 4, "scene_name": "", "novel_id": 9,
            "links": [{
                "asset_id": 13, "role": "character", "state_snapshot": {"appearance": "城堡"},
                "newer_state_available": False,
                "asset": {"name": "敌方城堡", "category": "buildings", "confirmed": True, "data": {"source_novel_id": 10}},
            }],
        }])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("role_category_mismatch", codes)
        self.assertIn("foreign_novel_asset", codes)
        self.assertEqual(report["error_count"], 2)
        self.assertFalse(report["ready"])

    def test_missing_snapshot_and_newer_evolution_are_reported(self):
        report = build_episode_asset_consistency_report([{
            "id": 4, "shot_number": 5, "scene_name": "", "novel_id": 9,
            "links": [{
                "asset_id": 14, "role": "weapon", "state_snapshot": None,
                "newer_state_available": True,
                "asset": {"name": "天秤座武器", "category": "weapons", "confirmed": True, "data": {}},
            }],
        }])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("missing_state_snapshot", codes)
        self.assertIn("newer_state_available", codes)
        self.assertEqual(report["warning_count"], 2)

    def test_generic_visual_role_with_scene_category_satisfies_scene_link(self):
        report = build_episode_asset_consistency_report([{
            "id": 6, "shot_number": 7, "scene_name": "海皇神殿", "novel_id": 9,
            "links": [{
                "asset_id": 15, "role": "visual", "state_snapshot": {"appearance": "神殿"},
                "newer_state_available": False,
                "asset": {"name": "海皇神殿", "category": "scenes", "confirmed": True, "data": {}},
            }],
        }])
        self.assertNotIn("missing_scene_asset", {issue["code"] for issue in report["issues"]})

    def test_shot_without_any_visual_asset_is_reported(self):
        report = build_episode_asset_consistency_report([{
            "id": 5, "shot_number": 6, "scene_name": "海界神殿", "novel_id": 9, "links": [],
        }])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("no_visual_assets", codes)
        self.assertIn("missing_scene_asset", codes)
        self.assertEqual(report["checked_link_count"], 0)


if __name__ == "__main__":
    unittest.main()
