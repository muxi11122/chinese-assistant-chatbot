from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from app.db import BASE_DIR, get_connection, init_db, utc_now
from app.model_client import embed_texts, get_embedding_model, get_embedding_provider


KB_DIR = BASE_DIR / "知识库min"
TEACHING_FILES = [
    KB_DIR / "教材" / "发展汉语高级阅读1.md",
    KB_DIR / "教材" / "发展汉语高级阅读1参考答案.md",
]
LEVEL_FILES = [
    KB_DIR / "水平表" / "HSK4词汇.md",
    KB_DIR / "水平表" / "HSK5词汇.md",
    KB_DIR / "水平表" / "HSK语法1-5.md",
]

ARTICLE_RE = re.compile(r"^#\s*(文章[一二三四五六七八九十]+)\s*(.+)?\s*$")
PRACTICAL_ARTICLE_RE = re.compile(r"^##\s*(实用阅读)\s+(.+?)\s*$")
PARAGRAPH_RE = re.compile(r"^\[(\d+)\]\s*(.+)")
NOTE_RE = re.compile(r"^([①②③④⑤⑥⑦⑧⑨⑩])\s*([^：:（(]+)")
SECTION_RE = re.compile(r"^(?:##\s*)?([一二三四五六七八九十]+)、\s*(.+)")
QUESTION_RE = re.compile(r"^(\d+)[.．、]\s*(.*)")
OPTION_RE = re.compile(r"^([A-H])[.．、]\s*(.*)")
INLINE_OPTION_RE = re.compile(r"([A-H])[.．、]?([^A-H]+?)(?=(?:[A-H][.．、]?)|$)")
ANSWER_RE = re.compile(r"(\d+)[.．、]\s*([^0-9]+?)(?=\s+\d+[.．、]|$)")
ARTICLE_NUMERALS = "一二三四五六七八九十"
DEFAULT_EMBED_BATCH_SIZE = 16
MAX_EMBED_RETRIES = 3


@dataclass(frozen=True)
class Chunk:
    id: str
    index_name: str
    source: str
    source_file: str
    chunk_type: str
    heading: str | None
    content: str
    metadata: dict[str, Any]

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            {
                "id": self.id,
                "index_name": self.index_name,
                "source": self.source,
                "chunk_type": self.chunk_type,
                "content": self.content,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VocabularyItem:
    item_no: int
    term: str
    content: str


def relative_source(path: Path) -> str:
    return path.relative_to(BASE_DIR).as_posix()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require_knowledge_files() -> None:
    missing = [path for path in [*TEACHING_FILES, *LEVEL_FILES] if not path.exists()]
    if missing:
        formatted = "\n".join(str(path.relative_to(BASE_DIR)) for path in missing)
        raise FileNotFoundError(f"Missing knowledge base files:\n{formatted}")


def article_id_from_count(count: int) -> str:
    return f"article_{count:03d}"


def section_id_from_count(count: int) -> str:
    return f"section_{count:03d}"


def base_metadata(
    chunk_id: str,
    source: str,
    source_file: str,
    chunk_type: str,
    article_id: str | None,
    article_title: str | None,
    section_id: str | None = None,
    section_title: str | None = None,
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "index_name": "teaching_index",
        "source": source,
        "source_file": source_file,
        "chunk_type": chunk_type,
        "article_id": article_id,
        "article_title": article_title,
        "section_id": section_id,
        "section_title": section_title,
        "paragraph_no": None,
        "question_no": None,
    }


def split_long_paragraph(text: str, limit: int = 1000) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[。！？；;])", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > limit and current:
            parts.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        parts.append(current.strip())
    return parts or [text]


def parse_option_labels(lines: list[str]) -> list[str]:
    labels: list[str] = []
    for line in lines:
        match = OPTION_RE.match(line.strip())
        if match and match.group(1) not in labels:
            labels.append(match.group(1))
    return labels


