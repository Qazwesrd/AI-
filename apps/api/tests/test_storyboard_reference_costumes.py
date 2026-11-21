import unittest

from app.models.story import StoryboardShot
from app.routers.local_image_generation import (
    StoryboardConsistencyInput,
    _consistency_prompt_for_shot,
)


class StoryboardReferenceCostumeTests(unittest.TestCase):
    def test_costume_profiles_and_shot_assignments_are_normalized(self):
        profile = StoryboardConsistencyInput(
            costume_profiles={"001": {"battle-suit": " 红色战斗服，胸甲细节固定 "}},
            shot_costume_variants={"0205": {"001": "battle-suit"}},
            character_reference_images={"001": 88},
        )
        self.assertEqual(profile.costume_profiles, {"1": {"battle-suit": "红色战斗服，胸甲细节固定"}})
        self.assertEqual(profile.shot_costume_variants, {"205": {"1": "battle-suit"}})
        self.assertEqual(profile.character_reference_images, {"1": 88})

    def test_invalid_costume_variant_identifier_is_rejected(self):
        with self.assertRaises(ValueError):
            StoryboardConsistencyInput(costume_profiles={"1": {"黄金圣衣/版本": "金色圣衣"}})

    def test_prompt_contains_selected_costume_and_reference_asset(self):
        shot = StoryboardShot()
        shot.id = 205
        shot.shot_number = 3
        shot.character_ids = [1]
        shot.scene_asset_id = 2
        shot.prop_ids = []
        profile = {
            "costume_profiles": {"1": {"battle-suit": "金色黄金圣衣，胸甲和肩甲固定"}},
            "shot_costume_variants": {"205": {"1": "battle-suit"}},
            "character_reference_images": {"1": 88},
            "scene_reference_images": {"2": 99},
        }
        prompt = _consistency_prompt_for_shot(shot, profile)
        self.assertIn("battle-suit", prompt)
        self.assertIn("金色黄金圣衣", prompt)
        self.assertIn("本地素材库图片 #88", prompt)
        self.assertIn("本地素材库图片 #99", prompt)

    def test_reference_media_ids_must_be_positive_integers(self):
        with self.assertRaises(ValueError):
            StoryboardConsistencyInput(character_reference_images={"1": 0})


if __name__ == "__main__":
    unittest.main()
