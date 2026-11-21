import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.models.media import MediaFile
from app.models.story import StoryProject, EpisodePlan, StoryboardShot, StoryboardMediaLink, StoryboardMediaRevision
from app.routers.local_image_generation import (
    LocalImageGenerateInput, _checkpoint_names, build_txt2img_workflow,
    generate_local_image, get_local_image_task, LocalImageArchiveInput,
    _safe_output_image, _download_comfyui_output, archive_local_image_task_output,
    _attach_media_to_shot, _remove_media_link_from_shot, _replace_shot_media,
)


class LocalImageGenerationTests(unittest.TestCase):
    def test_checkpoint_allowlist_parses_installed_models(self):
        data = {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model.safetensors", "../bad", "中文模型"]]}}}}
        self.assertEqual(_checkpoint_names(data), ["model.safetensors"])

    def test_workflow_has_expected_nodes_and_connections(self):
        payload = LocalImageGenerateInput(checkpoint="model.safetensors", prompt="一位少年站在山巅", confirm_local_generation=True)
        workflow = build_txt2img_workflow(payload, 123)
        self.assertEqual(workflow["4"]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(workflow["3"]["inputs"]["seed"], 123)
        self.assertEqual(workflow["8"]["inputs"]["samples"], ["3", 0])
        self.assertEqual(workflow["9"]["class_type"], "SaveImage")

    def test_generation_requires_explicit_confirmation(self):
        payload = LocalImageGenerateInput(checkpoint="model.safetensors", prompt="A cinematic mountain", confirm_local_generation=False)
        with self.assertRaises(HTTPException) as caught:
            generate_local_image(payload)
        self.assertEqual(caught.exception.status_code, 400)

    def test_generation_refuses_uninstalled_checkpoint(self):
        payload = LocalImageGenerateInput(checkpoint="missing.safetensors", prompt="A cinematic mountain", confirm_local_generation=True)
        with patch("app.routers.local_image_generation._local_request", return_value={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["other.safetensors"]]}}}}):
            with self.assertRaises(HTTPException) as caught:
                generate_local_image(payload)
        self.assertEqual(caught.exception.status_code, 404)

    def test_generation_queues_only_to_local_comfyui(self):
        payload = LocalImageGenerateInput(checkpoint="model.safetensors", prompt="A cinematic mountain", confirm_local_generation=True)
        with patch("app.routers.local_image_generation._local_request", side_effect=[
            {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model.safetensors"]]}}}},
            {"prompt_id": "12345678-1234-1234-1234-123456789abc"},
        ]) as request:
            result = generate_local_image(payload)
        self.assertTrue(result["local_only"])
        self.assertEqual(result["status"], "queued")
        self.assertEqual(request.call_args_list[1].args[0], "/prompt")

    def test_generation_rejects_dimensions_not_multiple_of_64_before_local_call(self):
        payload = LocalImageGenerateInput(checkpoint="model.safetensors", prompt="A cinematic mountain", width=700, confirm_local_generation=True)
        with patch("app.routers.local_image_generation._local_request") as request:
            with self.assertRaises(HTTPException) as caught:
                generate_local_image(payload)
        self.assertEqual(caught.exception.status_code, 400)
        request.assert_not_called()

    def test_checkpoint_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            LocalImageGenerateInput(checkpoint="../outside.safetensors", prompt="A cinematic mountain")

    def test_status_rejects_invalid_task_id(self):
        with self.assertRaises(HTTPException) as caught:
            get_local_image_task("../etc/passwd")
        self.assertEqual(caught.exception.status_code, 400)


    def test_archive_rejects_path_traversal_and_non_output_files(self):
        for item in (
            {"filename": "../secret.png", "subfolder": "", "type": "output"},
            {"filename": "safe.png", "subfolder": "../secret", "type": "output"},
            {"filename": "safe.png", "subfolder": "folder\\escape", "type": "output"},
            {"filename": "safe.png", "subfolder": "", "type": "temp"},
        ):
            with self.subTest(item=item), self.assertRaises(HTTPException):
                _safe_output_image(item)

    def test_archive_download_checks_image_magic_bytes(self):
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-payload"
        with patch("app.routers.local_image_generation.OPENER.open") as opened:
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, limit): return png
            opened.return_value = Response()
            raw, mime, suffix = _download_comfyui_output("frame.png", "")
        self.assertEqual(raw, png)
        self.assertEqual(mime, "image/png")
        self.assertEqual(suffix, ".png")

    def test_archive_rejects_output_not_owned_by_task(self):
        prompt_id = "12345678-1234-1234-1234-123456789abc"
        payload = LocalImageArchiveInput(filename="other.png")
        history = {prompt_id: {
            "status": {"completed": True},
            "outputs": {"9": {"images": [{"filename": "actual.png", "subfolder": "", "type": "output"}]}},
        }}
        with (
            patch("app.routers.local_image_generation._local_request", return_value=history),
            patch("app.routers.local_image_generation._download_comfyui_output") as download,
        ):
            with self.assertRaises(HTTPException) as caught:
                archive_local_image_task_output(prompt_id, payload, db=None)
        self.assertEqual(caught.exception.status_code, 404)
        download.assert_not_called()




