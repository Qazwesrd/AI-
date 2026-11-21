import unittest

from app.models.story import StoryProject, StoryboardShot
from app.routers.local_image_generation import (
    StoryboardConsistencyInput,
    _consistency_preflight,
    _consistency_prompt_for_shot,
)


class StoryboardConsistencyTests(unittest.TestCase):
    def test_profile_input_normalizes_asset_ids_and_descriptions(self):
        profile = StoryboardConsistencyInput(
            visual_style="  国漫电影风格  ",
            character_profiles={"001": " 银色长发、蓝色眼睛 "},
        )
        self.assertEqual(profile.visual_style, "国漫电影风格")
        self.assertEqual(profile.character_profiles, {"1": "银色长发、蓝色眼睛"})

    def test_profile_input_rejects_non_numeric_asset_ids(self):
        with self.assertRaises(ValueError):
            StoryboardConsistencyInput(character_profiles={"hero": "silver hair"})

    def test_prompt_contains_shared_style_and_referenced_assets_only(self):
        shot = StoryboardShot()
        shot.id = 10
        shot.shot_number = 2
        shot.scene_name = "圣域"
        shot.character_ids = [1, 2]
        shot.scene_asset_id = 5
        shot.prop_ids = [9]
        profile = {
            "visual_style": "统一国漫电影质感",
            "negative_prompt": "避免发色变化",
            "character_profiles": {"1": "银色长发，蓝色眼睛"},
            "scene_profiles": {"5": "白色大理石神殿"},
            "prop_profiles": {"9": "金色圣剑"},
        }
        prompt = _consistency_prompt_for_shot(shot, profile)
        self.assertIn("统一国漫电影质感", prompt)
        self.assertIn("银色长发，蓝色眼睛", prompt)
        self.assertIn("白色大理石神殿", prompt)
        self.assertIn("金色圣剑", prompt)
        self.assertIn("避免发色变化", prompt)

    def test_preflight_reports_missing_character_scene_and_prop_profiles(self):
        project = StoryProject()
        project.id = 7
        shot = StoryboardShot()
        shot.id = 10
        shot.shot_number = 1
        shot.character_ids = [1, 2]
        shot.scene_asset_id = 5
        shot.prop_ids = [9]
        result = _consistency_preflight(project, [shot], {
            "character_profiles": {"1": "银发"},
            "scene_profiles": {},
            "prop_profiles": {"9": "圣剑"},
        })
        self.assertFalse(result["ready"])
        self.assertEqual(result["missing_profiles"]["characters"], ["2"])
        self.assertEqual(result["missing_profiles"]["scenes"], ["5"])
        self.assertEqual(result["missing_profiles"]["props"], [])
        self.assertEqual(result["shot_count"], 1)


if __name__ == "__main__":
    unittest.main()
