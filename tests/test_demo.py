"""Exercise the API against a real MCP process; collect OTel spans locally."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    directory = tmp_path_factory.mktemp("demo")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = {
        "DATABASE_URL": f"sqlite:///{directory / 'test.db'}",
        "MCP_PORT": str(port),
        "MCP_URL": f"http://127.0.0.1:{port}/mcp",
        "AI_MODEL": "demo",
    }
    previous = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    spans_file = directory / "mcp-spans.jsonl"
    bootstrap = """
import os
import sys

import uvicorn
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor, SpanExportResult
OTLPSpanExporter.export = lambda self, spans: SpanExportResult.SUCCESS
from app.mcp_server import app, otel_provider
with open(sys.argv[1], "a") as output:
    otel_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(
        out=output, formatter=lambda span: span.to_json(indent=None) + "\\n"
    )))
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["MCP_PORT"]))
"""
    with (directory / "mcp.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", bootstrap, str(spans_file)], stdout=log, stderr=log
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((directory / "mcp.log").read_text())
                try:
                    httpx.get(settings["MCP_URL"], timeout=0.2)
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                pytest.fail("MCP did not start")
            with patch(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export",
                return_value=SpanExportResult.SUCCESS,
            ):
                from app.main import app, otel_provider

                exporter = InMemorySpanExporter()
                otel_provider.add_span_processor(SimpleSpanProcessor(exporter))
                try:
                    with TestClient(app, raise_server_exceptions=False) as client:
                        yield client, exporter, spans_file
                finally:
                    otel_provider.shutdown()
        finally:
            process.terminate()
            process.wait(timeout=10)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_todo_crud_and_filters(demo):
    client, _, _ = demo
    first = client.post(
        "/todos",
        json={
            "title": "Write demo",
            "text": "Keep it simple",
            "due_date": "2026-11-15",
            "tags": ["work", "urgent"],
        },
    )
    assert first.status_code == 201, first.text
    todo = first.json()
    assert todo["status"] == "open"
    assert todo["due_date"] == "2026-11-15"
    assert todo["created_at"].endswith("+00:00")
    second = client.post(
        "/todos", json={"title": "Read docs", "status": "doing", "tags": ["workshop"]}
    ).json()
    assert client.get(f"/todos/{todo['id']}").json() == todo
    filtered = client.get(
        "/todos", params=[("status", "open"), ("tags", "work"), ("tags", "urgent")]
    ).json()
    assert [t["id"] for t in filtered] == [todo["id"]]
    assert client.get("/todos", params={"tags": "wor"}).json() == []
    assert (
        client.get("/todos", params=[("tags", "work"), ("tags", "missing")]).json()
        == []
    )
    changed = client.patch(f"/todos/{todo['id']}", json={"status": "closed"}).json()
    assert changed["status"] == "closed"
    assert changed["title"] == todo["title"]
    assert changed["tags"] == todo["tags"]
    assert changed["created_at"] == todo["created_at"]
    assert changed["due_date"] == todo["due_date"]
    cleared = client.patch(
        f"/todos/{todo['id']}",
        json={"title": "Done", "text": "", "tags": [], "due_date": None},
    ).json()
    assert (
        cleared["tags"] == [] and cleared["text"] == "" and cleared["title"] == "Done"
    )
    assert client.post("/todos", json={"title": "   "}).status_code == 422
    assert (
        client.post("/todos", json={"title": "x", "status": "unknown"}).status_code
        == 422
    )
    assert client.get("/todos?status=unknown").status_code == 422
    assert (
        client.post("/todos", json={"title": "x", "due_date": "not-a-date"}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/todos/{todo['id']}", json={"created_at": "2020-01-01"}
        ).status_code
        == 422
    )
    for todo_id in [todo["id"], second["id"]]:
        deleted = client.delete(f"/todos/{todo_id}")
        assert deleted.status_code == 204 and deleted.content == b""
        assert client.get(f"/todos/{todo_id}").status_code == 404
        assert (
            client.patch(f"/todos/{todo_id}", json={"text": "missing"}).status_code
            == 404
        )
        assert client.delete(f"/todos/{todo_id}").status_code == 404


def test_agent_crud_uses_same_todos(demo):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from app.agent import agent

    client, _, _ = demo

    async def tool(name, arguments):
        def model(messages, info):
            if len(messages) == 1:
                return ModelResponse([ToolCallPart(name, arguments)])
            return ModelResponse([TextPart(json.dumps(messages[-1].parts[0].content))])

        with agent.override(model=FunctionModel(model)):
            async with agent:
                result = await agent.run(f"Use {name}")
        return json.loads(result.output)

    async def scenario():
        created = await tool(
            "create_todo", {"data": {"title": "From agent", "tags": ["agent"]}}
        )
        todo_id = created["id"]
        assert client.get(f"/todos/{todo_id}").json() == created
        assert await tool("get_todo", {"todo_id": todo_id}) == created
        changed = await tool(
            "update_todo",
            {
                "todo_id": todo_id,
                "data": {"status": "doing", "text": "Updated via MCP"},
            },
        )
        assert changed["title"] == "From agent" and changed["status"] == "doing"
        assert client.get(f"/todos/{todo_id}").json() == changed
        matches = await tool("list_todos", {"status": "doing", "tags": ["agent"]})
        assert matches == [changed]
        assert await tool("list_todos", {"status": "closed", "tags": ["agent"]}) == []
        assert await tool("delete_todo", {"todo_id": todo_id}) == {"deleted": todo_id}
        assert client.get(f"/todos/{todo_id}").status_code == 404

    asyncio.run(scenario())


def test_chat_persistence(demo):
    client, _, _ = demo
    assert client.get("/health").json() == {"status": "ok", "mode": "demo"}
    response = client.post("/chat", json={"message": "List TODOs"})
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "demo"
    messages = client.get(
        "/messages", params={"conversation_id": response.json()["conversation_id"]}
    ).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "List TODOs"
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "x" * 2001}).status_code == 422


def test_browser_context_reaches_mcp_and_errors(demo):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from app.agent import agent

    client, exporter, spans_file = demo
    trace_id = "a" * 32
    parent_id = "b" * 16
    headers = {"traceparent": f"00-{trace_id}-{parent_id}-01"}
    response = client.post("/chat", json={"message": "List TODOs"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["otel_trace_id"] == trace_id

    # Ask the real agent to call a tool for a missing TODO; no artificial error tool.
    model = FunctionModel(
        lambda messages, info: ModelResponse(
            [ToolCallPart("get_todo", {"todo_id": 999999})]
        )
    )
    with agent.override(model=model):
        result = client.post(
            "/chat", json={"message": "Get TODO 999999"}, headers=headers
        )
    assert result.status_code == 500
    remote = [json.loads(line) for line in spans_file.read_text().splitlines()]
    remote_trace = [
        span for span in remote if span["context"]["trace_id"] == f"0x{trace_id}"
    ]
    assert any(span["name"] == "list_todos" for span in remote_trace)
    assert any(
        span["status"]["status_code"] == "ERROR"
        and any(event["name"] == "exception" for event in span["events"])
        for span in remote_trace
    ), "MCP tool errors must be recorded in OTel"
    spans = [
        span
        for span in exporter.get_finished_spans()
        if span.context.trace_id == int(trace_id, 16)
    ]
    assert any(span.status.is_ok is False for span in spans)
    assert any(event.name == "exception" for span in spans for event in span.events)
    assert any(
        span.attributes.get("gen_ai.operation.name") == "invoke_agent" for span in spans
    )
    assert any("get_todo" in span.name for span in spans)
    outgoing_ids = {
        f"0x{span.context.span_id:016x}" for span in spans if span.kind.name == "CLIENT"
    }
    assert any(
        span["parent_id"] in outgoing_ids
        for span in remote_trace
        if span["kind"] == "SpanKind.SERVER"
    )
    assert any("db.statement" in span.attributes for span in spans)
    for method in ["POST", "PATCH", "DELETE"]:
        preflight = client.options(
            "/todos",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type,baggage,traceparent,tracestate",
            },
        )
        assert preflight.status_code == 200


def test_conversation_id_groups_turns_without_leaking(demo):
    from uuid import uuid4

    client, exporter, _ = demo
    first_id, second_id = str(uuid4()), str(uuid4())
    traces = []
    for conversation_id in [first_id, first_id, second_id]:
        response = client.post(
            "/chat", json={"message": "List TODOs", "conversation_id": conversation_id}
        )
        assert response.status_code == 200, response.text
        assert response.json()["conversation_id"] == conversation_id
        traces.append((response.json()["otel_trace_id"], conversation_id))
    assert len({trace_id for trace_id, _ in traces}) == 3
    for trace_id, conversation_id in traces:
        spans = [
            span
            for span in exporter.get_finished_spans()
            if span.context.trace_id == int(trace_id, 16)
            and span.attributes.get("gen_ai.operation.name") == "invoke_agent"
        ]
        assert spans
        assert all(
            span.attributes["gen_ai.conversation.id"] == conversation_id
            for span in spans
        )
    assert (
        client.post(
            "/chat", json={"message": "List TODOs", "conversation_id": "invalid"}
        ).status_code
        == 422
    )


def test_agent_remembers_tools_and_messages_per_conversation(demo):
    from uuid import uuid4

    from pydantic_ai.messages import (
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.models.function import FunctionModel

    from app.agent import agent
    from app.conversations import load_history

    client, _, _ = demo
    conversation_id, other_id = str(uuid4()), str(uuid4())

    def model(messages, info):
        last = messages[-1]
        if any(isinstance(part, ToolReturnPart) for part in last.parts):
            todo = last.parts[0].content
            return ModelResponse(
                [TextPart(f"**{todo['title']}** is {todo['status']}.")]
            )
        prompt = next(
            part.content for part in last.parts if isinstance(part, UserPromptPart)
        )
        if prompt == "Create a TODO called Quarterly report":
            return ModelResponse(
                [ToolCallPart("create_todo", {"data": {"title": "Quarterly report"}})]
            )
        previous = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "create_todo"
        ]
        if not previous:
            return ModelResponse([TextPart("Which TODO?")])
        todo_id = previous[-1]["id"]
        return ModelResponse(
            [
                ToolCallPart(
                    "update_todo", {"todo_id": todo_id, "data": {"status": "closed"}}
                )
            ]
        )

    with agent.override(model=FunctionModel(model)):
        first = client.post(
            "/chat",
            json={
                "message": "Create a TODO called Quarterly report",
                "conversation_id": conversation_id,
            },
        )
        assert first.status_code == 200, first.text
        # A fresh DB read reconstructs native tool calls and their results.
        history = load_history(conversation_id)
        assert any(
            isinstance(part, ToolCallPart)
            for message in history
            for part in message.parts
        )
        assert any(
            isinstance(part, ToolReturnPart)
            for message in history
            for part in message.parts
        )
        other = client.post(
            "/chat", json={"message": "Close it", "conversation_id": other_id}
        )
        assert other.json()["reply"] == "Which TODO?"
        followup = client.post(
            "/chat", json={"message": "Close it", "conversation_id": conversation_id}
        )
        assert followup.status_code == 200, followup.text
        assert "closed" in followup.json()["reply"]
    messages = client.get(
        "/messages", params={"conversation_id": conversation_id}
    ).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "Create a TODO called Quarterly report"
    assert messages[2]["content"] == "Close it"
    other_messages = client.get(
        "/messages", params={"conversation_id": other_id}
    ).json()
    assert len(other_messages) == 2 and other_messages[-1]["content"] == "Which TODO?"
    assert (
        client.get("/messages", params={"conversation_id": str(uuid4())}).json() == []
    )
    assert client.get("/messages").status_code == 422
    todo_id = next(
        part.content["id"]
        for message in history
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    assert client.get(f"/todos/{todo_id}").json()["status"] == "closed"
    client.delete(f"/todos/{todo_id}")


def test_concurrent_turns_keep_both_messages(demo):
    from uuid import uuid4

    from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    from app.agent import agent, ask

    client, _, _ = demo
    conversation_id = str(uuid4())

    async def model(messages, info):
        await asyncio.sleep(0.02)
        prompts = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        return ModelResponse([TextPart(f"Seen {len(prompts)} messages")])

    async def run():
        with agent.override(model=FunctionModel(model)):
            return await asyncio.gather(
                ask("First", conversation_id), ask("Second", conversation_id)
            )

    assert asyncio.run(run()) == ["Seen 1 messages", "Seen 2 messages"]
    messages = client.get(
        "/messages", params={"conversation_id": conversation_id}
    ).json()
    assert [m["content"] for m in messages] == [
        "First",
        "Seen 1 messages",
        "Second",
        "Seen 2 messages",
    ]


def test_due_date_migration_preserves_existing_todos(demo, monkeypatch, tmp_path):
    from sqlalchemy import inspect
    from sqlmodel import create_engine

    from app import db

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE todo (id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, "
            "title VARCHAR NOT NULL, text VARCHAR NOT NULL, status VARCHAR NOT NULL, "
            "tags JSON NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO todo VALUES (1, '2026-01-01', 'Existing task', '', 'open', '[]')"
        )
    monkeypatch.setattr(db, "engine", engine)
    db.init_db()
    db.init_db()
    with engine.connect() as connection:
        assert "due_date" in {
            column["name"] for column in inspect(connection).get_columns("todo")
        }
        assert connection.exec_driver_sql("SELECT title, due_date FROM todo").one() == (
            "Existing task",
            None,
        )
    engine.dispose()


def test_streamed_chat_calls_tools_and_keeps_history(demo):
    from uuid import uuid4

    from pydantic_ai.messages import ToolReturnPart, UserPromptPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from app.agent import agent
    from app.conversations import load_history

    client, exporter, _ = demo
    conversation_id = str(uuid4())

    async def model(messages, info):
        last = messages[-1]
        result = next(
            (part for part in last.parts if isinstance(part, ToolReturnPart)), None
        )
        if result:
            yield "Task **"
            await asyncio.sleep(0.02)
            yield result.content["title"]
            yield f"** is {result.content['status']}."
            return
        prompt = next(
            part.content for part in last.parts if isinstance(part, UserPromptPart)
        )
        if prompt == "Create a streamed task":
            name, arguments = (
                "create_todo",
                {
                    "data": {
                        "title": "Streamed task",
                        "due_date": "2026-12-20",
                        "tags": ["stream"],
                    }
                },
            )
        else:
            previous = next(
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "create_todo"
            )
            name, arguments = (
                "update_todo",
                {
                    "todo_id": previous["id"],
                    "data": {"status": "closed", "due_date": None},
                },
            )
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments))}

    with agent.override(model=FunctionModel(stream_function=model)):
        response = client.post(
            "/chat/stream",
            json={
                "message": "Create a streamed task",
                "conversation_id": conversation_id,
            },
            headers={"traceparent": f"00-{'9' * 32}-{'b' * 16}-01"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[0]["type"] == "start"
        assert events[0]["conversation_id"] == conversation_id
        assert events[0]["otel_trace_id"] == "9" * 32
        assert events[-1] == {"type": "done"}
        texts = [event["text"] for event in events if event["type"] == "text"]
        assert len(texts) >= 3
        assert len(texts[0]) < len(texts[-1])
        assert texts[-1] == "Task **Streamed task** is open."
        followup = client.post(
            "/chat/stream",
            json={"message": "Close it", "conversation_id": conversation_id},
        )
        followup_events = [json.loads(line) for line in followup.text.splitlines()]
        assert followup_events[-1]["type"] == "done"
        assert [event["text"] for event in followup_events if event["type"] == "text"][
            -1
        ] == "Task **Streamed task** is closed."
    messages = client.get(
        "/messages", params={"conversation_id": conversation_id}
    ).json()
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[-1]["content"] == "Task **Streamed task** is closed."
    history = load_history(conversation_id)
    created = next(
        part.content
        for message in history
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "create_todo"
    )
    todo = client.get(f"/todos/{created['id']}").json()
    assert todo["status"] == "closed" and todo["due_date"] is None
    client.delete(f"/todos/{created['id']}")
    spans = [
        span
        for span in exporter.get_finished_spans()
        if span.context.trace_id == int("9" * 32, 16)
        and span.attributes.get("gen_ai.operation.name") == "invoke_agent"
    ]
    assert spans
    assert all(
        span.attributes["gen_ai.conversation.id"] == conversation_id for span in spans
    )


def test_stream_error_does_not_save_incomplete_history(demo):
    from uuid import uuid4

    from pydantic_ai.models.function import FunctionModel

    from app.agent import agent

    client, _, _ = demo
    conversation_id = str(uuid4())

    async def failing_model(messages, info):
        yield "Partial answer"
        raise RuntimeError("Provider failed")

    with agent.override(model=FunctionModel(stream_function=failing_model)):
        response = client.post(
            "/chat/stream",
            json={"message": "Hello", "conversation_id": conversation_id},
        )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "error"
    assert not any(event["type"] == "done" for event in events)
    assert (
        client.get("/messages", params={"conversation_id": conversation_id}).json()
        == []
    )

    async def recovered_model(messages, info):
        yield "Recovered"

    with agent.override(model=FunctionModel(stream_function=recovered_model)):
        response = client.post(
            "/chat/stream",
            json={"message": "Retry", "conversation_id": conversation_id},
        )
    assert json.loads(response.text.splitlines()[-1])["type"] == "done"
    assert (
        client.get("/messages", params={"conversation_id": conversation_id}).json()[-1][
            "content"
        ]
        == "Recovered"
    )


def test_cancelled_stream_releases_conversation(demo):
    from uuid import uuid4

    from pydantic_ai.models.function import FunctionModel

    from app.agent import agent, stream_reply
    from app.conversations import load_history

    conversation_id = str(uuid4())

    async def slow_model(messages, info):
        yield "First chunk"
        await asyncio.Event().wait()

    async def recovered_model(messages, info):
        yield "Recovered after cancellation"

    async def run():
        with agent.override(model=FunctionModel(stream_function=slow_model)):
            reply = stream_reply("Start", conversation_id)
            assert await anext(reply) == "First chunk"
            await asyncio.wait_for(reply.aclose(), timeout=5)
        assert load_history(conversation_id) == []
        with agent.override(model=FunctionModel(stream_function=recovered_model)):
            texts = await asyncio.wait_for(
                collect(stream_reply("Retry", conversation_id)), timeout=5
            )
            assert texts[-1] == "Recovered after cancellation"

    async def collect(reply):
        return [text async for text in reply]

    asyncio.run(run())