class _FakeQuery:
    def __init__(self, rows, model):
        self.rows = rows
        self.model = model

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.rows.get(self.model)

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        value = self.rows.get(self.model, [])
        return value if isinstance(value, list) else ([value] if value else [])


class _FakeDB:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.added = []
        self.deleted = []
        self.committed = False

    def query(self, model):
        return _FakeQuery(self.rows, model)

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        self.committed = True


class StoryboardImageLinkTests(unittest.TestCase):
    def _rows(self, link=None, episode_project_id=7):
        project = StoryProject()
        project.id = 7
        shot = StoryboardShot()
        shot.id = 11
        shot.project_id = 7
        shot.episode_id = 13
        episode = EpisodePlan()
        episode.id = 13
        episode.project_id = episode_project_id
        return {StoryboardShot: shot, StoryProject: project, EpisodePlan: episode, StoryboardMediaLink: link}

    def test_attach_archived_image_to_valid_shot(self):
        db = _FakeDB(self._rows())
        media = MediaFile()
        media.id = 19
        link, existed = _attach_media_to_shot(db, media, 11)
        self.assertFalse(existed)
        self.assertEqual((link.project_id, link.episode_id, link.shot_id, link.media_file_id), (7, 13, 11, 19))
        self.assertEqual(db.added, [link])

    def test_attach_is_idempotent(self):
        existing = StoryboardMediaLink()
        db = _FakeDB(self._rows(link=existing))
        link, existed = _attach_media_to_shot(db, MediaFile(), 11)
        self.assertTrue(existed)
        self.assertIs(link, existing)
        self.assertEqual(db.added, [])

    def test_attach_rejects_missing_shot(self):
        db = _FakeDB({StoryboardShot: None})
        with self.assertRaises(HTTPException) as caught:
            _attach_media_to_shot(db, MediaFile(), 404)
        self.assertEqual(caught.exception.status_code, 404)

    def test_attach_rejects_cross_project_episode_mismatch(self):
        db = _FakeDB(self._rows(episode_project_id=8))
        with self.assertRaises(HTTPException) as caught:
            _attach_media_to_shot(db, MediaFile(), 11)
        self.assertEqual(caught.exception.status_code, 409)


    def test_unlink_removes_only_the_association(self):
        link = StoryboardMediaLink()
        link.media_file_id = 19
        db = _FakeDB({StoryboardShot: self._rows()[StoryboardShot], StoryboardMediaLink: link})
        result = _remove_media_link_from_shot(db, 11, 21)
        self.assertTrue(result["unlinked"])
        self.assertEqual(result["media_file_id"], 19)
        self.assertEqual(db.deleted, [link])
        self.assertTrue(db.committed)

    def test_replace_keeps_link_and_records_old_and_new_versions(self):
        link = StoryboardMediaLink()
        link.id = 21
        link.media_file_id = 19
        replacement = MediaFile()
        replacement.id = 20
        db = _FakeDB({
            StoryboardShot: self._rows()[StoryboardShot],
            StoryboardMediaLink: link,
            MediaFile: replacement,
            StoryboardMediaRevision: [],
        })
        result = _replace_shot_media(db, 11, 21, 20)
        self.assertTrue(result["replaced"])
        self.assertEqual(link.media_file_id, 20)
        revisions = [item for item in db.added if isinstance(item, StoryboardMediaRevision)]
        self.assertEqual([(item.version_number, item.media_file_id) for item in revisions], [(1, 19), (2, 20)])
        self.assertTrue(db.committed)

    def test_replace_rejects_missing_library_image(self):
        link = StoryboardMediaLink()
        link.id = 21
        link.media_file_id = 19
        db = _FakeDB({StoryboardShot: self._rows()[StoryboardShot], StoryboardMediaLink: link, MediaFile: None})
        with self.assertRaises(HTTPException) as caught:
            _replace_shot_media(db, 11, 21, 404)
        self.assertEqual(caught.exception.status_code, 404)

    def test_unlink_rejects_link_not_found(self):
        db = _FakeDB({StoryboardShot: self._rows()[StoryboardShot], StoryboardMediaLink: None})
        with self.assertRaises(HTTPException) as caught:
            _remove_media_link_from_shot(db, 11, 404)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(db.deleted, [])

    def test_archive_input_accepts_optional_shot_id(self):
        data = LocalImageArchiveInput(filename="frame.png", shot_id=11)
        self.assertEqual(data.shot_id, 11)


if __name__ == "__main__":
    unittest.main()
