from datetime import timedelta

from fastapi.testclient import TestClient

import server.photo_archive as photo_archive
from server.config import Settings
from server.main import create_app
from server.models import FlashConfig, ImageSource, MeterConfig, utcnow
from server.ocr import Recognition
from server.photo_archive import PhotoArchive


def test_flash_archive_saves_each_attempt_and_preview_without_recording(tmp_path, client, image):
    media = tmp_path / "media"
    media.mkdir()

    class Camera:
        configured = True
        frames = []

        def snapshot(self, entity, **kwargs):
            frame = image + bytes([len(self.frames)])
            self.frames.append(frame)
            return frame

        def close(self):
            pass

    camera = Camera()
    pipeline = client.pipeline
    seen = []

    def run(data, cfg):
        seen.append(data)
        return Recognition("123.457", .98, "candidate") if len(seen) > 1 else Recognition(
            None, 0, "ambiguous_digit",
        )

    pipeline.run = run
    settings = Settings(tmp_path / "addon", "b" * 48, photo_archive_dir=media / "waterwatch")
    app = create_app(settings, pipeline=pipeline, start_background=False, ha_camera=camera)
    app.state.service.save_config(MeterConfig(
        id="house", name="House", source=ImageSource(
            kind="home_assistant", camera_entity="camera.meter", flash=FlashConfig(light_entity="light.meter"),
        ),
    ), create=True)
    with TestClient(app) as api:
        gallery = "/api/meters/house/photos"
        assert api.get(gallery).status_code == 401
        assert api.put("/api/photo-archive", json={"enabled": False}).status_code == 401
        api.headers["Authorization"] = "Bearer " + settings.admin_token
        assert api.get(gallery).json()["enabled"] is False
        assert api.put("/api/photo-archive", json={"enabled": True}).json() == {"enabled": True}
        reading = api.post("/api/meters/house/capture?test_only=true").json()
        assert reading["reason"] == "baseline_required"
        assert api.post("/api/meters/house/camera-image").content == camera.frames[2]
        first = api.get(gallery, params={"limit": 2}).json()
        second = api.get(gallery, params={"before": first["next_before"]}).json()
        photos = first["photos"] + second["photos"]
        assert [(p["source"], p["attempt"], p["reason"]) for p in photos] == [
            ("preview", None, None), ("test", 2, "baseline_required"), ("test", 1, "ambiguous_digit"),
        ]
        for photo, expected in zip(photos, reversed(camera.frames), strict=True):
            response = api.get(f"{gallery}/{photo['id']}")
            assert response.headers["content-type"] == "image/png"
            assert response.content == expected
            assert api.get(f"{gallery}/{photo['id']}", headers={"Authorization": ""}).status_code == 401
        uploaded = api.post("/api/meters/house/test", params={"captured_at": utcnow().isoformat()}, content=image)
        assert uploaded.status_code == 200
        photos = api.get(gallery).json()["photos"]
        assert len(photos) == 4 and photos[0]["source"] == "test" and photos[0]["attempt"] is None
        assert api.put("/api/photo-archive", json={"enabled": False}).json() == {"enabled": False}
        assert api.post("/api/meters/house/camera-image").status_code == 200
        assert api.get(gallery).json()["photos"] == photos
        assert api.get("/api/meters/house/history").json() == []
        assert app.state.service.store.all("SELECT * FROM outbox") == []
        reopened = PhotoArchive(settings.photo_archive_dir, settings.data_dir)
        assert reopened.page("house", None, 12)["enabled"] is False


def test_archive_purges_oldest_for_quota_then_expires_while_disabled(tmp_path, monkeypatch, image):
    archive = PhotoArchive(tmp_path / "waterwatch", tmp_path)
    now = utcnow()
    monkeypatch.setattr(photo_archive, "utcnow", lambda: now)
    monkeypatch.setattr(photo_archive, "MAX_BYTES", 2 * len(image))
    archive.set_enabled(True)
    archive.save("house", now, image, source="test")
    original = archive.page("house", None, 12)["photos"][0]["id"]
    now += timedelta(seconds=1)
    archive.save("house", now, image, source="reading")
    page = archive.page("house", None, 12)
    assert len(page["photos"]) == 1
    assert page["used_bytes"] <= page["limit_bytes"]
    assert archive.original("house", original) is None
    assert archive.original("other", page["photos"][0]["id"]) is None
    archive.set_enabled(False)
    now += timedelta(days=30, seconds=1)
    reopened = PhotoArchive(tmp_path / "waterwatch", tmp_path)
    assert reopened.page("house", None, 12)["photos"] == []
    assert not list((tmp_path / "waterwatch").rglob("original.*"))
