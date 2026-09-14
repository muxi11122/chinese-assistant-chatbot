from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import app.chat_service as chat_service
from app.db import DB_PATH


def fake_stream_reply(input_text: str):
    assert "[Teaching RAG]" in input_text
    assert "[Level Boundary RAG]" in input_text
    assert "时间状语位置" in input_text
    yield "我明白你的意思。"
    yield "这个句子先看时间词的位置。"


def main() -> None:
    chat_service.stream_reply = fake_stream_reply
    prepared = chat_service.prepare_chat_generation(
        "我昨天去图书馆学习中文早上。",
        history=[],
    )
    tokens = list(chat_service.stream_chat_reply(prepared))
    reply = "".join(tokens)
    chat_service.save_assistant_reply(prepared, reply)

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        events = connection.execute(
            """
            SELECT event_type, payload_json
            FROM agent_events
            WHERE conversation_id = ?
            ORDER BY created_at
            """,
            (prepared["conversation_id"],),
        ).fetchall()
        messages = connection.execute(
            """
            SELECT role, content, metadata_json
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at
            """,
            (prepared["conversation_id"],),
        ).fetchall()

    event_types = [row["event_type"] for row in events]
    context_payload = json.loads(events[0]["payload_json"])
    assistant_metadata = json.loads(messages[-1]["metadata_json"])

    assert event_types == ["rag_context_built", "rag_chat_completed"]
    assert [row["role"] for row in messages] == ["user", "assistant"]
    assert prepared["analysis"]["problem_type"] == "sentence_correction"
    assert prepared["analysis"]["language_point"] == "时间状语位置"
    assert len(context_payload["teaching_top_k"]) == 3
    assert len(context_payload["level_top_k"]) == 3
    assert context_payload["embedding_provider"] == "gcp"
    assert assistant_metadata["provider"] == "gcp"
    assert reply == "我明白你的意思。这个句子先看时间词的位置。"

    print(f"conversation_id={prepared['conversation_id']}")
    print(f"problem_type={prepared['analysis']['problem_type']}")
    print(f"language_point={prepared['analysis']['language_point']}")
    print(f"teaching_top_k={len(context_payload['teaching_top_k'])}")
    print(f"level_top_k={len(context_payload['level_top_k'])}")
    print(f"reply={reply}")


if __name__ == "__main__":
    main()