def flush_question(
    chunks: list[Chunk],
    source: str,
    source_file: str,
    article_id: str | None,
    article_title: str | None,
    section_id: str | None,
    section_title: str | None,
    question_no: str | None,
    lines: list[str],
) -> None:
    if not question_no or not lines:
        return
    safe_section_id = section_id or "section_000"
    question_id = f"{int(question_no):03d}" if question_no.isdigit() else question_no
    chunk_id = f"{article_id or 'article_000'}_{safe_section_id}_q_{question_id}"
    content = "\n".join(line.strip() for line in lines if line.strip())
    option_labels = parse_option_labels(lines)
    metadata = base_metadata(
        chunk_id,
        source,
        source_file,
        "question_block",
        article_id,
        article_title,
        section_id,
        section_title,
    )
    metadata.update(
        {
            "question_no": question_no,
            "has_options": bool(option_labels),
            "option_labels": option_labels,
        }
    )
    chunks.append(
        Chunk(
            id=chunk_id,
            index_name="teaching_index",
            source=source,
            source_file=source_file,
            chunk_type="question_block",
            heading=section_title or article_title,
            content=content,
            metadata=metadata,
        )
    )


def parse_teaching_markdown(path: Path) -> list[Chunk]:
    source = relative_source(path)
    source_file = path.name
    lines = path.read_text(encoding="utf-8").splitlines()
    chunks: list[Chunk] = []
    article_count = 0
    section_count = 0
    article_id: str | None = None
    article_title: str | None = None
    section_id: str | None = None
    section_title: str | None = None
    question_no: str | None = None
    question_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("![](") or line.startswith("(选自"):
            continue

        article_match = ARTICLE_RE.match(line)
        practical_match = PRACTICAL_ARTICLE_RE.match(line)
        if article_match or practical_match:
            if question_no != "section" or len(question_lines) > 1:
                flush_question(
                    chunks,
                    source,
                    source_file,
                    article_id,
                    article_title,
                    section_id,
                    section_title,
                    question_no,
                    question_lines,
                )
            question_no = None
            question_lines = []
            article_count += 1
            section_count = 0
            article_id = article_id_from_count(article_count)
            if article_match:
                article_title = f"{article_match.group(1)} {article_match.group(2) or ''}".strip()
            else:
                article_title = f"{practical_match.group(1)} {practical_match.group(2)}"
            section_id = None
            section_title = None
            chunk_id = f"{article_id}_title"
            metadata = base_metadata(chunk_id, source, source_file, "article_title", article_id, article_title)
            chunks.append(
                Chunk(chunk_id, "teaching_index", source, source_file, "article_title", article_title, article_title, metadata)
            )
            continue

        section_match = SECTION_RE.match(line)
        if section_match and article_id:
            if question_no != "section" or len(question_lines) > 1:
                flush_question(
                    chunks,
                    source,
                    source_file,
                    article_id,
                    article_title,
                    section_id,
                    section_title,
                    question_no,
                    question_lines,
                )
            question_no = None
            question_lines = []
            section_count += 1
            section_id = section_id_from_count(section_count)
            section_title = f"{section_match.group(1)}、{section_match.group(2).strip()}"
            question_no = "section"
            question_lines = [section_title]
            chunk_id = f"{article_id}_{section_id}"
            metadata = base_metadata(
                chunk_id,
                source,
                source_file,
                "exercise_section",
                article_id,
                article_title,
                section_id,
                section_title,
            )
            chunks.append(
                Chunk(chunk_id, "teaching_index", source, source_file, "exercise_section", article_title, section_title, metadata)
            )
            continue

        paragraph_match = PARAGRAPH_RE.match(line)
        if paragraph_match and article_id and not section_id:
            paragraph_no = int(paragraph_match.group(1))
            paragraph_text = line
            for part_no, paragraph_part in enumerate(split_long_paragraph(paragraph_text), start=1):
                suffix = f"_part_{part_no:02d}" if part_no > 1 else ""
                chunk_id = f"{article_id}_p_{paragraph_no:03d}{suffix}"
                metadata = base_metadata(chunk_id, source, source_file, "paragraph", article_id, article_title)
                metadata["paragraph_no"] = paragraph_no
                if part_no > 1:
                    metadata["part_no"] = part_no
                chunks.append(
                    Chunk(chunk_id, "teaching_index", source, source_file, "paragraph", article_title, paragraph_part, metadata)
                )
            continue

        note_match = NOTE_RE.match(line)
        if note_match and article_id:
            note_no = note_match.group(1)
            term = note_match.group(2).strip()
            chunk_id = f"{article_id}_note_{note_no}"
            metadata = base_metadata(chunk_id, source, source_file, "note", article_id, article_title, section_id, section_title)
            metadata.update({"note_no": note_no, "term": term})
            chunks.append(Chunk(chunk_id, "teaching_index", source, source_file, "note", article_title, line, metadata))
            continue

        question_match = QUESTION_RE.match(line)
        if question_match and article_id and (section_id or line.endswith("（）") or "（" in line):
            if question_no != "section" or len(question_lines) > 1:
                flush_question(chunks, source, source_file, article_id, article_title, section_id, section_title, question_no, question_lines)
            question_no = question_match.group(1)
            question_lines = [line]
            inline_options = INLINE_OPTION_RE.findall(question_match.group(2))
            if inline_options:
                question_lines = [re.sub(r"\s*[A-D][.．、]?.*$", "", line).strip()]
                question_lines.extend(f"{label}. {text.strip()}" for label, text in inline_options if text.strip())
            continue

        if question_no:
            if OPTION_RE.match(line):
                question_lines.append(line)
                continue
            if question_no == "section" and not re.match(r"^(#|##|\[|[①②③④⑤⑥⑦⑧⑨⑩])", line):
                question_lines.append(line)
                continue
            if question_no != "section" and not re.match(r"^(#|##|\[|[①②③④⑤⑥⑦⑧⑨⑩])", line):
                question_lines.append(line)
                continue

        if article_id and not section_id and not line.startswith("##") and not line.startswith("【"):
            next_no = sum(
                1
                for chunk in chunks
                if chunk.metadata.get("article_id") == article_id and chunk.chunk_type == "paragraph"
            ) + 1
            chunk_id = f"{article_id}_p_{next_no:03d}"
            metadata = base_metadata(chunk_id, source, source_file, "paragraph", article_id, article_title)
            metadata["paragraph_no"] = next_no
            chunks.append(Chunk(chunk_id, "teaching_index", source, source_file, "paragraph", article_title, line, metadata))

    if question_no != "section" or len(question_lines) > 1:
        flush_question(chunks, source, source_file, article_id, article_title, section_id, section_title, question_no, question_lines)
    return chunks


