"""HTTP API for TODOs and the agent chat."""

import json
import os
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import trace
from pydantic import BaseModel, Field

# WORKSHOP 1: initialize the todo-backend provider here.
# isort: split

# Initialize instrumentation before constructing the shared agent and database engine.
from app import todos  # noqa: E402
from app.agent import ask, stream_reply  # noqa: E402
from app.conversations import ChatMessage, get_messages  # noqa: E402
from app.db import Todo, TodoStatus, init_db  # noqa: E402
from app.todos import TodoCreate, TodoNotFound, TodoUpdate  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="TODO app", lifespan=lifespan)
# WORKSHOP 2: instrument the FastAPI app here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=[
        "content-type",
        "baggage",
        "traceparent",
        "tracestate",
    ],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID = Field(default_factory=uuid4)


class ChatResponse(BaseModel):
    reply: str
    conversation_id: UUID
    otel_trace_id: str
    mode: str


@app.get("/health")
def health():
    return {"status": "ok", "mode": os.getenv("AI_MODEL", "demo")}


@app.exception_handler(TodoNotFound)
async def todo_not_found(request: Request, exc: TodoNotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.post("/todos", response_model=Todo, status_code=201)
def create_todo(body: TodoCreate):
    return todos.create_todo(body)


@app.get("/todos", response_model=list[Todo])
def list_todos(
    status: TodoStatus | None = None, tags: Annotated[list[str] | None, Query()] = None
):
    return todos.list_todos(status, tags)


@app.get("/todos/{todo_id}", response_model=Todo)
def get_todo(todo_id: int):
    return todos.get_todo(todo_id)


@app.patch("/todos/{todo_id}", response_model=Todo)
def update_todo(todo_id: int, body: TodoUpdate):
    return todos.update_todo(todo_id, body)


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    todos.delete_todo(todo_id)
    return Response(status_code=204)


@app.get("/messages", response_model=list[ChatMessage])
def messages(conversation_id: UUID):
    return get_messages(str(conversation_id))


def chat_metadata(conversation_id: UUID) -> dict:
    return {
        "conversation_id": str(conversation_id),
        "otel_trace_id": format(
            trace.get_current_span().get_span_context().trace_id, "032x"
        ),
        "mode": os.getenv("AI_MODEL", "demo"),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest):
    reply = await ask(body.message, str(body.conversation_id))
    return ChatResponse(reply=reply, **chat_metadata(body.conversation_id))


@app.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    metadata = chat_metadata(body.conversation_id)

    async def events():
        yield json.dumps({"type": "start", **metadata}) + "\n"
        try:
            async for text in stream_reply(body.message, str(body.conversation_id)):
                yield json.dumps({"type": "text", "text": text}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
        except Exception:
            # WORKSHOP 3: optionally record this failure on a manual span.
            yield (
                json.dumps(
                    {
                        "type": "error",
                        "message": "The reply could not be completed. Please try again.",
                    }
                )
                + "\n"
            )

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
