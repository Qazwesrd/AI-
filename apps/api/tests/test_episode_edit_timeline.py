import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.story import EpisodePlan, StoryboardShot, EpisodeEditTimeline
from app.models.visual import VisualAsset
from app.models.media import MediaFile, MediaTask
from app.routers.media import (
    EpisodeTimelineInput, get_episode_edit_timeline,
    save_episode_edit_timeline,
)
from fastapi import HTTPException


class EpisodeEditingTimelineTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            engine,
            tables=[
                EpisodePlan.__table__,
                StoryboardShot.__table__,
                EpisodeEditTimeline.__table__,
                VisualAsset.__table__,
                MediaTask.__table__,
                MediaFile.__table__,
            ],
        )
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        episode = EpisodePlan(
            project_id=1, episode_number=1, title="测试分集",
            duration_seconds=100,
        )
        self.db.add(episode)
        self.db.flush()
        self.episode_id = episode.id
        self.shot_a = StoryboardShot(
            project_id=1, episode_id=episode.id, shot_number=1,
            sort_order=1, duration_seconds=5,
        )
        self.shot_b = StoryboardShot(
            project_id=1, episode_id=episode.id, shot_number=2,
            sort_order=2, duration_seconds=6,
        )
        self.character = VisualAsset(
            category="characters", name="主角", confirmed=True,
        )
        self.character_two = VisualAsset(
            category="characters", name="对手", confirmed=True,
        )
        self.db.add_all([self.character, self.character_two])
        self.db.flush()
        self.shot_a.character_ids = [self.character.id]
        self.shot_b.character_ids = [self.character.id, self.character_two.id]
        self.shot_b.dialogue = "你终于来了。"
        self.db.add_all([self.shot_a, self.shot_b])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_default_timeline_uses_all_existing_shots(self):
        result = get_episode_edit_timeline(self.episode_id, self.db)
        self.assertEqual(result["timeline"]["shot_order"], [self.shot_a.id, self.shot_b.id])
        self.assertEqual(result["version"], 0)

    def test_save_and_reload_timeline_increments_version(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_b.id, self.shot_a.id],
            transitions=[{
                "after_shot_id": self.shot_b.id,
                "transition": "dissolve",
                "duration_seconds": 0.6,
            }],
            intro_title="序章",
            outro_title="未完待续",
            music_fade_in_seconds=1.5,
            music_fade_out_seconds=2.5,
            music_volume=0.2,
        )
        saved = save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(saved["version"], 1)
        reloaded = get_episode_edit_timeline(self.episode_id, self.db)
        self.assertEqual(reloaded["timeline"]["shot_order"], [self.shot_b.id, self.shot_a.id])
        self.assertEqual(reloaded["timeline"]["intro_title"], "序章")
        saved_again = save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(saved_again["version"], 2)

    def test_character_voice_profiles_are_saved_and_character_roster_is_returned(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            character_voice_profiles={str(self.character.id): "nova"},
        )
        save_episode_edit_timeline(self.episode_id, payload, self.db)
        reloaded = get_episode_edit_timeline(self.episode_id, self.db)
        self.assertEqual(
            reloaded["timeline"]["character_voice_profiles"],
            {str(self.character.id): "nova"},
        )
        self.assertIn(
            {"id": self.character.id, "name": "主角"},
            reloaded["characters"],
        )
        self.assertEqual(reloaded["shots"][0]["character_ids"], [self.character.id])

    def test_speaker_by_shot_id_is_saved_and_returned(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            speaker_by_shot_id={str(self.shot_b.id): self.character_two.id},
        )
        save_episode_edit_timeline(self.episode_id, payload, self.db)
        reloaded = get_episode_edit_timeline(self.episode_id, self.db)
        self.assertEqual(
            reloaded["timeline"]["speaker_by_shot_id"],
            {str(self.shot_b.id): self.character_two.id},
        )
        self.assertEqual(
            set(reloaded["shots"][1]["character_ids"]),
            {self.character.id, self.character_two.id},
        )

    def test_rejects_speaker_not_assigned_to_shot(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            speaker_by_shot_id={str(self.shot_a.id): self.character_two.id},
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_speaker_for_shot_outside_episode(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            speaker_by_shot_id={"99999": self.character.id},
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_voice_profile_for_character_outside_episode(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            character_voice_profiles={"99999": "nova"},
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_missing_or_foreign_shots(self):
        payload = EpisodeTimelineInput(shot_order=[self.shot_a.id])
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_duplicate_shot_ids(self):
        payload = EpisodeTimelineInput(shot_order=[self.shot_a.id, self.shot_a.id])
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_audio_owned_by_another_project(self):
        from pathlib import Path
        from unittest.mock import patch

        audio_path = "/managed/audio/foreign-music.mp3"
        self.db.add(MediaFile(
            name="foreign-music.mp3",
            category="audio",
            file_path=audio_path,
            metadata_json={"project_id": 2},
        ))
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            music_path=audio_path,
        )
        with (
            patch("app.routers.media.safe_storage_file", return_value=Path(audio_path)),
            patch("app.routers.media.probe_media", return_value={"streams": [{"codec_type": "audio"}]}),
        ):
            with self.assertRaises(HTTPException) as caught:
                save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("属于其他项目", caught.exception.detail)

    def test_allows_legacy_shared_audio_without_project_metadata(self):
        from pathlib import Path
        from unittest.mock import patch

        audio_path = "/managed/audio/shared-music.mp3"
        self.db.add(MediaFile(
            name="shared-music.mp3",
            category="audio",
            file_path=audio_path,
            metadata_json={},
        ))
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            music_path=audio_path,
        )
        with (
            patch("app.routers.media.safe_storage_file", return_value=Path(audio_path)),
            patch("app.routers.media.probe_media", return_value={"streams": [{"codec_type": "audio"}]}),
        ):
            saved = save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(saved["version"], 1)

    def test_rejects_sound_effect_owned_by_another_project(self):
        from pathlib import Path
        from unittest.mock import patch

        effect_path = "/managed/audio/foreign-effect.mp3"
        self.db.add(MediaFile(
            name="foreign-effect.mp3",
            category="audio",
            file_path=effect_path,
            metadata_json={"project_id": 2},
        ))
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            sound_effect_tracks=[{"path": effect_path, "name": "foreign effect"}],
        )
        with (
            patch("app.routers.media.safe_storage_file", return_value=Path(effect_path)),
            patch("app.routers.media.probe_media", return_value={"streams": [{"codec_type": "audio"}]}),
        ):
            with self.assertRaises(HTTPException) as caught:
                save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("属于其他项目", caught.exception.detail)

    def test_rejects_cut_with_nonzero_duration(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            transitions=[{
                "after_shot_id": self.shot_a.id,
                "transition": "cut",
                "duration_seconds": 0.5,
            }],
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