def parse_answer_markdown(path: Path) -> list[Chunk]:
    source = relative_source(path)
    source_file = path.name
    chunks: list[Chunk] = []
    article_count = 0
    section_count = 0
    article_id: str | None = None
    article_title: str | None = None
    section_id: str | None = None
    section_title: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("# 发展汉语") or line.startswith("## 第"):
            continue
        article_match = re.match(r"^##\s*(文章[一二三四五六七八九十]+)\s*(.+)$", line)
        practical_match = re.match(r"^实用阅读\s+(.+?)\s+(.+)$", line)
        if article_match:
            article_count += 1
            section_count = 0
            article_id = article_id_from_count(article_count)
            article_title = f"{article_match.group(1)} {article_match.group(2)}"
            section_id = None
            section_title = None
            continue
        if practical_match:
            article_count += 1
            section_count = 1
            article_id = article_id_from_count(article_count)
            article_title = f"实用阅读 {practical_match.group(1)}"
            section_id = section_id_from_count(section_count)
            section_title = "根据选课通知选择正确答案"
            line = practical_match.group(2)

        section_match = re.match(r"^(?:##\s*)?([一二三四五六七八九十]+)、\s*(.+)$", line)
        if section_match and article_id:
            section_count += 1
            section_id = section_id_from_count(section_count)
            section_title = f"{section_match.group(1)}、{section_match.group(2).strip()}"
            answer_tail = section_match.group(2).strip()
        else:
            answer_tail = line

        if not article_id:
            continue
        matches = ANSWER_RE.findall(answer_tail)
        if matches:
            for question_no, answer in matches:
                answer_text = answer.strip()
                chunk_id = f"{article_id}_{section_id or 'section_000'}_a_{int(question_no):03d}"
                metadata = base_metadata(
                    chunk_id,
                    source,
                    source_file,
                    "answer",
                    article_id,
                    article_title,
                    section_id,
                    section_title,
                )
                metadata.update({"question_no": question_no, "answer": answer_text})
                chunks.append(
                    Chunk(chunk_id, "teaching_index", source, source_file, "answer", section_title or article_title, answer_text, metadata)
                )
        elif section_id and answer_tail and not answer_tail.startswith("##"):
            chunk_id = f"{article_id}_{section_id}_a_section"
            metadata = base_metadata(
                chunk_id,
                source,
                source_file,
                "answer",
                article_id,
                article_title,
                section_id,
                section_title,
            )
            metadata.update({"question_no": "section", "answer": answer_tail})
            chunks.append(Chunk(chunk_id, "teaching_index", source, source_file, "answer", section_title, answer_tail, metadata))
    return chunks


