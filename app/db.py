from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CHAT_LOG_DIR = BASE_DIR / "chat_logs"
TRACE_LOG_DIR = CHAT_LOG_DIR / "traces"
DB_PATH = DATA_DIR / "app.sqlite3"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_LOG_DIR.mkdir(parents=True, exist_ok=True)

    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            CREATE TABLE IF NOT EXISTS agent_events (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                ON messages(conversation_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_agent_events_conversation_created
                ON agent_events(conversation_id, created_at);

            CREATE TABLE IF NOT EXISTS knowledge_sources (
                source TEXT PRIMARY KEY,
                index_name TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id TEXT PRIMARY KEY,
                index_name TEXT NOT NULL,
                source TEXT NOT NULL,
                source_file TEXT NOT NULL,
                chunk_type TEXT NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_index_name
                ON knowledge_chunks(index_name);

            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source
                ON knowledge_chunks(source);

            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_type
                ON knowledge_chunks(index_name, chunk_type);
            """
        )


def append_jsonl(record: dict[str, Any], created_at: str | None = None) -> None:
    CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = created_at or record.get("created_at") or utc_now()
    log_date = timestamp[:10]
    log_path = CHAT_LOG_DIR / f"{log_date}.jsonl"

    with log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        file.write("\n")


def write_trace(record: dict[str, Any], created_at: str | None = None) -> Path:
    timestamp = created_at or record.get("created_at") or utc_now()
    safe_timestamp = timestamp.replace(":", "").replace("-", "")
    conversation_id = str(record.get("conversation_id") or "unknown")
    trace_dir = TRACE_LOG_DIR / timestamp[:10]
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"{conversation_id}_{safe_timestamp}.json"
    trace_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    append_jsonl({"type": "trace", "path": str(trace_path), **record}, timestamp)
    return trace_path


def ensure_conversation(
    conversation_id: str | None,
    *,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    init_db()
    now = utc_now()
    resolved_id = conversation_id or new_id("conv")

    with get_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM conversations WHERE id = ?",
            (resolved_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO conversations (id, created_at, updated_at, title, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    resolved_id,
                    now,
                    now,
                    title,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
        else:
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, resolved_id),
            )

    return resolved_id


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_db()
    now = utc_now()
    message = {
        "id": new_id("msg"),
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "created_at": now,
        "metadata": metadata or {},
    }

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message["id"],
                conversation_id,
                role,
                content,
                now,
                json.dumps(message["metadata"], ensure_ascii=False, sort_keys=True),
            ),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )

    append_jsonl({"type": "message", **message}, now)
    return message


def save_agent_event(
    conversation_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_db()
    now = utc_now()
    event = {
        "id": new_id("event"),
        "conversation_id": conversation_id,
        "event_type": event_type,
        "payload": payload or {},
        "created_at": now,
    }

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO agent_events (id, conversation_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                conversation_id,
                event_type,
                json.dumps(event["payload"], ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )

    append_jsonl({"type": "agent_event", **event}, now)
    return event
