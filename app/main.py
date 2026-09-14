from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.chat_service import prepare_chat_generation, save_assistant_reply, stream_chat_reply


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Chinese Assistant Chatbot", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(payload: ChatRequest) -> StreamingResponse:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")

    conversation_id = payload.conversation_id or f"conv_{uuid.uuid4().hex}"
    history = [item.model_dump() for item in payload.history]

    async def stream_response() -> AsyncIterator[str]:
        reply_parts: list[str] = []
        prepared: dict | None = None
        try:
            status_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def status_callback(
                stage: str,
                status_message: str,
                extra: dict[str, object] | None = None,
            ) -> None:
                payload: dict[str, object] = {
                    "stage": stage,
                    "message": status_message,
                }
                if extra:
                    payload.update(extra)
                loop.call_soon_threadsafe(status_queue.put_nowait, payload)

            prepare_task = asyncio.create_task(
                asyncio.to_thread(
                    prepare_chat_generation,
                    message,
                    conversation_id=conversation_id,
                    history=history,
                    status_callback=status_callback,
                )
            )
            while not prepare_task.done():
                try:
                    status = await asyncio.wait_for(status_queue.get(), timeout=0.25)
                    yield sse("status", status)
                except TimeoutError:
                    continue
            while not status_queue.empty():
                yield sse("status", status_queue.get_nowait())
            prepared = await prepare_task
            yield sse(
                "metadata",
                {
                    "conversation_id": prepared["conversation_id"],
                    "problem_type": prepared["analysis"]["problem_type"],
                    "language_point": prepared["analysis"]["language_point"],
                },
            )

            yield sse("status", {"stage": "model", "message": "正在调用 GCP 模型流式生成"})
            token_stream = stream_chat_reply(prepared)
            sentinel = object()
            while True:
                chunk = await asyncio.to_thread(next, token_stream, sentinel)
                if chunk is sentinel:
                    break
                reply_parts.append(chunk)
                yield sse("token", chunk)

            reply = "".join(reply_parts)
            await asyncio.to_thread(save_assistant_reply, prepared, reply)
            yield sse("done", {"conversation_id": prepared["conversation_id"]})
        except Exception as exc:
            yield sse("error", {"message": str(exc)})

    return StreamingResponse(stream_response(), media_type="text/event-stream")


def sse(event: str, data: dict[str, object] | str) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