def parse_level_markdown(path: Path) -> list[Chunk]:
    source = relative_source(path)
    source_file = path.name
    text = path.read_text(encoding="utf-8")
    chunks: list[Chunk] = []
    if "词汇" in source_file:
        vocabulary_items = parse_vocabulary_items(text)
        group_size = 15 if "HSK4" in source_file else 8
        level = "HSK4" if "HSK4" in source_file else "HSK5"
        for group_no, start in enumerate(range(0, len(vocabulary_items), group_size), start=1):
            group = vocabulary_items[start : start + group_size]
            if not group:
                continue
            first_no = group[0].item_no
            last_no = group[-1].item_no
            terms = [item.term for item in group]
            chunk_id = f"{source_file.removesuffix('.md')}_vocab_group_{group_no:03d}"
            heading = f"{level}词汇 {first_no}-{last_no}"
            content = "\n\n".join(item.content for item in group)
            metadata = {
                "id": chunk_id,
                "index_name": "level_index",
                "source": source,
                "source_file": source_file,
                "chunk_type": "vocabulary_group",
                "item_no_start": first_no,
                "item_no_end": last_no,
                "group_no": group_no,
                "group_size": len(group),
                "terms": terms,
                "level": level,
            }
            chunks.append(Chunk(chunk_id, "level_index", source, source_file, "vocabulary_group", heading, content, metadata))
        return chunks

    tables = re.findall(r"<table>.*?</table>", text, flags=re.S)
    grammar_item_no = 0
    for table_no, table in enumerate(tables, start=1):
        rows = extract_html_table_rows(table)
        table_text = " ".join(" | ".join(row) for row in rows)
        level_match = re.search(r"([一二三四五六]级)语法项目表", table_text)
        level = level_match.group(1) if level_match else f"table_{table_no:02d}"
        for row_no, cells in enumerate(rows, start=1):
            grammar_item = grammar_item_from_cells(cells)
            if not grammar_item:
                continue
            grammar_item_no += 1
            chunk_id = f"{source_file.removesuffix('.md')}_grammar_{grammar_item_no:03d}"
            title = grammar_item["title"]
            content = (
                f"等级：{level}\n"
                f"语法点：{title}\n"
                f"结构：{grammar_item['structure']}\n"
                f"例句：{grammar_item['example']}"
            )
            metadata = {
                "id": chunk_id,
                "index_name": "level_index",
                "source": source,
                "source_file": source_file,
                "chunk_type": "grammar",
                "item_no": grammar_item_no,
                "table_no": table_no,
                "row_no": row_no,
                "level": level,
                "grammar_point": title,
            }
            chunks.append(Chunk(chunk_id, "level_index", source, source_file, "grammar", title, content, metadata))
    return chunks


