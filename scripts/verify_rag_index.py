from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import DB_PATH, get_connection
from app.model_client import get_embedding_model, get_embedding_provider
from app.rag_index import build_indexes, search_index, visible_proxy_env_names


DEFAULT_QUERIES = [
    "我昨天去图书馆学习中文早上。",
    "“方便”是什么意思？",
    "我可以说“我方便明天去”吗？",
]


def summarize_chunks() -> dict[str, Any]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT index_name, source, chunk_type, metadata_json
            FROM knowledge_chunks
            ORDER BY index_name, source, chunk_type
            """
        ).fetchall()
        embedding_rows = connection.execute(
            """
            SELECT embedding_provider, embedding_model, embedding_json
            FROM knowledge_chunks
            WHERE embedding_provider = ? AND embedding_model = ?
            LIMIT 5
            """,
            (get_embedding_provider(), get_embedding_model()),
        ).fetchall()
    by_index = Counter(row["index_name"] for row in rows)
    by_type = Counter((row["index_name"], row["chunk_type"]) for row in rows)
    samples = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        if row["chunk_type"] in {"question_block", "answer", "paragraph", "note"}:
            samples.append(
                {
                    "index_name": row["index_name"],
                    "source": row["source"],
                    "chunk_type": row["chunk_type"],
                    "article_id": metadata.get("article_id"),
                    "section_id": metadata.get("section_id"),
                    "question_no": metadata.get("question_no"),
                    "option_labels": metadata.get("option_labels"),
                    "answer": metadata.get("answer"),
                }
            )
        if len(samples) >= 8:
            break
    return {
        "db_path": str(DB_PATH),
        "active_embedding_provider": get_embedding_provider(),
        "active_embedding_model": get_embedding_model(),
        "embedding_dimensions_sample": [
            len(json.loads(row["embedding_json"])) for row in embedding_rows
        ],
        "total_chunks": len(rows),
        "by_index": dict(by_index),
        "by_type": {f"{index}:{chunk_type}": count for (index, chunk_type), count in by_type.items()},
        "metadata_samples": samples,
    }


def print_search_results(query: str, top_k: int) -> None:
    print(f"\nQUERY: {query}")
    for index_name in ("teaching_index", "level_index"):
        print(f"\n[{index_name} top {top_k}]")
        results = search_index(query, index_name, top_k)
        if results and all(result["score"] == 0 for result in results):
            print("WARNING: all scores are 0. Check whether stored embeddings and query embeddings use the same provider/model/dimension.")
        for rank, result in enumerate(results, start=1):
            metadata = result["metadata"]
            snippet = result["content"].replace("\n", " ")[:180]
            print(
                f"{rank}. score={result['score']:.4f} "
                f"id={result['id']} type={result['chunk_type']} "
                f"article={metadata.get('article_id')} section={metadata.get('section_id')} "
                f"q={metadata.get('question_no')} source={result['source_file']}"
            )
            print(f"   {snippet}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and verify the dual RAG embedding indexes.")
    parser.add_argument("--force", action="store_true", help="Rebuild embeddings even when source hashes are unchanged.")
    parser.add_argument("--quiet", action="store_true", help="Only print summaries and final search results.")
    parser.add_argument("--top-k", type=int, default=3, help="Top-k results per index.")
    parser.add_argument("--query", action="append", help="Query to verify. Can be passed multiple times.")
    args = parser.parse_args()

    print(
        "VERIFY RAG INDEX\n"
        f"provider={get_embedding_provider()} model={get_embedding_model()} "
        f"force={args.force}",
        flush=True,
    )
    proxy_names = visible_proxy_env_names()
    if proxy_names:
        print(f"Proxy env detected: {', '.join(proxy_names)}", flush=True)

    result = build_indexes(force=args.force, log_progress=not args.quiet)
    print("BUILD SUMMARY")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\nCHUNK SUMMARY")
    print(json.dumps(summarize_chunks(), ensure_ascii=False, indent=2))

    for query in args.query or DEFAULT_QUERIES:
        print_search_results(query, args.top_k)

    print(f"\nSQLite cache: {Path(DB_PATH)}")


if __name__ == "__main__":
    main()
