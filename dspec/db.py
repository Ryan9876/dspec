from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .config import db_path

_LOCK = threading.RLock()
STAGES = ("constitution", "requirements", "solution", "tasks")


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(path: Path | None = None) -> None:
    with _LOCK, connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_sessions (
              id TEXT PRIMARY KEY,
              bundle_name TEXT NOT NULL UNIQUE,
              project_type TEXT NOT NULL DEFAULT 'greenfield',
              status TEXT NOT NULL DEFAULT 'in_progress',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS spec_documents (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL REFERENCES project_sessions(id) ON DELETE CASCADE,
              spec_type TEXT NOT NULL CHECK(spec_type IN ('constitution','requirements','solution','tasks')),
              content TEXT NOT NULL,
              revision_number INTEGER NOT NULL,
              version_number INTEGER NOT NULL DEFAULT 1,
              quality_score REAL NOT NULL DEFAULT 0.0,
              review_json TEXT NOT NULL DEFAULT '{}',
              approval_status TEXT NOT NULL DEFAULT 'draft',
              created_at TEXT NOT NULL,
              UNIQUE(session_id, spec_type, revision_number)
            );
            CREATE INDEX IF NOT EXISTS idx_spec_latest ON spec_documents(session_id, spec_type, revision_number DESC);
            CREATE TABLE IF NOT EXISTS draft_buffers (
              session_id TEXT NOT NULL REFERENCES project_sessions(id) ON DELETE CASCADE,
              spec_type TEXT NOT NULL CHECK(spec_type IN ('constitution','requirements','solution','tasks')),
              content TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(session_id, spec_type)
            );
            CREATE TABLE IF NOT EXISTS interview_answers (
              session_id TEXT NOT NULL REFERENCES project_sessions(id) ON DELETE CASCADE,
              stage TEXT NOT NULL,
              question_id TEXT NOT NULL,
              selected_option_id TEXT,
              free_text_payload TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(session_id, stage, question_id)
            );
            CREATE TABLE IF NOT EXISTS audit_reports (
              id TEXT PRIMARY KEY,
              session_id TEXT REFERENCES project_sessions(id) ON DELETE SET NULL,
              repo_path TEXT NOT NULL,
              report_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_ast_cache (
              file_path TEXT PRIMARY KEY,
              file_hash TEXT NOT NULL,
              ast_summary_json TEXT NOT NULL,
              last_scanned_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _LOCK:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def create_session(bundle_name: str, project_type: str = "greenfield") -> dict[str, Any]:
    sid = str(uuid.uuid4())
    now = utcnow()
    with tx() as conn:
        conn.execute(
            "INSERT INTO project_sessions(id,bundle_name,project_type,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (sid, bundle_name, project_type, "in_progress", now, now),
        )
    return get_session(sid)


def get_session(session_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM project_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise KeyError(session_id)
        result = dict(row)
        specs: dict[str, Any] = {}
        for stage in STAGES:
            spec = conn.execute(
                "SELECT * FROM spec_documents WHERE session_id=? AND spec_type=? ORDER BY revision_number DESC LIMIT 1",
                (session_id, stage),
            ).fetchone()
            if spec:
                item = dict(spec)
                item["review"] = json.loads(item.pop("review_json"))
                specs[stage] = item
        answers = [dict(r) for r in conn.execute("SELECT * FROM interview_answers WHERE session_id=? ORDER BY stage,question_id", (session_id,))]
        drafts = {
            row["spec_type"]: {"content": row["content"], "updated_at": row["updated_at"]}
            for row in conn.execute(
                "SELECT spec_type,content,updated_at FROM draft_buffers WHERE session_id=?",
                (session_id,),
            )
        }
        result["specs"] = specs
        result["drafts"] = drafts
        result["answers"] = answers
        return result


def list_sessions() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM project_sessions ORDER BY updated_at DESC")]


def save_answer(session_id: str, stage: str, question_id: str, selected: str | None, free_text: str | None) -> None:
    if stage not in STAGES:
        raise ValueError("Invalid stage")
    now = utcnow()
    with tx() as conn:
        conn.execute(
            """INSERT INTO interview_answers(session_id,stage,question_id,selected_option_id,free_text_payload,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,stage,question_id) DO UPDATE SET
            selected_option_id=excluded.selected_option_id, free_text_payload=excluded.free_text_payload, updated_at=excluded.updated_at""",
            (session_id, stage, question_id, selected, free_text, now),
        )
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))


def save_draft_buffer(session_id: str, stage: str, content: str) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Invalid stage")
    now = utcnow()
    with tx() as conn:
        conn.execute(
            """INSERT INTO draft_buffers(session_id,spec_type,content,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(session_id,spec_type) DO UPDATE SET
            content=excluded.content, updated_at=excluded.updated_at""",
            (session_id, stage, content, now),
        )
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return {"stage": stage, "content": content, "updated_at": now}


def save_spec(session_id: str, stage: str, content: str, quality_score: float = 0.0, review: dict[str, Any] | None = None, approval_status: str = "draft") -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Invalid stage")
    if not content.strip():
        raise ValueError("Spec content cannot be empty")
    now = utcnow()
    with tx() as conn:
        current = conn.execute("SELECT COALESCE(MAX(revision_number),0) FROM spec_documents WHERE session_id=? AND spec_type=?", (session_id, stage)).fetchone()[0]
        revision = int(current) + 1
        sid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO spec_documents(id,session_id,spec_type,content,revision_number,version_number,quality_score,review_json,approval_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, session_id, stage, content, revision, 1, quality_score, json.dumps(review or {}), approval_status, now),
        )
        conn.execute(
            """INSERT INTO draft_buffers(session_id,spec_type,content,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(session_id,spec_type) DO UPDATE SET
            content=excluded.content, updated_at=excluded.updated_at""",
            (session_id, stage, content, now),
        )
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return get_session(session_id)["specs"][stage]


def update_spec_review(spec_id: str, score: float, review: dict[str, Any], approval_status: str | None = None) -> None:
    with tx() as conn:
        if approval_status:
            conn.execute("UPDATE spec_documents SET quality_score=?,review_json=?,approval_status=? WHERE id=?", (score, json.dumps(review), approval_status, spec_id))
        else:
            conn.execute("UPDATE spec_documents SET quality_score=?,review_json=? WHERE id=?", (score, json.dumps(review), spec_id))


def set_setting(key: str, value: Any) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
            (key, json.dumps(value), utcnow()),
        )


def get_setting(key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default


def cache_get(path: str, sha256: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT ast_summary_json FROM file_ast_cache WHERE file_path=? AND file_hash=?", (path, sha256)).fetchone()
        return json.loads(row[0]) if row else None


def cache_put(path: str, sha256: str, summary: dict[str, Any]) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO file_ast_cache(file_path,file_hash,ast_summary_json,last_scanned_at) VALUES(?,?,?,?) ON CONFLICT(file_path) DO UPDATE SET file_hash=excluded.file_hash,ast_summary_json=excluded.ast_summary_json,last_scanned_at=excluded.last_scanned_at",
            (path, sha256, json.dumps(summary), utcnow()),
        )