def split_vocabulary_blocks(text: str) -> list[str]:
    normalized = re.sub(r"(?<!\d)(\d+)(?=【)", r"\n\1 ", text)
    normalized = re.sub(r"(?m)^(#+\s*)?(\d+)(?=【)", r"\1\2 ", normalized)
    pattern = re.compile(r"(?m)^(?:#+\s*)?\d+\s*(?:【[^\n】]+】|[^\s【】]+\s+)")
    matches = list(pattern.finditer(normalized))
    blocks: list[str] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        block = normalized[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def parse_vocabulary_items(text: str) -> list[VocabularyItem]:
    items: list[VocabularyItem] = []
    seen_item_numbers: Counter[int] = Counter()
    for block in split_vocabulary_blocks(text):
        block = block.strip()
        bracket_match = re.match(r"^(?:#+\s*)?(\d+)\s*【([^】]+)】", block)
        plain_match = re.match(r"^(?:#+\s*)?(\d+)\s+([^\s【】]+)\s+", block)
        match = bracket_match or plain_match
        if not match:
            continue
        item_no = int(match.group(1))
        seen_item_numbers[item_no] += 1
        term = match.group(2).strip()
        items.append(VocabularyItem(item_no=item_no, term=term, content=block))
    return items


def extract_html_table_rows(table: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.S):
        cells = []
        for cell_html in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.S):
            plain = re.sub(r"<[^>]+>", "", cell_html)
            plain = html.unescape(plain)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain:
                cells.append(plain)
        if cells:
            rows.append(cells)
    return rows


def grammar_item_from_cells(cells: list[str]) -> dict[str, str] | None:
    joined = " | ".join(cells)
    skip_tokens = ("目标描述", "语法项目", "结构形式", "举例", "实词", "虚词", "句子成分", "复句")
    if any(token == joined or joined.startswith(f"{token} |") for token in skip_tokens):
        return None
    if "语法项目表" in joined or not joined.strip(" |"):
        return None

    useful = [cell for cell in cells if cell and cell not in {"实词", "虚词", "句子成分、句型和句类", "复句"}]
    if len(useful) < 2:
        return None
    if len(useful) == 2:
        title, example = useful
        structure = ""
    else:
        title = useful[0]
        structure = useful[1]
        example = "；".join(useful[2:])
    title = re.sub(r"\s+", " ", title).strip(" ：:")
    structure = re.sub(r"\s+", " ", structure).strip(" ：:")
    example = re.sub(r"\s+", " ", example).strip(" ：:")
    if not title or len(title) > 180:
        return None
    return {"title": title, "structure": structure, "example": example}


def parse_source(path: Path, index_name: str) -> list[Chunk]:
    if index_name == "level_index":
        return parse_level_markdown(path)
    if path.name.endswith("参考答案.md"):
        return parse_answer_markdown(path)
    return parse_teaching_markdown(path)


def fetch_embedding_cache(connection: sqlite3.Connection, hashes: list[str], model: str) -> dict[str, list[float]]:
    if not hashes:
        return {}
    placeholders = ",".join("?" for _ in hashes)
    embedding_provider = get_embedding_provider()
    rows = connection.execute(
        f"""
        SELECT content_hash, embedding_json
        FROM knowledge_chunks
        WHERE embedding_provider = ?
          AND embedding_model = ?
          AND content_hash IN ({placeholders})
        """,
        [embedding_provider, model, *hashes],
    ).fetchall()
    return {row["content_hash"]: json.loads(row["embedding_json"]) for row in rows}


def progress_log(enabled: bool, message: str) -> None:
    if enabled:
        print(message, flush=True)


