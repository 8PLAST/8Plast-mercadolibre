from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


LOCAL_MAIN_DB = Path(__file__).resolve().parents[1] / "8plast_stock.db"


def database_path() -> Path:
    return Path(os.environ.get("WEBHOOK_DATABASE_PATH") or os.environ.get("DATABASE_PATH") or LOCAL_MAIN_DB)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS mercadolibre_webhook_events (
    id INTEGER PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    first_received_at TEXT NOT NULL,
    last_received_at TEXT NOT NULL,
    topic TEXT,
    resource TEXT,
    user_id TEXT,
    application_id TEXT,
    attempts INTEGER,
    sent TEXT,
    received TEXT,
    payload_json TEXT NOT NULL,
    delivery_count INTEGER NOT NULL DEFAULT 1,
    is_complete INTEGER NOT NULL DEFAULT 0 CHECK(is_complete IN (0,1)),
    processing_status TEXT NOT NULL DEFAULT 'RECEIVED'
);
CREATE TABLE IF NOT EXISTS mercadolibre_webhook_deliveries (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES mercadolibre_webhook_events(id),
    received_at TEXT NOT NULL,
    attempts INTEGER,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ml_webhook_status ON mercadolibre_webhook_events(processing_status, id);
"""


def connect(path: Path | None = None):
    selected = path or database_path()
    selected.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(selected), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def stable_event_key(payload: dict) -> str:
    """Ignora attempts/received para que un reintento conserve la misma identidad."""
    identity = {key: payload.get(key) for key in ("topic", "resource", "user_id", "application_id", "sent")}
    if not any(value not in (None, "") for value in identity.values()):
        identity = {key: value for key, value in payload.items() if key not in ("attempts", "received")}
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_delivery(payload: dict, path: Path | None = None) -> dict:
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    key = stable_event_key(payload); now = utc_now()
    complete = bool(payload.get("topic") and payload.get("resource") and payload.get("user_id") is not None)
    con = connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute("SELECT id FROM mercadolibre_webhook_events WHERE event_key=?", (key,)).fetchone()
        if existing:
            event_id = existing["id"]
            con.execute("""UPDATE mercadolibre_webhook_events SET last_received_at=?,attempts=?,received=?,
                payload_json=?,delivery_count=delivery_count+1 WHERE id=?""",
                (now, payload.get("attempts"), payload.get("received"), payload_json, event_id))
            duplicate = True
        else:
            cur = con.execute("""INSERT INTO mercadolibre_webhook_events
                (event_key,first_received_at,last_received_at,topic,resource,user_id,application_id,
                 attempts,sent,received,payload_json,is_complete)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (key,now,now,payload.get("topic"),payload.get("resource"),
                None if payload.get("user_id") is None else str(payload.get("user_id")),
                None if payload.get("application_id") is None else str(payload.get("application_id")),
                payload.get("attempts"),payload.get("sent"),payload.get("received"),payload_json,int(complete)))
            event_id=cur.lastrowid; duplicate=False
        con.execute("""INSERT INTO mercadolibre_webhook_deliveries(event_id,received_at,attempts,payload_json)
            VALUES (?,?,?,?)""",(event_id,now,payload.get("attempts"),payload_json))
        con.commit()
        return {"event_id":event_id,"event_key":key,"duplicate":duplicate,"complete":complete}
    except Exception:
        con.rollback(); raise
    finally:
        con.close()
