from __future__ import annotations

import re
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Callable

from app.db import ensure_conversation, save_agent_event, save_message, utc_now, write_trace
from app.model_client import (
    embed_texts,
    get_embedding_model,
    get_embedding_provider,
    get_llm_model,
    get_llm_provider,
    stream_reply,
)
from app.rag_index import require_active_indexes, search_index_by_embedding
from prompt_loader import build_prompt_bundle, detect_problem_type


LANGUAGE_POINT_PATTERNS = (
    (re.compile(r"[“\"]([^”\"]+)[”\"]\s*(?:是什么意思|什么意思|怎么用|可以怎么说)"), 1),
    (re.compile(r"(?:这个词|词语)\s*[“\"]?([^”\"？?，,。\s]+)"), 1),
    (re.compile(r"([\u4e00-\u9fff]{1,8})\s*(?:是什么意思|什么意思|怎么用)"), 1),
    (re.compile(r"为什么\s*(.+?)(?:[？?。]|$)"), 1),
)

PROBLEM_TYPE_LABELS = {
    "vocabulary_question": "词汇用法",
    "grammar_question": "语法结构",
    "sentence_correction": "句子纠错",
    "reading_expression": "阅读表达理解",
    "expression_naturalness": "表达自然度",
}

StatusCallback = Callable[[str, str, Mapping[str, Any] | None], None]


def emit_status(
    callback: StatusCallback | None,
    stage: str,
    message: str,
    extra: Mapping[str, Any] | None = None,
) -> None:
    print(f"[chat] {stage}: {message}", flush=True)
    if callback:
        callback(stage, message, extra)


def analyze_message(message: str) -> dict[str, str]:
    problem_type = detect_problem_type(message)
    language_point = extract_language_point(message, problem_type)
    return {
        "problem_type": problem_type,
        "language_point": language_point,
    }


def extract_language_point(message: str, problem_type: str) -> str:
    compact = " ".join(message.strip().split())
    for pattern, group_no in LANGUAGE_POINT_PATTERNS:
        match = pattern.search(compact)
        if match:
            point = clean_language_point(match.group(group_no))
            if point:
                return point

    if problem_type == "sentence_correction":
        if any(word in compact for word in ("昨天", "今天", "明天", "早上", "晚上", "上午", "下午")):
            return "时间状语位置"
        return "句子语序和表达自然度"
    if problem_type == "expression_naturalness":
        return "表达自然度和搭配"
    if problem_type == "reading_expression":
        return "课文表达理解"
    if problem_type == "grammar_question":
        return "语法结构"
    return "词汇意义和用法"


def clean_language_point(value: str) -> str:
    cleaned = re.sub(r"^(这个|词语|句子|表达)", "", value.strip())
    cleaned = re.sub(r"[，,。？?！!：:].*$", "", cleaned).strip()
    return cleaned[:40]


def build_retrieval_query(message: str, analysis: Mapping[str, str]) -> str:
    language_point = analysis.get("language_point", "").strip()
    if language_point and language_point not in message:
        return f"{language_point}\n{message.strip()}"
    return message.strip()


def format_reference_for_prompt(result: Mapping[str, Any]) -> dict[str, Any]:
    metadata = result.get("metadata") or {}
    heading_parts = [
        result.get("source_file"),
        metadata.get("article_title"),
        metadata.get("section_title"),
        result.get("chunk_type"),
    ]
    heading = " / ".join(str(part) for part in heading_parts if part)
    content = str(result.get("content") or "").strip()
    if result.get("chunk_type") in {"question_block", "answer"}:
        content = "教学参考（不是直接给学生的练习答案）：" + content
    return {
        "heading": heading,
        "content": content,
        "score": result.get("score"),
        "source": result.get("source"),
        "chunk_type": result.get("chunk_type"),
        "metadata": metadata,
    }


def trace_result(result: Mapping[str, Any]) -> dict[str, Any]:
    metadata = result.get("metadata") or {}
    return {
        "id": result.get("id"),
        "index_name": result.get("index_name"),
        "score": round(float(result.get("score") or 0.0), 6),
        "source": result.get("source"),
        "source_file": result.get("source_file"),
        "chunk_type": result.get("chunk_type"),
        "heading": result.get("heading"),
        "content": result.get("content"),
        "metadata": metadata,
    }