def visible_proxy_env_names() -> list[str]:
    return [
        name
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
        if os.getenv(name)
    ]


def embed_chunk_batch(
    batch: list[Chunk],
    *,
    log_progress: bool = False,
    depth: int = 0,
) -> list[list[float]]:
    texts = [chunk.content for chunk in batch]
    for attempt in range(1, MAX_EMBED_RETRIES + 1):
        try:
            return embed_texts(texts)
        except Exception as exc:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                progress_log(
                    log_progress,
                    f"      batch failed on attempt {attempt}; splitting "
                    f"{len(batch)} chunks into {midpoint}+{len(batch) - midpoint}: {exc}",
                )
                return [
                    *embed_chunk_batch(batch[:midpoint], log_progress=log_progress, depth=depth + 1),
                    *embed_chunk_batch(batch[midpoint:], log_progress=log_progress, depth=depth + 1),
                ]
            if attempt < MAX_EMBED_RETRIES:
                sleep_seconds = attempt * 2
                progress_log(
                    log_progress,
                    f"      single chunk embedding failed on attempt {attempt}; "
                    f"retrying in {sleep_seconds}s: {exc}",
                )
                time.sleep(sleep_seconds)
                continue

            proxy_names = visible_proxy_env_names()
            proxy_hint = (
                f" Detected proxy env vars: {', '.join(proxy_names)}."
                if proxy_names
                else " No proxy env vars detected."
            )
            raise RuntimeError(
                "GCP embedding request failed even for a single chunk. "
                "This is usually a network/proxy/TLS issue rather than an indexing logic issue."
                f"{proxy_hint} Original error: {exc}"
            ) from exc

    raise RuntimeError("Unexpected embedding retry state.")


def embed_missing_chunks(
    chunks: list[Chunk],
    *,
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
    log_progress: bool = False,
) -> dict[str, list[float]]:
    embeddings_by_hash: dict[str, list[float]] = {}
    total = len(chunks)
    for start in range(0, total, batch_size):
        batch = chunks[start : start + batch_size]
        progress_log(
            log_progress,
            f"    embedding batch {start + 1}-{start + len(batch)} / {total}",
        )
        embeddings = embed_chunk_batch(batch, log_progress=log_progress)
        for chunk, embedding in zip(batch, embeddings, strict=True):
            embeddings_by_hash[chunk.content_hash] = embedding
    return embeddings_by_hash


