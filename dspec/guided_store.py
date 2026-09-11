from __future__ import annotations

import json
from typing import Any

from . import db


_DECISION_TYPES = {"architecture"}


def ensure_tables() -> None:
    """Create DS-CHG-002 local-only state tables without changing existing data."""
    with db.tx() as conn:
        conn.executescript(
            """
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


def list_engineering_decisions(session_id: str) -> list[dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        result: list[dict[str, Any]] = []
        for row in conn.execute(
            "SELECT * FROM engineering_decisions WHERE session_id=? ORDER BY decision_type,decision_id",
            (session_id,),
        ):
            item = dict(row)
            item["options"] = json.loads(item.pop("options_json"))
            item["custom"] = json.loads(item.pop("custom_json"))
            result.append(item)
        return result


def _selected_option(options: dict[str, Any], selected_option_id: str | None) -> dict[str, Any] | None:
    rows = options.get("options") if isinstance(options, dict) else None
    if not isinstance(rows, list):
        return None
    return next(
        (
            item
            for item in rows
            if isinstance(item, dict) and item.get("id") == selected_option_id
        ),
        None,
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
    if decision_type not in _DECISION_TYPES:
        raise ValueError("Unsupported engineering decision type.")
    if not decision_id.strip() or not source_context_sha256.strip():
        raise ValueError("Decision identity and source context are required.")

    chosen = _selected_option(options, selected_option_id)
    if status == "selected" and chosen is None:
        raise ValueError("Selected engineering option is missing from the comparison payload.")

    ensure_tables()
    now = db.utcnow()
    context_payload = json.dumps(
        {
            "decision_id": decision_id,
            "selected_option_id": selected_option_id,
            "selected_option": chosen,
            "custom": custom or {},
            "source_context_sha256": source_context_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with db.tx() as conn:
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
        # Mirror the selected decision into the existing review-context authority
        # so Solution/Tasks review evidence and Solution generation see the same
        # explicit selected architecture context.
        conn.execute(
            """INSERT INTO interview_answers(
                session_id,stage,question_id,selected_option_id,free_text_payload,updated_at
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(session_id,stage,question_id) DO UPDATE SET
                selected_option_id=excluded.selected_option_id,
                free_text_payload=excluded.free_text_payload,
                updated_at=excluded.updated_at""",
            (
                session_id,
                "solution",
                f"engineering-decision:{decision_type}:{decision_id}",
                selected_option_id,
                context_payload,
                now,
            ),
        )
        conn.execute("DELETE FROM execution_plans WHERE session_id=?", (session_id,))
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))

    return next(
        item
        for item in list_engineering_decisions(session_id)
        if item["decision_type"] == decision_type and item["decision_id"] == decision_id
    )


def stale_architecture_decision(session_id: str, reason: str) -> bool:
    """Retain the historical decision row but remove it from active spec authority."""
    ensure_tables()
    now = db.utcnow()
    with db.tx() as conn:
        row = conn.execute(
            """SELECT 1 FROM engineering_decisions
               WHERE session_id=? AND decision_type='architecture'
                 AND decision_id='primary-stack' AND status='selected'""",
            (session_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """UPDATE engineering_decisions
               SET status='stale', updated_at=?
               WHERE session_id=? AND decision_type='architecture' AND decision_id='primary-stack'""",
            (now, session_id),
        )
        conn.execute(
            """DELETE FROM interview_answers
               WHERE session_id=? AND stage='solution'
                 AND question_id='engineering-decision:architecture:primary-stack'""",
            (session_id,),
        )
        conn.execute("DELETE FROM execution_plans WHERE session_id=?", (session_id,))
        conn.execute("UPDATE project_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return True


def clear_session_guidance(session_id: str) -> None:
    """Retire active DS-CHG-002 state after a root product-intent change."""
    ensure_tables()
    with db.tx() as conn:
        conn.execute("DELETE FROM engineering_decisions WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM execution_plans WHERE session_id=?", (session_id,))
        conn.execute(
            """DELETE FROM interview_answers
               WHERE session_id=? AND stage='solution'
                 AND question_id LIKE 'engineering-decision:%'""",
            (session_id,),
        )


def record_concept_exposures(concepts: list[dict[str, str]]) -> None:
    if not concepts:
        return
    ensure_tables()
    now = db.utcnow()
    with db.tx() as conn:
        for concept in concepts:
            concept_id = str(concept.get("id") or "").strip()
            label = str(concept.get("label") or concept_id).strip()
            if not concept_id:
                continue
            conn.execute(
                """INSERT INTO concept_exposure(
                    concept_id,label,exposure_count,first_seen_at,last_seen_at,user_marked_understood
                ) VALUES(?,?,1,?,?,0)
                ON CONFLICT(concept_id) DO UPDATE SET
                    label=excluded.label,
                    exposure_count=concept_exposure.exposure_count+1,
                    last_seen_at=excluded.last_seen_at""",
                (concept_id, label, now, now),
            )


def list_concept_exposures(limit: int = 30) -> list[dict[str, Any]]:
    ensure_tables()
    safe_limit = max(1, min(int(limit), 100))
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT concept_id,label,exposure_count,first_seen_at,last_seen_at,user_marked_understood
                   FROM concept_exposure ORDER BY last_seen_at DESC LIMIT ?""",
                (safe_limit,),
            )
        ]


def explanation_depth(concept_id: str) -> str:
    ensure_tables()
    with db.connect() as conn:
        row = conn.execute(
            """SELECT exposure_count,user_marked_understood
               FROM concept_exposure WHERE concept_id=?""",
            (concept_id,),
        ).fetchone()
        if not row:
            return "full"
        if bool(row["user_marked_understood"]) or int(row["exposure_count"]) > 1:
            return "concise"
        return "full"


def save_execution_plan(
    session_id: str,
    source_snapshot_sha256: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    ensure_tables()
    now = db.utcnow()
    with db.tx() as conn:
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


def get_execution_plan(session_id: str) -> dict[str, Any] | None:
    ensure_tables()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT source_snapshot_sha256,plan_json,updated_at FROM execution_plans WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {**json.loads(row["plan_json"]), "updated_at": row["updated_at"]}


def clear_execution_plan(session_id: str) -> None:
    ensure_tables()
    with db.tx() as conn:
        conn.execute("DELETE FROM execution_plans WHERE session_id=?", (session_id,))


def augment_session(session: dict[str, Any]) -> dict[str, Any]:
    result = dict(session)
    result["engineering_decisions"] = list_engineering_decisions(str(session["id"]))
    result["execution_plan"] = get_execution_plan(str(session["id"]))
    return result
