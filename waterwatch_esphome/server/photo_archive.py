"""Original meter photos, independent of the readings database."""

import json
import logging
import re
import threading
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from server.models import timestamp, utcnow

log = logging.getLogger(__name__)
RETENTION_DAYS = 30
MAX_BYTES = 2_000_000_000
PHOTO_ID = r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}"


class PhotoArchive:
    def __init__(self, root: Path | None, data_dir: Path):
        self.root = root.absolute() if root is not None else None
        self.enabled = data_dir / "photo-archive-enabled"
        self.lock = threading.RLock()
        self.entries = None
        self.used_bytes = 0
        self.error = False

    def _folder(self, photo_id):
        return self.root / photo_id[:4] / photo_id[4:6] / photo_id[6:8] / photo_id

    def _load(self):
        if self.entries is not None:
            return
        self.root.mkdir(exist_ok=True)
        if self.root.resolve() != self.root:
            raise OSError("Unexpected photo archive location")
        # Staging directories are never visible to the gallery. Reclaim a
        # write interrupted by a restart, including its bytes before enforcing quota.
        for staging in self.root.glob("*/*/*/.*"):
            photo_id = staging.name[1:]
            if (re.fullmatch(PHOTO_ID, photo_id) and staging.is_dir()
                    and staging.parent == self._folder(photo_id).parent
                    and staging.resolve() == staging):
                for name in ("original.jpg", "original.png", "photo.json"):
                    (staging / name).unlink(missing_ok=True)
                if not any(staging.iterdir()):
                    staging.rmdir()
        entries = {}
        for metadata in self.root.glob("*/*/*/*/photo.json"):
            photo_id = metadata.parent.name
            if not re.fullmatch(PHOTO_ID, photo_id) or self._folder(photo_id) != metadata.parent:
                continue
            if metadata.resolve() != metadata:
                continue
            try:
                entry = json.loads(metadata.read_text(encoding="utf-8"))
                if entry["id"] != photo_id or entry["format"] not in {"jpg", "png"}:
                    continue
                original = metadata.parent / ("original." + entry["format"])
                if original.resolve() != original:
                    continue
                if not original.is_file():
                    metadata.unlink(missing_ok=True)
                    continue
                entry["bytes"] = original.stat().st_size
                entry["storage_bytes"] = entry["bytes"] + metadata.stat().st_size
                entries[photo_id] = entry
            except (FileNotFoundError, ValueError, TypeError, KeyError):
                continue
        self.entries = dict(sorted(entries.items()))
        self.used_bytes = sum(entry["storage_bytes"] for entry in self.entries.values())

    def _prune(self, incoming=0):
        cutoff = (utcnow() - timedelta(days=RETENTION_DAYS)).strftime("%Y%m%dT%H%M%S%fZ")
        while self.entries:
            photo_id = next(iter(self.entries))
            if photo_id >= cutoff and self.used_bytes + incoming <= MAX_BYTES:
                break
            entry = self.entries[photo_id]
            folder = self._folder(photo_id)
            if folder.resolve() != folder:
                raise OSError("Unexpected photo directory")
            (folder / ("original." + entry["format"])).unlink(missing_ok=True)
            (folder / "photo.json").unlink(missing_ok=True)
            del self.entries[photo_id]
            self.used_bytes -= entry["storage_bytes"]
            # Empty date folders can go too. Keep any additional media files.
            for directory in (folder, *list(folder.parents)[:3]):
                try:
                    directory.rmdir()
                except OSError:
                    break

    def maintain(self):
        if self.root is not None:
            with self.lock:
                self._load()
                self._prune()

    def set_enabled(self, enabled):
        with self.lock:
            if self.root is None:
                raise ValueError("Photo archive is unavailable")
            if enabled:
                self._load()
                self.enabled.touch()
            else:
                self.enabled.unlink(missing_ok=True)

    def save(self, meter_id, captured_at, data, *, source, result=None, attempt=None):
        if self.root is None:
            return
        if data.startswith(b"\xff\xd8\xff"):
            extension = "jpg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = "png"
        else:
            return
        with self.lock:
            if not self.enabled.exists():
                return
            try:
                self._load()
                self._save(meter_id, captured_at, data, extension, source, result, attempt)
                self.error = False
            except OSError:
                self.error = True
                log.warning("Could not save a photo to the WaterWatch archive")

    def _save(self, meter_id, captured_at, data, extension, source, result, attempt):
        photo_id = utcnow().strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex
        result = result or {}
        entry = {
            "id": photo_id,
            "meter_id": meter_id,
            "captured_at": timestamp(captured_at),
            "format": extension,
            "source": source,
            "attempt": attempt,
            "reason": result.get("reason"),
            "raw_value": result.get("raw_value"),
            "confidence": result.get("confidence"),
        }
        metadata = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        size = len(data) + len(metadata)
        if size > MAX_BYTES:
            raise OSError("Photo exceeds archive quota")
        self._prune(size)
        folder = self._folder(photo_id)
        if folder.parent.resolve() != folder.parent:
            raise OSError("Unexpected photo directory")
        folder.parent.mkdir(parents=True, exist_ok=True)
        staging = folder.with_name("." + photo_id)
        staging.mkdir()
        original = staging / ("original." + extension)
        sidecar = staging / "photo.json"
        try:
            original.write_bytes(data)
            sidecar.write_bytes(metadata)
            staging.rename(folder)
        except OSError:
            original.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            staging.rmdir()
            raise
        self.entries[photo_id] = {**entry, "bytes": len(data), "storage_bytes": size}
        self.used_bytes += size

    def page(self, meter_id, before, limit):
        with self.lock:
            self.maintain()
            rows = []
            if self.entries is not None:
                for photo_id in reversed(self.entries):
                    entry = self.entries[photo_id]
                    if (before is None or photo_id < before) and entry["meter_id"] == meter_id:
                        rows.append({key: value for key, value in entry.items() if key != "storage_bytes"})
                        if len(rows) > limit:
                            break
            return {
                "available": self.root is not None,
                "enabled": self.root is not None and self.enabled.exists(),
                "retention_days": RETENTION_DAYS,
                "limit_bytes": MAX_BYTES,
                "used_bytes": self.used_bytes,
                "write_error": self.error,
                "photos": rows[:limit],
                "next_before": rows[limit-1]["id"] if len(rows) > limit else None,
            }

    def original(self, meter_id, photo_id):
        with self.lock:
            self.maintain()
            entry = self.entries.get(photo_id) if self.entries is not None else None
            if entry is None or entry["meter_id"] != meter_id:
                return None
            extension = entry["format"]
            path = self._folder(photo_id) / ("original." + extension)
            if path.resolve() != path or not path.is_file():
                return None
            return path.read_bytes(), extension