def upsert_chunk_embeddings(
    connection: sqlite3.Connection,
    chunks: list[Chunk],
    embeddings_by_hash: dict[str, list[float]],
    *,
    embedding_provider: str,
    embedding_model: str,
    created_at: str,
) -> None:
    for chunk in chunks:
        connection.execute(
            """
            INSERT OR REPLACE INTO knowledge_chunks (
                id, index_name, source, source_file, chunk_type, heading, content,
                content_hash, metadata_json, embedding_provider, embedding_model,
                embedding_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.id,
                chunk.index_name,
                chunk.source,
                chunk.source_file,
                chunk.chunk_type,
                chunk.heading,
                chunk.content,
                chunk.content_hash,
                json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
                embedding_provider,
                embedding_model,
                json.dumps(embeddings_by_hash[chunk.content_hash]),
                created_at,
            ),
        )


def store_source_chunks(
    path: Path,
    index_name: str,
    chunks: list[Chunk],
    force: bool = False,
    log_progress: bool = False,
) -> int:
    source = relative_source(path)
    source_hash = file_hash(path)
    embedding_provider = get_embedding_provider()
    embedding_model = get_embedding_model()
    progress_log(
        log_progress,
        f"[index] {source} -> {len(chunks)} chunks "
        f"(provider={embedding_provider}, model={embedding_model}, force={force})",
    )
    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT source_hash, embedding_model, chunk_count
            FROM knowledge_sources
            WHERE source = ? AND embedding_provider = ?
            """,
            (source, embedding_provider),
        ).fetchone()
        existing_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM knowledge_chunks
            WHERE source = ? AND embedding_provider = ? AND embedding_model = ?
            """,
            (source, embedding_provider, embedding_model),
        ).fetchone()["count"]
        if (
            not force
            and existing
            and existing["source_hash"] == source_hash
            and existing["embedding_model"] == embedding_model
            and existing["chunk_count"] == len(chunks)
            and existing_count == len(chunks)
        ):
            progress_log(log_progress, "    up to date; using cached embeddings")
            return 0

        hashes = [chunk.content_hash for chunk in chunks]
        if force:
            connection.execute(
                """
                DELETE FROM knowledge_chunks
                WHERE source = ? AND embedding_provider = ? AND embedding_model = ?
                """,
                (source, embedding_provider, embedding_model),
            )
            connection.execute(
                """
                DELETE FROM knowledge_sources
                WHERE source = ? AND embedding_provider = ? AND embedding_model = ?
                """,
                (source, embedding_provider, embedding_model),
            )
            connection.commit()

        cache = {} if force else fetch_embedding_cache(connection, hashes, embedding_model)
        missing_chunks = [chunk for chunk in chunks if chunk.content_hash not in cache]
        progress_log(
            log_progress,
            f"    cache hits={len(chunks) - len(missing_chunks)} "
            f"missing={len(missing_chunks)}",
        )
        if missing_chunks:
            total_missing = len(missing_chunks)
            for start in range(0, total_missing, DEFAULT_EMBED_BATCH_SIZE):
                batch = missing_chunks[start : start + DEFAULT_EMBED_BATCH_SIZE]
                progress_log(
                    log_progress,
                    f"    embedding batch {start + 1}-{start + len(batch)} / {total_missing}",
                )
                embeddings = embed_chunk_batch(batch, log_progress=log_progress)
                batch_cache = {
                    chunk.content_hash: embedding
                    for chunk, embedding in zip(batch, embeddings, strict=True)
                }
                cache.update(batch_cache)
                upsert_chunk_embeddings(
                    connection,
                    batch,
                    batch_cache,
                    embedding_provider=embedding_provider,
                    embedding_model=embedding_model,
                    created_at=utc_now(),
                )
                connection.commit()
                progress_log(
                    log_progress,
                    f"    persisted embedding batch {start + 1}-{start + len(batch)} / {total_missing}",
                )

        now = utc_now()
        progress_log(log_progress, "    finalizing source metadata in SQLite")
        upsert_chunk_embeddings(
            connection,
            chunks,
            cache,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            created_at=now,
        )
        current_ids = [chunk.id for chunk in chunks]
        if current_ids:
            placeholders = ",".join("?" for _ in current_ids)
            connection.execute(
                f"""
                DELETE FROM knowledge_chunks
                WHERE source = ?
                  AND embedding_provider = ?
                  AND embedding_model = ?
                  AND id NOT IN ({placeholders})
                """,
                (source, embedding_provider, embedding_model, *current_ids),
            )
        connection.execute(
            """
            INSERT INTO knowledge_sources (
                source, index_name, source_hash, embedding_provider, embedding_model,
                chunk_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                index_name = excluded.index_name,
                source_hash = excluded.source_hash,
                embedding_provider = excluded.embedding_provider,
                embedding_model = excluded.embedding_model,
                chunk_count = excluded.chunk_count,
                updated_at = excluded.updated_at
            """,
            (source, index_name, source_hash, embedding_provider, embedding_model, len(chunks), now),
        )
        progress_log(log_progress, f"    done; stored {len(chunks)} chunks")
        return len(chunks)


def build_indexes(force: bool = False, log_progress: bool = False) -> dict[str, Any]:
    require_knowledge_files()
    init_db()
    result: dict[str, Any] = {"rebuilt_sources": [], "sources": [], "total_chunks": 0}
    progress_log(log_progress, "[build] starting RAG index build")
    for index_name, paths in (("teaching_index", TEACHING_FILES), ("level_index", LEVEL_FILES)):
        progress_log(log_progress, f"[build] index={index_name}")
        for path in paths:
            progress_log(log_progress, f"  parsing {relative_source(path)}")
            chunks = parse_source(path, index_name)
            progress_log(log_progress, f"  parsed {len(chunks)} chunks")
            rebuilt = store_source_chunks(
                path,
                index_name,
                chunks,
                force=force,
                log_progress=log_progress,
            )
            result["total_chunks"] += len(chunks)
            source_summary = {
                "index_name": index_name,
                "source": relative_source(path),
                "chunk_count": len(chunks),
                "rebuilt": rebuilt > 0,
            }
            result["sources"].append(source_summary)
            if rebuilt > 0:
                result["rebuilt_sources"].append(source_summary)
    progress_log(log_progress, f"[build] complete; total_chunks={result['total_chunks']}")
    return result


def get_active_index_counts() -> dict[str, int]:
    init_db()
    embedding_provider = get_embedding_provider()
    embedding_model = get_embedding_model()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT index_name, COUNT(*) AS chunk_count
            FROM knowledge_chunks
            WHERE embedding_provider = ? AND embedding_model = ?
            GROUP BY index_name
            """,
            (embedding_provider, embedding_model),
        ).fetchall()
    return {row["index_name"]: int(row["chunk_count"]) for row in rows}


