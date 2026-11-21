import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.story import EpisodePlan, StoryboardShot, EpisodeEditTimeline, StoryProject
from app.models.visual import VisualAsset
from app.models.media import MediaFile, MediaTask, SubtitleTrack
from app.routers.media import (
    EpisodeTimelineInput, SubtitleInput, get_episode_edit_timeline,
    save_episode_edit_timeline, create_subtitle,
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
                StoryProject.__table__,
                EpisodePlan.__table__,
                StoryboardShot.__table__,
                EpisodeEditTimeline.__table__,
                VisualAsset.__table__,
                MediaTask.__table__,
                MediaFile.__table__,
                SubtitleTrack.__table__,
            ],
        )
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        project = StoryProject(id=1, novel_id=1, name="测试项目")
        self.db.add(project)
        self.db.flush()
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

    def test_create_subtitle_persists_project_and_episode_ownership(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            with patch("app.routers.media.SUBTITLE_DIR", Path(temporary)):
                result = create_subtitle(
                    SubtitleInput(
                        name="本集字幕",
                        project_id=1,
                        episode_id=self.episode_id,
                        cues=[{"start": 0, "end": 1, "text": "测试字幕"}],
                    ),
                    self.db,
                )
            track = self.db.query(SubtitleTrack).filter(SubtitleTrack.id == result["id"]).first()
            self.assertEqual(track.project_id, 1)
            self.assertEqual(track.episode_id, self.episode_id)

    def test_create_subtitle_rejects_mismatched_project_and_episode(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        self.db.add(StoryProject(id=2, novel_id=1, name="其他项目"))
        self.db.commit()
        with tempfile.TemporaryDirectory() as temporary:
            with patch("app.routers.media.SUBTITLE_DIR", Path(temporary)):
                with self.assertRaises(HTTPException) as caught:
                    create_subtitle(
                        SubtitleInput(
                            name="错误归属字幕",
                            project_id=2,
                            episode_id=self.episode_id,
                            cues=[{"start": 0, "end": 1, "text": "测试字幕"}],
                        ),
                        self.db,
                    )
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(list(Path(temporary).glob("*.srt")), [])

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
        self.assertIn("不属于当前项目或分集", caught.exception.detail)

    def test_rejects_audio_owned_by_another_episode_in_same_project(self):
        from pathlib import Path
        from unittest.mock import patch

        audio_path = "/managed/audio/other-episode.mp3"
        self.db.add(MediaFile(
            name="other-episode.mp3",
            category="audio",
            file_path=audio_path,
            metadata_json={"project_id": 1, "episode_id": 999},
        ))
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            voice_path=audio_path,
        )
        with (
            patch("app.routers.media.safe_storage_file", return_value=Path(audio_path)),
            patch("app.routers.media.probe_media", return_value={"streams": [{"codec_type": "audio"}]}),
        ):
            with self.assertRaises(HTTPException) as caught:
                save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("其他项目", caught.exception.detail)

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

    def test_rejects_subtitle_track_owned_by_another_project(self):
        track = SubtitleTrack(
            name="foreign.srt",
            file_path="/managed/subtitles/foreign.srt",
            project_id=2,
            episode_id=99,
        )
        self.db.add(track)
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            subtitle_track_id=track.id,
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("其他项目", caught.exception.detail)

    def test_rejects_subtitle_track_owned_by_another_episode_in_same_project(self):
        track = SubtitleTrack(
            name="other-episode.srt",
            file_path="/managed/subtitles/other-episode.srt",
            project_id=1,
            episode_id=999,
        )
        self.db.add(track)
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            subtitle_track_id=track.id,
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("其他分集", caught.exception.detail)

    def test_allows_legacy_unscoped_subtitle_track(self):
        track = SubtitleTrack(
            name="legacy.srt",
            file_path="/managed/subtitles/legacy.srt",
        )
        self.db.add(track)
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            subtitle_track_id=track.id,
        )
        saved = save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(saved["timeline"]["subtitle_track_id"], track.id)

    def test_get_timeline_suppresses_foreign_subtitle_track_from_legacy_data(self):
        track = SubtitleTrack(
            name="foreign.srt",
            file_path="/managed/subtitles/foreign.srt",
            project_id=2,
            episode_id=99,
        )
        self.db.add(track)
        self.db.flush()
        record = EpisodeEditTimeline(
            episode_id=self.episode_id,
            edit_data={
                "shot_order": [self.shot_a.id, self.shot_b.id],
                "subtitle_track_id": track.id,
            },
            version=1,
        )
        self.db.add(record)
        self.db.commit()
        result = get_episode_edit_timeline(self.episode_id, self.db)
        self.assertNotIn("subtitle_track_id", result["timeline"])
        self.assertIn("其他项目", result["subtitle_track_warning"])

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
