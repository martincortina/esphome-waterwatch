import json
import sqlite3
import threading
from contextlib import contextmanager

from server.models import MeterConfig


class Store:
    """One SQLite writer; transaction + outbox is the storage-adapter boundary."""

    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
            CREATE TABLE IF NOT EXISTS meters(
                id TEXT PRIMARY KEY, config TEXT NOT NULL, token_hash TEXT NOT NULL,
                last_seen TEXT, rssi INTEGER
            );
            CREATE TABLE IF NOT EXISTS readings(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                meter_id TEXT NOT NULL REFERENCES meters(id), event_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, captured_at TEXT NOT NULL, received_at TEXT NOT NULL,
                raw_value TEXT, value_micro INTEGER, confidence REAL NOT NULL,
                accepted INTEGER NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL,
                rate_lph REAL, image_key TEXT, details TEXT NOT NULL,
                UNIQUE(meter_id,event_id)
            );
            CREATE INDEX IF NOT EXISTS reading_good ON readings(meter_id,accepted,seq DESC);
            CREATE TABLE IF NOT EXISTS outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS counter_checkpoints(
                meter_id TEXT PRIMARY KEY REFERENCES meters(id), snapshot TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deleted_events(
                meter_id TEXT NOT NULL REFERENCES meters(id), event_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, PRIMARY KEY(meter_id,event_id)
            );
            CREATE TABLE IF NOT EXISTS camera_frames(
                meter_id TEXT NOT NULL REFERENCES meters(id), camera_entity TEXT NOT NULL,
                image_sha256 TEXT NOT NULL, PRIMARY KEY(meter_id,camera_entity)
            );
        """)
        if self.db.execute("SELECT version FROM schema_version").fetchone()[0] != 1:
            raise RuntimeError("Unsupported database schema version")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def one(self, sql, params=()):
        with self.lock:
            row = self.db.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params)]

    def config(self, meter_id):
        row = self.one("SELECT config FROM meters WHERE id=?", (meter_id,))
        if row is None:
            raise KeyError(meter_id)
        return MeterConfig.model_validate_json(row["config"])

    def configs(self):
        return [
            MeterConfig.model_validate_json(r["config"])
            for r in self.all("SELECT config FROM meters ORDER BY id")
        ]

    def last_good(self, meter_id):
        current = self.one(
            "SELECT * FROM readings WHERE meter_id=? AND accepted=1 ORDER BY seq DESC LIMIT 1",
            (meter_id,),
        )
        saved = self.one("SELECT snapshot FROM counter_checkpoints WHERE meter_id=?", (meter_id,))
        checkpoint = json.loads(saved["snapshot"]) if saved else None
        return max((row for row in (current, checkpoint) if row), key=lambda row: row["seq"], default=None)

    @staticmethod
    def enqueue(db, topic, payload):
        # State is a retained snapshot, not an event stream. Coalesce while broker is offline.
        db.execute("DELETE FROM outbox WHERE topic=?", (topic,))
        db.execute("INSERT INTO outbox(topic,payload) VALUES(?,?)", (topic, json.dumps(payload)))

    def camera_frame(self, meter_id, camera_entity):
        saved = self.one("SELECT image_sha256 FROM camera_frames WHERE meter_id=? AND camera_entity=?", (meter_id, camera_entity))
        if saved:
            return saved["image_sha256"]
        # Upgrade fallback for captures recorded before the dedicated marker existed.
        row = self.one("SELECT json_extract(details,'$.capture.image_sha256') AS digest FROM readings WHERE meter_id=? AND json_extract(details,'$.capture.camera_entity')=? ORDER BY seq DESC LIMIT 1",
                       (meter_id, camera_entity))
        return row["digest"] if row else None

    def close(self):
        with self.lock:
            self.db.close()
