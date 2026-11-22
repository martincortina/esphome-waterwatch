"""Original meter photos, independent of the readings database."""

import json
import logging
import re
import threading
from datetime import datetime, timedelta
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

    def _legacy_folder(self, photo_id):
        return self.root / photo_id[:4] / photo_id[4:6] / photo_id[6:8] / photo_id

    def _paths(self, entry):
        captured = datetime.fromisoformat(entry["captured_at"])
        date = captured.date().isoformat()
        clock = captured.strftime("%H-%M-%S_%f")
        suffix = entry["id"].rsplit("-", 1)[1]
        stem = f"{date}_{clock}Z-{suffix}"
        folder = self.root / date
        return folder / f"{stem}.{entry['format']}", folder / f".{stem}.json"

    @staticmethod
    def _read_entry(metadata):
        try:
            entry = json.loads(metadata.read_text(encoding="utf-8"))
            if not re.fullmatch(PHOTO_ID, entry["id"]) or entry["format"] not in {"jpg", "png"}:
                return None
            entry["captured_at"] = timestamp(datetime.fromisoformat(entry["captured_at"]))
            if not isinstance(entry["meter_id"], str):
                return None
            return entry
        except (FileNotFoundError, ValueError, TypeError, KeyError):
            return None

    def _index(self, entries, entry, original, metadata):
        size = original.stat().st_size
        entries[entry["id"]] = {
            **entry,
            "file_name": original.name,
            "bytes": size,
            "storage_bytes": size + metadata.stat().st_size,
            "original_path": original,
            "metadata_path": metadata,
        }

    @staticmethod
    def _write_atomic(path, data):
        staging = path.with_name(f".waterwatch-{uuid4().hex}.tmp")
        output = staging.open("xb")
        try:
            with output:
                output.write(data)
            staging.replace(path)
        finally:
            staging.unlink(missing_ok=True)

    def _migrate_legacy(self, entries):
        for metadata in self.root.glob("*/*/*/*/photo.json"):
            if metadata.resolve() != metadata:
                continue
            entry = self._read_entry(metadata)
            if entry is None or metadata.parent != self._legacy_folder(entry["id"]):
                continue
            original = metadata.parent / ("original." + entry["format"])
            if original.resolve() != original:
                continue
            target, sidecar = self._paths(entry)
            try:
                if target.parent.resolve() != target.parent or sidecar.resolve() != sidecar:
                    raise OSError("Unexpected photo directory")
                if original.is_file():
                    # Keep the original and its metadata readable until the new
                    # metadata is in place. The original itself moves atomically.
                    if target.exists() or target.is_symlink():
                        raise OSError("Photo location already occupied")
                    target.parent.mkdir(exist_ok=True)
                    self._write_atomic(sidecar, metadata.read_bytes())
                    original.rename(target)
                    self._index(entries, entry, target, sidecar)
                elif entry["id"] not in entries:
                    continue
                metadata.unlink()
                self._remove_empty_folders(metadata.parent)
            except OSError:
                if original.is_file() and entry["id"] not in entries:
                    self._index(entries, entry, original, metadata)
                log.warning("Could not move a WaterWatch photo into its daily folder")

    def _remove_empty_folders(self, folder):
        while folder != self.root:
            try:
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent

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
                    and staging.parent == self._legacy_folder(photo_id).parent
                    and staging.resolve() == staging):
                for name in ("original.jpg", "original.png", "photo.json"):
                    (staging / name).unlink(missing_ok=True)
                if not any(staging.iterdir()):
                    staging.rmdir()
        entries = {}
        for metadata in self.root.glob("????-??-??/.*Z-*.json"):
            if metadata.resolve() != metadata:
                continue
            entry = self._read_entry(metadata)
            if entry is None:
                continue
            original, expected = self._paths(entry)
            if metadata != expected or original.resolve() != original:
                continue
            if original.is_file():
                self._index(entries, entry, original, metadata)
            else:
                metadata.unlink(missing_ok=True)
        for staging in self.root.glob("????-??-??/.waterwatch-*.tmp"):
            if re.fullmatch(r"\.waterwatch-[a-f0-9]{32}\.tmp", staging.name) and staging.resolve() == staging:
                staging.unlink(missing_ok=True)
        self._migrate_legacy(entries)
        self.entries = dict(sorted(entries.items()))
        self.used_bytes = sum(entry["storage_bytes"] for entry in self.entries.values())

    def _prune(self, incoming=0):
        cutoff = (utcnow() - timedelta(days=RETENTION_DAYS)).strftime("%Y%m%dT%H%M%S%fZ")
        while self.entries:
            photo_id = next(iter(self.entries))
            if photo_id >= cutoff and self.used_bytes + incoming <= MAX_BYTES:
                break
            entry = self.entries[photo_id]
            original, metadata = entry["original_path"], entry["metadata_path"]
            if original.parent.resolve() != original.parent:
                raise OSError("Unexpected photo directory")
            original.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            del self.entries[photo_id]
            self.used_bytes -= entry["storage_bytes"]
            self._remove_empty_folders(original.parent)

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
        original, sidecar = self._paths(entry)
        if original.parent.resolve() != original.parent:
            raise OSError("Unexpected photo directory")
        original.parent.mkdir(exist_ok=True)
        self._write_atomic(sidecar, metadata)
        try:
            self._write_atomic(original, data)
        except OSError:
            sidecar.unlink(missing_ok=True)
            raise
        previous_id = next(reversed(self.entries), "")
        self._index(self.entries, entry, original, sidecar)
        if photo_id < previous_id:
            self.entries = dict(sorted(self.entries.items()))
        self.used_bytes += size

    def page(self, meter_id, before, limit):
        with self.lock:
            self.maintain()
            rows = []
            private = {"storage_bytes", "original_path", "metadata_path"}
            if self.entries is not None:
                for photo_id in reversed(self.entries):
                    entry = self.entries[photo_id]
                    if (before is None or photo_id < before) and entry["meter_id"] == meter_id:
                        rows.append({key: value for key, value in entry.items() if key not in private})
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
            path = entry["original_path"]
            if path.resolve() != path or not path.is_file():
                return None
            return path.read_bytes(), extension