def summarize_results(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for result in results:
        metadata = result.get("metadata") or {}
        summary.append(
            {
                "id": result.get("id"),
                "index_name": result.get("index_name"),
                "score": round(float(result.get("score") or 0.0), 6),
                "source_file": result.get("source_file"),
                "chunk_type": result.get("chunk_type"),
                "article_id": metadata.get("article_id"),
                "section_id": metadata.get("section_id"),
                "question_no": metadata.get("question_no"),
            }
        )
    return summary


def prepare_chat_generation(
    message: str,
    *,
    conversation_id: str | None = None,
    history: Sequence[Mapping[str, str]] | None = None,
    status_callback: StatusCallback | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    emit_status(status_callback, "conversation", "正在保存本轮消息")
    resolved_conversation_id = ensure_conversation(
        conversation_id,
        title=message.strip()[:40] or "Untitled conversation",
        metadata={"source": "rag_chat"},
    )
    save_message(
        resolved_conversation_id,
        "user",
        message,
        metadata={"source": "api_chat"},
    )

    emit_status(status_callback, "analyze", "正在分析问题类型和语言点")
    analysis = analyze_message(message)
    retrieval_query = build_retrieval_query(message, analysis)
    emit_status(
        status_callback,
        "index",
        "正在检查当前 embedding 模型对应的 RAG 索引",
        {
            "embedding_provider": get_embedding_provider(),
            "embedding_model": get_embedding_model(),
        },
    )
    index_counts = require_active_indexes()
    emit_status(
        status_callback,
        "query_embedding",
        "正在生成本轮问题的 query embedding",
        {"index_counts": index_counts},
    )
    query_embedding = embed_texts([retrieval_query])[0]
    emit_status(status_callback, "retrieval", "正在检索 teaching_index top 3")
    teaching_results = search_index_by_embedding(query_embedding, "teaching_index", top_k=3)
    emit_status(status_callback, "retrieval", "正在检索 level_index top 3")
    level_results = search_index_by_embedding(query_embedding, "level_index", top_k=3)
    emit_status(status_callback, "prompt", "正在拼接 runtime context 和 prompt")
    teaching_refs = [format_reference_for_prompt(result) for result in teaching_results]
    level_refs = [format_reference_for_prompt(result) for result in level_results]
    prompt_bundle = build_prompt_bundle(
        message,
        problem_type=analysis["problem_type"],
        history=history,
        teaching_refs=teaching_refs,
        level_refs=level_refs,
        detected_point=analysis,
    )
    trace_created_at = utc_now()

    event_payload = {
        "provider": get_llm_provider(),
        "model": get_llm_model(),
        "embedding_provider": get_embedding_provider(),
        "embedding_model": get_embedding_model(),
        "rag_enabled": True,
        "prompt_mode": "kernel_workflow_runtime",
        "problem_type": analysis["problem_type"],
        "problem_type_label": PROBLEM_TYPE_LABELS.get(analysis["problem_type"], analysis["problem_type"]),
        "language_point": analysis["language_point"],
        "retrieval_query": retrieval_query,
        "teaching_top_k": summarize_results(teaching_results),
        "level_top_k": summarize_results(level_results),
    }
    save_agent_event(resolved_conversation_id, "rag_context_built", event_payload)
    emit_status(
        status_callback,
        "ready",
        "RAG context 已准备好，准备调用模型流式生成",
        {"elapsed_seconds": round(time.perf_counter() - started_at, 2)},
    )

    return {
        "conversation_id": resolved_conversation_id,
        "created_at": trace_created_at,
        "user_message": message,
        "history": list(history or []),
        "analysis": analysis,
        "retrieval": {
            "retrieval_query": retrieval_query,
            "index_counts": index_counts,
            "query_embedding_provider": get_embedding_provider(),
            "query_embedding_model": get_embedding_model(),
            "query_embedding_dimensions": len(query_embedding),
            "teaching_results": [trace_result(result) for result in teaching_results],
            "level_results": [trace_result(result) for result in level_results],
            "teaching_refs_for_prompt": teaching_refs,
            "level_refs_for_prompt": level_refs,
        },
        "model": {
            "provider": get_llm_provider(),
            "model": get_llm_model(),
            "embedding_provider": get_embedding_provider(),
            "embedding_model": get_embedding_model(),
        },
        "prompt": prompt_bundle["prompt"],
        "prompt_bundle": prompt_bundle,
        "event_payload": event_payload,
    }


def stream_chat_reply(prepared: Mapping[str, Any]) -> Iterator[str]:
    yield from stream_reply(str(prepared["prompt"]))


def save_assistant_reply(prepared: Mapping[str, Any], reply: str) -> None:
    event_payload = dict(prepared["event_payload"])
    save_message(
        str(prepared["conversation_id"]),
        "assistant",
        reply,
        metadata={
            "source": "rag_chat",
            "provider": get_llm_provider(),
            "model": get_llm_model(),
            "embedding_provider": get_embedding_provider(),
            "embedding_model": get_embedding_model(),
            "problem_type": prepared["analysis"]["problem_type"],
            "language_point": prepared["analysis"]["language_point"],
        },
    )
    event_payload["reply_chars"] = len(reply)
    save_agent_event(str(prepared["conversation_id"]), "rag_chat_completed", event_payload)
    trace_record = {
        "type": "rag_chat_trace",
        "conversation_id": prepared["conversation_id"],
        "created_at": prepared.get("created_at") or utc_now(),
        "user_message": prepared.get("user_message"),
        "history": prepared.get("history"),
        "analysis": prepared.get("analysis"),
        "retrieval": prepared.get("retrieval"),
        "prompt_bundle": prepared.get("prompt_bundle"),
        "final_prompt": prepared.get("prompt"),
        "event_payload": event_payload,
        "assistant_reply": reply,
    }
    trace_path = write_trace(trace_record, str(trace_record["created_at"]))
    save_agent_event(
        str(prepared["conversation_id"]),
        "rag_trace_written",
        {"path": str(trace_path)},
    )