def expected_sources_by_index() -> dict[str, list[str]]:
    return {
        "teaching_index": [relative_source(path) for path in TEACHING_FILES],
        "level_index": [relative_source(path) for path in LEVEL_FILES],
    }


def require_active_indexes(index_names: Sequence[str] = ("teaching_index", "level_index")) -> dict[str, int]:
    require_knowledge_files()
    counts = get_active_index_counts()
    embedding_provider = get_embedding_provider()
    embedding_model = get_embedding_model()
    sources_by_index = expected_sources_by_index()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT source, index_name, chunk_count
            FROM knowledge_sources
            WHERE embedding_provider = ? AND embedding_model = ?
            """,
            (embedding_provider, embedding_model),
        ).fetchall()
    complete_sources = {(row["index_name"], row["source"]) for row in rows if row["chunk_count"] > 0}
    missing = []
    for index_name in index_names:
        expected_sources = sources_by_index[index_name]
        if counts.get(index_name, 0) == 0 or any(
            (index_name, source) not in complete_sources for source in expected_sources
        ):
            missing.append(index_name)
    if missing:
        missing_text = "、".join(missing)
        raise RuntimeError(
            f"当前 {embedding_provider}/{embedding_model} 缺少完整 RAG 索引：{missing_text}。"
            "请先运行 `uv run python scripts/verify_rag_index.py` "
            "完成 teaching_index 和 level_index 的真实 embedding 构建。"
        )
    return counts


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search_index_by_embedding(
    query_embedding: list[float],
    index_name: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    init_db()
    embedding_provider = get_embedding_provider()
    embedding_model = get_embedding_model()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, index_name, source, source_file, chunk_type, heading, content,
                   content_hash, metadata_json, embedding_json
            FROM knowledge_chunks
            WHERE index_name = ? AND embedding_provider = ? AND embedding_model = ?
            """,
            (index_name, embedding_provider, embedding_model),
        ).fetchall()
    scored = []
    for row in rows:
        score = cosine_similarity(query_embedding, json.loads(row["embedding_json"]))
        metadata = json.loads(row["metadata_json"])
        scored.append(
            {
                "score": score,
                "id": row["id"],
                "index_name": row["index_name"],
                "source": row["source"],
                "source_file": row["source_file"],
                "chunk_type": row["chunk_type"],
                "heading": row["heading"],
                "content": row["content"],
                "metadata": metadata,
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]


def search_index(query: str, index_name: str, top_k: int = 3) -> list[dict[str, Any]]:
    query_embedding = embed_texts([query])[0]
    return search_index_by_embedding(query_embedding, index_name, top_k)
