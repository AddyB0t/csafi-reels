import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS reels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reel_key TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL,
        source_url TEXT,
        media_url TEXT,
        sender_id TEXT,
        caption TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        stage TEXT,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        video_sha256 TEXT,
        folder_name TEXT,
        topic TEXT,
        creator TEXT,
        summary TEXT,
        tags TEXT,
        resources TEXT,
        analysis TEXT,
        duration_s REAL,
        processing_s REAL,
        duplicate_of INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        filed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reels_status ON reels(status)",
    "CREATE INDEX IF NOT EXISTS idx_reels_sha ON reels(video_sha256)",
    """
    CREATE TABLE IF NOT EXISTS reel_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reel_id INTEGER NOT NULL,
        ts TEXT NOT NULL,
        level TEXT NOT NULL,
        message TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_reel ON reel_events(reel_id)",
    """
    CREATE TABLE IF NOT EXISTS webhook_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        signature_ok INTEGER NOT NULL,
        reels_found INTEGER NOT NULL,
        body TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reel_id INTEGER,
        ts TEXT NOT NULL,
        stage TEXT NOT NULL,
        model TEXT NOT NULL,
        seconds REAL NOT NULL,
        prompt_tokens INTEGER,
        output_tokens INTEGER,
        ok INTEGER NOT NULL,
        error TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kv (
        k TEXT PRIMARY KEY,
        v TEXT
    )
    """,
]

STATUSES = ("queued", "downloading", "analysing", "writing", "filed", "failed", "duplicate")
IN_PROGRESS = ("downloading", "analysing", "writing")
JSON_COLS = ("tags", "resources", "analysis")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self.tx() as c:
            for stmt in SCHEMA:
                c.execute(stmt)

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self._conn()
        with self._write_lock:
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, args).fetchall()

    # ---------- reels ----------
    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        for col in JSON_COLS:
            if d.get(col):
                try:
                    d[col] = json.loads(d[col])
                except json.JSONDecodeError:
                    pass
        return d

    def enqueue(
        self,
        *,
        reel_key: str,
        source: str,
        source_url: str | None = None,
        media_url: str | None = None,
        sender_id: str | None = None,
        caption: str | None = None,
    ) -> tuple[int, bool]:
        """Returns (reel_id, created). An existing reel is never re-queued here."""
        ts = now()
        with self.tx() as c:
            row = c.execute("SELECT id, media_url FROM reels WHERE reel_key=?", (reel_key,)).fetchone()
            if row:
                if media_url and not row["media_url"]:
                    c.execute("UPDATE reels SET media_url=?, updated_at=? WHERE id=?", (media_url, ts, row["id"]))
                return row["id"], False
            cur = c.execute(
                """INSERT INTO reels (reel_key, source, source_url, media_url, sender_id, caption,
                   status, created_at, updated_at) VALUES (?,?,?,?,?,?, 'queued', ?, ?)""",
                (reel_key, source, source_url, media_url, sender_id, caption, ts, ts),
            )
            return int(cur.lastrowid), True

    def claim_next(self) -> dict[str, Any] | None:
        with self.tx() as c:
            row = c.execute("SELECT id FROM reels WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            c.execute(
                "UPDATE reels SET status='downloading', stage='download', error=NULL, attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        return self.get(row["id"])

    def update(self, reel_id: int, **fields: Any) -> None:
        if not fields:
            return
        for col in JSON_COLS:
            if col in fields and not isinstance(fields[col], (str, type(None))):
                fields[col] = json.dumps(fields[col], ensure_ascii=False)
        fields["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE reels SET {cols} WHERE id=?", (*fields.values(), reel_id))

    def get(self, reel_id: int) -> dict[str, Any] | None:
        return self._row(self._conn().execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone())

    def get_by_key(self, key: str) -> dict[str, Any] | None:
        return self._row(self._conn().execute("SELECT * FROM reels WHERE reel_key=?", (key,)).fetchone())

    def find_filed_by_sha(self, sha: str, exclude_id: int) -> dict[str, Any] | None:
        return self._row(
            self._conn()
            .execute("SELECT * FROM reels WHERE video_sha256=? AND status='filed' AND id<>?", (sha, exclude_id))
            .fetchone()
        )

    def folder_names(self, exclude_id: int | None = None) -> set[str]:
        rows = self.q("SELECT id, folder_name FROM reels WHERE folder_name IS NOT NULL")
        return {r["folder_name"].lower() for r in rows if r["id"] != exclude_id}

    def list_reels(
        self, *, q: str = "", status: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        where, args = [], []
        if status:
            where.append("status=?")
            args.append(status)
        if q:
            like = f"%{q.lower()}%"
            where.append(
                "(lower(coalesce(topic,'')) LIKE ? OR lower(coalesce(summary,'')) LIKE ? OR lower(coalesce(creator,'')) LIKE ?"
                " OR lower(coalesce(tags,'')) LIKE ? OR lower(coalesce(resources,'')) LIKE ? OR lower(coalesce(caption,'')) LIKE ?"
                " OR lower(coalesce(source_url,'')) LIKE ?)"
            )
            args += [like] * 7
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.q(f"SELECT count(*) AS n FROM reels {clause}", tuple(args))[0]["n"]
        rows = self.q(f"SELECT * FROM reels {clause} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset))
        return [self._row(r) for r in rows], total

    def filed_reels(self) -> list[dict[str, Any]]:
        return [self._row(r) for r in self.q("SELECT * FROM reels WHERE status='filed' ORDER BY filed_at DESC, id DESC")]

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for r in self.q("SELECT status, count(*) AS n FROM reels GROUP BY status"):
            out[r["status"]] = r["n"]
        return out

    def requeue(self, reel_id: int) -> bool:
        with self.tx() as c:
            cur = c.execute(
                "UPDATE reels SET status='queued', stage=NULL, error=NULL, updated_at=? WHERE id=? AND status IN ('failed','filed','duplicate')",
                (now(), reel_id),
            )
            return cur.rowcount > 0

    def reset_interrupted(self) -> int:
        with self.tx() as c:
            cur = c.execute(
                f"UPDATE reels SET status='queued', updated_at=? WHERE status IN ({','.join('?' * len(IN_PROGRESS))})",
                (now(), *IN_PROGRESS),
            )
            return cur.rowcount

    # ---------- events / logs ----------
    def event(self, reel_id: int, message: str, level: str = "info") -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO reel_events (reel_id, ts, level, message) VALUES (?,?,?,?)",
                (reel_id, now(), level, message[:2000]),
            )

    def events(self, reel_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.q("SELECT * FROM reel_events WHERE reel_id=? ORDER BY id", (reel_id,))]

    def log_webhook(self, body: str, signature_ok: bool, reels_found: int) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO webhook_log (ts, signature_ok, reels_found, body) VALUES (?,?,?,?)",
                (now(), int(signature_ok), reels_found, body[:8000]),
            )
            c.execute("DELETE FROM webhook_log WHERE ts < datetime('now', '-14 days')")

    def log_llm(
        self,
        *,
        reel_id: int | None,
        stage: str,
        model: str,
        seconds: float,
        ok: bool,
        prompt_tokens: int | None = None,
        output_tokens: int | None = None,
        error: str | None = None,
    ) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO llm_calls (reel_id, ts, stage, model, seconds, prompt_tokens, output_tokens, ok, error)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (reel_id, now(), stage, model, round(seconds, 2), prompt_tokens, output_tokens, int(ok), error),
            )

    def kv_get(self, k: str) -> str | None:
        rows = self.q("SELECT v FROM kv WHERE k=?", (k,))
        return rows[0]["v"] if rows else None

    def kv_set(self, k: str, v: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
