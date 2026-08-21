from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import DB_PATH, ensure_dirs

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS library (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    folder TEXT NOT NULL,
    decade_override INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    taken_at TEXT,
    width INTEGER,
    height INTEGER,
    scanned_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_sha ON photos(sha256);
CREATE INDEX IF NOT EXISTS idx_photos_taken ON photos(taken_at);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    birth_year INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    x1 REAL NOT NULL,
    y1 REAL NOT NULL,
    x2 REAL NOT NULL,
    y2 REAL NOT NULL,
    det_score REAL NOT NULL,
    quality TEXT NOT NULL CHECK (quality IN ('ok', 'unidentifiable')),
    embedding BLOB,
    age_est REAL,
    sex_est TEXT,
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    cluster_id INTEGER,
    assigned_how TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);
CREATE INDEX IF NOT EXISTS idx_faces_cluster ON faces(cluster_id);
CREATE INDEX IF NOT EXISTS idx_faces_how_person ON faces(assigned_how, person_id);

CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN ('unknown', 'named', 'junk')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_merges (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    source_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

MIGRATIONS = [
    "ALTER TABLE photos ADD COLUMN integrity TEXT NOT NULL DEFAULT 'unchecked'",
    "ALTER TABLE photos ADD COLUMN size_bytes INTEGER",
    "ALTER TABLE person_merges ADD COLUMN source_name TEXT",
    "ALTER TABLE photos ADD COLUMN rotation INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE photos ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE people ADD COLUMN category TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE people ADD COLUMN nickname TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE photos ADD COLUMN comment TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE faces ADD COLUMN tag_x REAL",
    "ALTER TABLE faces ADD COLUMN tag_y REAL",
    "ALTER TABLE faces ADD COLUMN comment TEXT NOT NULL DEFAULT ''",
    """
    CREATE TABLE IF NOT EXISTS photo_tags (
        photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
        tag TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (photo_id, tag)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag)",
]


_ready: set[str] = set()


def _db_key(conn: sqlite3.Connection, path: Path | None = None) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        file = row["file"] if row else ""
        if file:
            return str(Path(file).resolve())
    except Exception:
        pass
    return str(Path(path or DB_PATH).resolve())


def connect(path: Path | None = None) -> sqlite3.Connection:
    ensure_dirs()
    db_path = path or DB_PATH
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-8192")
    conn.execute("PRAGMA busy_timeout=30000")
    init_db(conn, db_path)
    return conn


def init_db(conn: sqlite3.Connection, path: Path | None = None) -> None:
    key = _db_key(conn, path)
    if key in _ready:
        return
    conn.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.commit()
    _ready.add(key)


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()
