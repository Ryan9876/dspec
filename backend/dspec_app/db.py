from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

STAGES = ("constitution", "requirements", "solution", "tasks")
_lock = threading.RLock()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_dir() -> Path:
    path = Path(os.environ.get("DSPEC_RUNTIME_DIR", "~/.dspec/runtime")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return Path(os.environ.get("DSPEC_DB_PATH", str(runtime_dir() / "dspec.db"))).expanduser()


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with _lock:
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


def init_db() -> None:
    with transaction() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_sessions (
                id TEXT PRIMARY KEY,
                bundle_name TEXT NOT NULL UNIQUE,
                project_type TEXT NOT NULL DEFAULT 'greenfield',
                status TEXT NOT NULL DEFAULT 'in_progress',
                active_stage TEXT NOT NULL DEFAULT 'constitution',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stage_answers (
                session_id TEXT NOT NULL REFERENCES project_sessions(id) ON DELETE CASCADE,
                stage TEXT NOT NULL,
                answers_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(session_id, stage)
            );
            CREATE TABLE IF NOT EXISTS spec_documents (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES project_sessions(id) ON DELETE CASCADE,
                spec_type TEXT NOT NULL,
                content TEXT NOT NULL,
                revision_number INTEGER NOT NULL DEFAULT 1,
                version_number INTEGER NOT NULL DEFAULT 1,
                quality_score REAL NOT NULL DEFAULT 0.0,
                approved INTEGER NOT NULL DEFAULT 0,
                review_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, spec_type)
            );
            CREATE TABLE IF NOT EXISTS audit_reports (
                id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES project_sessions(id) ON DELETE SET NULL,
                repo_path TEXT NOT NULL,
                governance_security_score REAL NOT NULL,
                requirements_clarity_score REAL NOT NULL,
                architecture_consistency_score REAL NOT NULL,
                test_task_coverage_score REAL NOT NULL,
                composite_score REAL NOT NULL,
                structural_diff_md TEXT NOT NULL,
                upgrade_spec_md TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_ast_cache (
                file_path TEXT PRIMARY KEY,
                file_hash TEXT NOT NULL,
                ast_summary_json TEXT NOT NULL,
                last_scanned_at TEXT NOT NULL
            );
            """
        )


def unique_bundle_name(conn: sqlite3.Connection, requested: str) -> str:
    base = "-".join(requested.strip().lower().replace("_", "-").split()) or "untitled-project"
    name = base
    suffix = 2
    while conn.execute("SELECT 1 FROM project_sessions WHERE bundle_name=?", (name,)).fetchone():
        name = f"{base}-{suffix}"
        suffix += 1
    return name


def create_session(bundle_name: str, project_type: str = "greenfield") -> dict:
    now = utcnow()
    session_id = str(uuid.uuid4())
    with transaction() as conn:
        name = unique_bundle_name(conn, bundle_name)
        conn.execute(
            "INSERT INTO project_sessions(id,bundle_name,project_type,status,active_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (session_id, name, project_type, "in_progress", "constitution", now, now),
        )
        for stage in STAGES:
            conn.execute(
                "INSERT INTO stage_answers(session_id,stage,answers_json,updated_at) VALUES(?,?,?,?)",
                (session_id, stage, "{}", now),
            )
            conn.execute(
                "INSERT INTO spec_documents(id,session_id,spec_type,content,revision_number,version_number,quality_score,approved,review_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), session_id, stage, "", 1, 1, 0.0, 0, "{}", now, now),
            )
    return get_session(session_id)


def get_session(session_id: str) -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM project_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        answer_rows = conn.execute("SELECT * FROM stage_answers WHERE session_id=?", (session_id,)).fetchall()
        spec_rows = conn.execute("SELECT * FROM spec_documents WHERE session_id=?", (session_id,)).fetchall()
        answers = {r["stage"]: json.loads(r["answers_json"]) for r in answer_rows}
        specs = {
            r["spec_type"]: {
                "content": r["content"],
                "revision": r["revision_number"],
                "version": r["version_number"],
                "quality_score": r["quality_score"],
                "approved": bool(r["approved"]),
                "review": json.loads(r["review_json"] or "{}"),
                "updated_at": r["updated_at"],
            }
            for r in spec_rows
        }
        return {**dict(row), "answers": answers, "specs": specs}
    finally:
        conn.close()


def save_answers(session_id: str, stage: str, answers: dict) -> dict:
    if stage not in STAGES:
        raise ValueError("invalid stage")
    now = utcnow()
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM project_sessions WHERE id=?", (session_id,)).fetchone():
            raise KeyError(session_id)
        conn.execute(
            "UPDATE stage_answers SET answers_json=?,updated_at=? WHERE session_id=? AND stage=?",
            (json.dumps(answers, separators=(",", ":")), now, session_id, stage),
        )
        conn.execute("UPDATE project_sessions SET active_stage=?,updated_at=? WHERE id=?", (stage, now, session_id))
    return get_session(session_id)


def save_spec(session_id: str, stage: str, content: str, quality_score: float, review: dict) -> dict:
    if stage not in STAGES:
        raise ValueError("invalid stage")
    now = utcnow()
    with transaction() as conn:
        row = conn.execute(
            "SELECT revision_number FROM spec_documents WHERE session_id=? AND spec_type=?", (session_id, stage)
        ).fetchone()
        if row is None:
            raise KeyError(session_id)
        revision = int(row["revision_number"]) + 1
        conn.execute(
            "UPDATE spec_documents SET content=?,revision_number=?,quality_score=?,approved=0,review_json=?,updated_at=? WHERE session_id=? AND spec_type=?",
            (content, revision, float(quality_score), json.dumps(review), now, session_id, stage),
        )
        conn.execute("UPDATE project_sessions SET active_stage=?,updated_at=? WHERE id=?", (stage, now, session_id))
    return get_session(session_id)


def approve_spec(session_id: str, stage: str) -> dict:
    now = utcnow()
    with transaction() as conn:
        row = conn.execute(
            "SELECT content,quality_score,review_json FROM spec_documents WHERE session_id=? AND spec_type=?", (session_id, stage)
        ).fetchone()
        if row is None:
            raise KeyError(session_id)
        review = json.loads(row["review_json"] or "{}")
        if not row["content"].strip():
            raise ValueError("draft is empty")
        if float(row["quality_score"]) < 0.90:
            raise ValueError("quality score is below 0.90")
        if review.get("must_fix"):
            raise ValueError("must-fix findings remain")
        conn.execute(
            "UPDATE spec_documents SET approved=1,updated_at=? WHERE session_id=? AND spec_type=?", (now, session_id, stage)
        )
    return get_session(session_id)
