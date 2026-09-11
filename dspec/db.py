from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager, nullcontext
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
            CREATE TABLE IF NOT EXISTS stage_invalidations (
              session_id TEXT NOT NULL REFERENCES project_sessions(id) ON DELETE CASCADE,
              stage TEXT NOT NULL CHECK(stage IN ('constitution','requirements','solution','tasks')),
              invalidated_at TEXT NOT NULL,
              reason TEXT NOT NULL,
              PRIMARY KEY(session_id, stage)
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
            CREATE TABLE IF NOT EXISTS engineering_decisions (
              session_id TEXT NOT NULL REFERENCES project_sessions(id) ON DELETE CASCADE,
              decision_type TEXT NOT NULL,
              decision_id TEXT NOT NULL,
              selected_option_id TEXT,
              options_json TEXT NOT NULL DEFAULT '{}',
              custom_json TEXT NOT NULL DEFAULT '{}',
              source_context_sha256 TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'selected',
              updated_at TEXT NOT NULL,
              PRIMARY KEY(session_id, decision_type, decision_id)
            );
            CREATE TABLE IF NOT EXISTS concept_exposure (
              concept_id TEXT PRIMARY KEY,
              label TEXT NOT NULL,
              exposure_count INTEGER NOT NULL DEFAULT 1,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              user_marked_understood INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS execution_plans (
              session_id TEXT PRIMARY KEY REFERENCES project_sessions(id) ON DELETE CASCADE,
              source_snapshot_sha256 TEXT NOT NULL,
              plan_json TEXT NOT NULL,
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


def _stage_invalidations(
    conn: sqlite3.Connection,
    session_id: str,
) -> dict[str, str]:
    return {
        row["stage"]: row["invalidated_at"]
        for row in conn.execute(
            "SELECT stage,invalidated_at FROM stage_invalidations WHERE session_id=?",
            (session_id,),
        )
    }


def _active_after_clause(column: str, cutoff: str | None) -> tuple[str, tuple[Any, ...]]:
    if not cutoff:
        return "", ()
    return f" AND {column}>?", (cutoff,)


def get_session(session_id: str, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    with nullcontext(connection) if connection is not None else connect() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        row = conn.execute("SELECT * FROM project_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise KeyError(session_id)
        result = dict(row)
        invalidations = _stage_invalidations(conn, session_id)
        specs: dict[str, Any] = {}
        for stage in STAGES:
            cutoff = invalidations.get(stage)
            clause, params = _active_after_clause("created_at", cutoff)
            spec = conn.execute(
                f"SELECT * FROM spec_documents WHERE session_id=? AND spec_type=?{clause} ORDER BY revision_number DESC LIMIT 1",
                (session_id, stage, *params),
            ).fetchone()
            if spec:
                item = dict(spec)
                item["review"] = json.loads(item.pop("review_json"))
                specs[stage] = item

        answers: list[dict[str, Any]] = []
        for answer in conn.execute(
            "SELECT * FROM interview_answers WHERE session_id=? ORDER BY stage,question_id",
            (session_id,),
        ):
            item = dict(answer)
            cutoff = invalidations.get(item["stage"])
            if cutoff and item["updated_at"] <= cutoff:
                continue
            answers.append(item)

        drafts: dict[str, dict[str, str]] = {}
        for draft in conn.execute(
            "SELECT spec_type,content,updated_at FROM draft_buffers WHERE session_id=?",
            (session_id,),
        ):
            cutoff = invalidations.get(draft["spec_type"])
            if cutoff and draft["updated_at"] <= cutoff:
                continue
            drafts[draft["spec_type"]] = {
                "content": draft["content"],
                "updated_at": draft["updated_at"],
            }

        decisions: list[dict[str, Any]] = []
        for decision_row in conn.execute(
            "SELECT * FROM engineering_decisions WHERE session_id=? ORDER BY decision_type,decision_id",
            (session_id,),
        ):
            item = dict(decision_row)
            item["options"] = json.loads(item.pop("options_json"))
            item["custom"] = json.loads(item.pop("custom_json"))
            decisions.append(item)

        plan_row = conn.execute(
            "SELECT source_snapshot_sha256,plan_json,updated_at FROM execution_plans WHERE session_id=?",
            (session_id,),
        ).fetchone()

        result["specs"] = specs
        result["drafts"] = drafts
        result["answers"] = answers
        result["engineering_decisions"] = decisions
        result["execution_plan"] = (
            {**json.loads(plan_row["plan_json"]), "updated_at": plan_row["updated_at"]}
            if plan_row else None
        )
        root = next(
            (
                answer for answer in answers
                if answer.get("stage") == "constitution"
                and answer.get("question_id") == "assistant-constitution"
            ),
            None,
        )
        result["intent_context_sha256"] = _intent_hash(
            root.get("selected_option_id") if root else None,
            root.get("free_text_payload") if root else None,
        )
        result["invalidations"] = [
            {"stage": stage, "invalidated_at": invalidated_at}
            for stage, invalidated_at in sorted(invalidations.items(), key=lambda item: STAGES.index(item[0]))
        ]
        for stage, spec in specs.items():
            review = spec["review"]
            if (review.get("semantic_status") == "PASS" or spec["approval_status"] == "approved") and not review_is_current(result, stage):
                spec["approval_status"] = "draft"
                spec["review"] = {
                    **review,
                    "passed": False,
                    "semantic_status": "STALE",
                    "semantic_error": "The saved review does not cover the current drafts and discovery answers. Save changed drafts and review again.",
                }
        return result


def review_context(session: dict[str, Any], stage: str) -> str:
    """Bind evidence to the exact active tier and higher-authority input snapshot."""
    stages = STAGES[:STAGES.index(stage) + 1]
    architecture_decisions = (
        sorted(
            [
                {
                    "decision_type": item.get("decision_type"),
                    "decision_id": item.get("decision_id"),
                    "selected_option_id": item.get("selected_option_id"),
                    "custom": item.get("custom"),
                    "source_context_sha256": item.get("source_context_sha256"),
                }
                for item in session.get("engineering_decisions", [])
                if item.get("decision_type") == "architecture"
            ],
            key=lambda item: str(item.get("decision_id") or ""),
        )
        if stage in {"solution", "tasks"} else []
    )
    context = {
        "session_id": session["id"],
        "specs": {s: {k: session.get("specs", {}).get(s, {}).get(k) for k in ("id", "content")} for s in stages},
        "drafts": {s: session.get("drafts", {}).get(s, {}).get("content") for s in stages},
        "engineering_decisions": architecture_decisions,
        "answers": sorted(
            [{k: answer.get(k) for k in ("stage", "question_id", "selected_option_id", "free_text_payload")}
             for answer in session.get("answers", []) if answer["stage"] in stages],
            key=lambda a: (a["stage"], a["question_id"]),
        ),
    }
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def pending_drafts(session: dict[str, Any], stage: str) -> list[str]:
    return [s for s in STAGES[:STAGES.index(stage) + 1]
            if s in session.get("drafts", {})
            and session["drafts"][s]["content"] != session.get("specs", {}).get(s, {}).get("content", "")]


def review_is_current(session: dict[str, Any], stage: str) -> bool:
    review = session.get("specs", {}).get(stage, {}).get("review", {})
    return bool(
        review.get("semantic_status") == "PASS" and review.get("passed") is True
        and review.get("context_sha256") == review_context(session, stage)
        and not pending_drafts(session, stage)
    )


class StateConflict(ValueError):
    """A slow model result no longer describes the user's current work."""


def require_context(conn: sqlite3.Connection, session_id: str, stage: str, expected: str) -> None:
    if review_context(get_session(session_id, conn), stage) != expected:
        raise StateConflict("Project inputs changed during the request. Current work was preserved; retry using the current draft.")


def list_sessions() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM project_sessions ORDER BY updated_at DESC")]


def _normalized_intent(selected: str | None, free_text: str | None) -> tuple[str, str]:
    return ((selected or "").strip(), (free_text or "").strip())


def _intent_hash(selected: str | None, free_text: str | None) -> str:
    normalized = _normalized_intent(selected, free_text)
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode()).hexdigest()


def _current_intent_hash(conn: sqlite3.Connection, session_id: str) -> str:
    row = conn.execute(
        """SELECT selected_option_id,free_text_payload
           FROM interview_answers
           WHERE session_id=? AND stage='constitution' AND question_id='assistant-constitution'""",
        (session_id,),
    ).fetchone()
    if not row:
        return _intent_hash(None, None)
    return _intent_hash(row["selected_option_id"], row["free_text_payload"])


def require_intent_context(
    conn: sqlite3.Connection,
    session_id: str,
    expected_intent_sha256: str,
) -> None:
    if _current_intent_hash(conn, session_id) != expected_intent_sha256:
        raise StateConflict(
            "Project intent changed while this edit was pending. The stale edit was not saved."
        )


def save_answer(
    session_id: str,
    stage: str,
    question_id: str,
    selected: str | None,
    free_text: str | None,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Invalid stage")

    invalidated_stages: list[str] = []
    now = utcnow()
    with tx() as conn:
        if stage == "constitution" and question_id == "assistant-constitution":
            previous = conn.execute(
                """SELECT selected_option_id,free_text_payload
                   FROM interview_answers
                   WHERE session_id=? AND stage='constitution' AND question_id='assistant-constitution'""",
                (session_id,),
            ).fetchone()
            changed = bool(
                previous
                and _normalized_intent(previous["selected_option_id"], previous["free_text_payload"])
                != _normalized_intent(selected, free_text)
            )
            if changed:
                invalidated_at = now
                invalidated_stages = list(STAGES)
                conn.executemany(
                    """INSERT INTO stage_invalidations(session_id,stage,invalidated_at,reason)
                       VALUES(?,?,?,?)
                       ON CONFLICT(session_id,stage) DO UPDATE SET
                       invalidated_at=excluded.invalidated_at,reason=excluded.reason""",
                    [
                        (
                            session_id,
                            current,
                            invalidated_at,
                            "Root product intent changed; prior active discovery/spec context is stale.",
                        )
                        for current in STAGES
                    ],
                )
                conn.execute("DELETE FROM draft_buffers WHERE session_id=?", (session_id,))
                conn.execute(
                    """DELETE FROM interview_answers
                       WHERE session_id=?
                         AND NOT (stage='constitution' AND question_id='assistant-constitution')""",
                    (session_id,),
                )
                # Architecture decisions and execution plans derive from the old
                # root intent. They are active planning authority, not immutable
                # history, so retire them with the old intent epoch.
                conn.execute("DELETE FROM engineering_decisions WHERE session_id=?", (session_id,))
                conn.execute("DELETE FROM execution_plans WHERE session_id=?", (session_id,))
                now = utcnow()

        conn.execute(
            """INSERT INTO interview_answers(session_id,stage,question_id,selected_option_id,free_text_payload,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,stage,question_id) DO UPDATE SET
            selected_option_id=excluded.selected_option_id, free_text_payload=excluded.free_text_payload, updated_at=excluded.updated_at""",
            (session_id, stage, question_id, selected, free_text, now),
        )
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return {
        "saved": True,
        "intent_invalidated": bool(invalidated_stages),
        "invalidated_stages": invalidated_stages,
    }


def save_draft_buffer(
    session_id: str,
    stage: str,
    content: str,
    expected_context: str | None = None,
    expected_intent_sha256: str | None = None,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Invalid stage")
    now = utcnow()
    with tx() as conn:
        if expected_context is not None:
            require_context(conn, session_id, stage, expected_context)
        if expected_intent_sha256 is not None:
            require_intent_context(conn, session_id, expected_intent_sha256)
        conn.execute(
            """INSERT INTO draft_buffers(session_id,spec_type,content,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(session_id,spec_type) DO UPDATE SET
            content=excluded.content, updated_at=excluded.updated_at""",
            (session_id, stage, content, now),
        )
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return {"stage": stage, "content": content, "updated_at": now}


def save_spec(
    session_id: str,
    stage: str,
    content: str,
    quality_score: float = 0.0,
    review: dict[str, Any] | None = None,
    approval_status: str = "draft",
    expected_context: str | None = None,
    expected_intent_sha256: str | None = None,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Invalid stage")
    if not content.strip():
        raise ValueError("Spec content cannot be empty")
    now = utcnow()
    with tx() as conn:
        if expected_context is not None:
            require_context(conn, session_id, stage, expected_context)
        if expected_intent_sha256 is not None:
            require_intent_context(conn, session_id, expected_intent_sha256)
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
        if stage in {"requirements", "solution", "tasks"}:
            conn.execute("DELETE FROM execution_plans WHERE session_id=?", (session_id,))
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return get_session(session_id)["specs"][stage]


def update_spec_review(spec_id: str, score: float, review: dict[str, Any], approval_status: str | None = None, connection: sqlite3.Connection | None = None) -> None:
    with nullcontext(connection) if connection is not None else tx() as conn:
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


def cache_snapshot(root_path: str) -> dict[str, tuple[str, dict[str, Any]]]:
    prefix = str(Path(root_path).resolve()) + os.sep
    with connect() as conn:
        rows = conn.execute(
            "SELECT file_path,file_hash,ast_summary_json FROM file_ast_cache WHERE file_path LIKE ?",
            (prefix + "%",),
        ).fetchall()
    return {
        row["file_path"]: (row["file_hash"], json.loads(row["ast_summary_json"]))
        for row in rows
    }


def cache_put_many(entries: list[tuple[str, str, dict[str, Any]]]) -> None:
    if not entries:
        return
    now = utcnow()
    rows = [(path, sha256, json.dumps(summary), now) for path, sha256, summary in entries]
    with tx() as conn:
        conn.executemany(
            """INSERT INTO file_ast_cache(file_path,file_hash,ast_summary_json,last_scanned_at)
            VALUES(?,?,?,?) ON CONFLICT(file_path) DO UPDATE SET
            file_hash=excluded.file_hash,
            ast_summary_json=excluded.ast_summary_json,
            last_scanned_at=excluded.last_scanned_at""",
            rows,
        )


def save_engineering_decision(
    session_id: str,
    decision_type: str,
    decision_id: str,
    selected_option_id: str | None,
    options: dict[str, Any],
    custom: dict[str, Any] | None,
    source_context_sha256: str,
    status: str = "selected",
) -> dict[str, Any]:
    if decision_type not in {"architecture"}:
        raise ValueError("Unsupported engineering decision type.")
    if not decision_id.strip() or not source_context_sha256.strip():
        raise ValueError("Decision identity and source context are required.")
    now = utcnow()
    with tx() as conn:
        conn.execute(
            """INSERT INTO engineering_decisions(
                session_id,decision_type,decision_id,selected_option_id,options_json,custom_json,
                source_context_sha256,status,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id,decision_type,decision_id) DO UPDATE SET
                selected_option_id=excluded.selected_option_id,
                options_json=excluded.options_json,
                custom_json=excluded.custom_json,
                source_context_sha256=excluded.source_context_sha256,
                status=excluded.status,
                updated_at=excluded.updated_at""",
            (
                session_id,
                decision_type,
                decision_id,
                selected_option_id,
                json.dumps(options),
                json.dumps(custom or {}),
                source_context_sha256,
                status,
                now,
            ),
        )
        conn.execute("DELETE FROM execution_plans WHERE session_id=?", (session_id,))
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return next(
        item for item in get_session(session_id)["engineering_decisions"]
        if item["decision_type"] == decision_type and item["decision_id"] == decision_id
    )


def record_concept_exposures(concepts: list[dict[str, str]]) -> None:
    if not concepts:
        return
    now = utcnow()
    with tx() as conn:
        for concept in concepts:
            concept_id = str(concept.get("id") or "").strip()
            label = str(concept.get("label") or concept_id).strip()
            if not concept_id:
                continue
            conn.execute(
                """INSERT INTO concept_exposure(concept_id,label,exposure_count,first_seen_at,last_seen_at,user_marked_understood)
                VALUES(?,?,1,?,?,0)
                ON CONFLICT(concept_id) DO UPDATE SET
                    label=excluded.label,
                    exposure_count=concept_exposure.exposure_count+1,
                    last_seen_at=excluded.last_seen_at""",
                (concept_id, label, now, now),
            )


def list_concept_exposures() -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            dict(row) for row in conn.execute(
                "SELECT concept_id,label,exposure_count,first_seen_at,last_seen_at,user_marked_understood FROM concept_exposure ORDER BY last_seen_at DESC"
            )
        ]


def save_execution_plan(
    session_id: str,
    source_snapshot_sha256: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    now = utcnow()
    with tx() as conn:
        conn.execute(
            """INSERT INTO execution_plans(session_id,source_snapshot_sha256,plan_json,updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                source_snapshot_sha256=excluded.source_snapshot_sha256,
                plan_json=excluded.plan_json,
                updated_at=excluded.updated_at""",
            (session_id, source_snapshot_sha256, json.dumps(plan), now),
        )
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return {**plan, "updated_at": now}


def clear_execution_plan(session_id: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM execution_plans WHERE session_id=?", (session_id,))


def get_execution_plan(session_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT source_snapshot_sha256,plan_json,updated_at FROM execution_plans WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {**json.loads(row["plan_json"]), "updated_at": row["updated_at"]}
