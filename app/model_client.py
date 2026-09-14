from __future__ import annotations

import os
from pathlib import Path
from typing import Any


EMBEDDING_PROVIDER = "gcp"
LLM_PROVIDER = "gcp"
DEFAULT_LLM_MODEL = "gemini-3.8-flash"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = PROJECT_ROOT / ".env"


def load_dotenv_if_present(path: Path = DOTENV_PATH) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv_if_present()


def get_embedding_model() -> str:
    return os.getenv("GCP_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def get_embedding_provider() -> str:
    return EMBEDDING_PROVIDER


def get_llm_model() -> str:
    return os.getenv("GCP_LLM_MODEL", DEFAULT_LLM_MODEL)


def get_llm_provider() -> str:
    return LLM_PROVIDER


def _extract_embedding_values(embedding: Any) -> list[float]:
    values = getattr(embedding, "values", None)
    if values is not None:
        return [float(value) for value in values]
    if isinstance(embedding, dict):
        raw_values = embedding.get("values") or embedding.get("embedding")
        if raw_values is not None:
            return [float(value) for value in raw_values]
    if isinstance(embedding, list):
        return [float(value) for value in embedding]
    raise RuntimeError(f"Unsupported embedding response item: {type(embedding)!r}")


def _extract_result_embeddings(result: Any) -> list[list[float]]:
    embeddings = getattr(result, "embeddings", None)
    if embeddings is None and isinstance(result, dict):
        embeddings = result.get("embeddings")
    if embeddings is not None:
        return [_extract_embedding_values(embedding) for embedding in embeddings]

    embedding = getattr(result, "embedding", None)
    if embedding is None and isinstance(result, dict):
        embedding = result.get("embedding")
    if embedding is not None:
        return [_extract_embedding_values(embedding)]

    raise RuntimeError("GCP GenAI embedding response did not include embeddings.")


def _extract_event_text(event: Any) -> str:
    delta = getattr(event, "delta", None)
    if delta is not None:
        delta_text = getattr(delta, "text", None)
        if isinstance(delta_text, str) and delta_text:
            return delta_text
        if isinstance(delta, dict):
            delta_text = delta.get("text")
            if isinstance(delta_text, str) and delta_text:
                return delta_text
    for attribute in ("text", "output_text", "delta", "content"):
        value = getattr(event, attribute, None)
        if isinstance(value, str) and value:
            return value
    if isinstance(event, dict):
        for key in ("text", "output_text", "delta", "content"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is required for real embeddings. "
            "Install dependencies and configure GEMINI_API_KEY."
        ) from exc

    client = genai.Client()
    model = get_embedding_model()

    result = client.models.embed_content(model=model, contents=texts)
    embeddings = _extract_result_embeddings(result)
    if len(embeddings) == len(texts):
        return embeddings

    if len(texts) == 1:
        raise RuntimeError(
            f"GCP GenAI returned {len(embeddings)} embeddings for 1 text."
        )

    per_text_embeddings: list[list[float]] = []
    for text in texts:
        single_result = client.models.embed_content(model=model, contents=text)
        single_embeddings = _extract_result_embeddings(single_result)
        if len(single_embeddings) != 1:
            raise RuntimeError(
                f"GCP GenAI returned {len(single_embeddings)} embeddings for a single text."
            )
        per_text_embeddings.append(single_embeddings[0])
    return per_text_embeddings


def stream_reply(input_text: str) -> Any:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is required for real LLM calls. "
            "Install dependencies and configure GEMINI_API_KEY."
        ) from exc

    client = genai.Client()
    stream = client.interactions.create(
        model=get_llm_model(),
        input=input_text,
        stream=True,
    )
    for event in stream:
        text = _extract_event_text(event)
        if text:
            yield text
