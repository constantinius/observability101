"""Check real OTLP exports across both Python processes without external services."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_otel_distributed_trace(tmp_path):
    exported = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            request = ExportTraceServiceRequest.FromString(
                self.rfile.read(int(self.headers["Content-Length"]))
            )
            for resource in request.resource_spans:
                service = next(
                    a.value.string_value
                    for a in resource.resource.attributes
                    if a.key == "service.name"
                )
                for scope in resource.scope_spans:
                    exported.extend((service, span) for span in scope.spans)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    mcp_port, api_port = free_port(), free_port()
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'otel.db'}",
        "MCP_URL": f"http://127.0.0.1:{mcp_port}/mcp",
        "AI_MODEL": "demo",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{collector.server_port}",
    }
    processes, logs = [], []
    try:
        for module, port, path in [
            ("app.mcp_server:app", mcp_port, "/mcp"),
            ("app.main:app", api_port, "/health"),
        ]:
            log_path = tmp_path / f"{port}.log"
            log = log_path.open("w")
            logs.append(log)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    module,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                env=environment,
                stdout=log,
                stderr=log,
            )
            processes.append(process)
            for _ in range(150):
                assert process.poll() is None, log_path.read_text()
                try:
                    httpx.get(f"http://127.0.0.1:{port}{path}", timeout=0.2)
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                raise AssertionError(f"Service did not start: {log_path.read_text()}")

        trace_id, parent_id = "c" * 32, "d" * 16
        conversation_id = str(uuid4())
        with (
            suppress_instrumentation(),
            httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=20) as client,
        ):
            response = client.post(
                "/chat",
                json={
                    "message": "List TODOs",
                    "conversation_id": conversation_id,
                },
                headers={
                    "traceparent": f"00-{trace_id}-{parent_id}-01",
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["otel_trace_id"] == trace_id

            streamed = client.post(
                "/chat/stream",
                json={"message": "List TODOs", "conversation_id": str(uuid4())},
                headers={
                    "traceparent": f"00-{'7' * 32}-{parent_id}-01",
                },
            )
            events = [json.loads(line) for line in streamed.text.splitlines()]
            assert events[0]["otel_trace_id"] == "7" * 32
            assert events[-1]["type"] == "done"

        # The shared agent must keep concurrent conversations' traces separate.
        def chat_in_parallel(trace_digit):
            with suppress_instrumentation():
                response = httpx.post(
                    f"http://127.0.0.1:{api_port}/chat",
                    json={
                        "message": "List TODOs",
                        "conversation_id": str(uuid4()),
                    },
                    headers={
                        "traceparent": f"00-{trace_digit * 32}-{parent_id}-01",
                    },
                    timeout=20,
                )
                assert response.status_code == 200, response.text
                assert response.json()["otel_trace_id"] == trace_digit * 32

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(chat_in_parallel, ["e", "f"]))

        # The two exporters batch independently; wait for both to deliver.
        for _ in range(100):
            spans = [
                (service, span)
                for service, span in exported
                if span.trace_id.hex() == trace_id
            ]
            names = {span.name for _, span in spans}
            if "list_todos" in names and any("chat" in name for name in names):
                break
            time.sleep(0.1)
        assert {service for service, _ in spans} == {"todo-backend", "todo-mcp"}

        def attributes(span):
            return {a.key: a.value for a in span.attributes}

        assert any("gen_ai.request.model" in attributes(span) for _, span in spans)
        assert any(
            attributes(span).get("gen_ai.conversation.id", None)
            and attributes(span)["gen_ai.conversation.id"].string_value
            == conversation_id
            for _, span in spans
        )
        assert any(
            "db.statement" in attributes(span) or "db.query.text" in attributes(span)
            for _, span in spans
        ), "SQLite query spans missing"
        tool = next(
            span
            for service, span in spans
            if service == "todo-mcp" and span.name == "list_todos"
        )
        server = next(
            span for service, span in spans if span.span_id == tool.parent_span_id
        )
        assert server.kind == 2, "MCP tool must be a child of its HTTP server span"
        client = next(
            span for service, span in spans if span.span_id == server.parent_span_id
        )
        assert client.kind == 3, (
            "MCP server must continue the backend's HTTP client span"
        )
        for concurrent_id in ["e" * 32, "f" * 32, "7" * 32]:
            for _ in range(100):
                concurrent = [
                    (service, span)
                    for service, span in exported
                    if span.trace_id.hex() == concurrent_id
                ]
                services = {service for service, _ in concurrent}
                if services == {"todo-backend", "todo-mcp"} and any(
                    span.name == "list_todos" for _, span in concurrent
                ):
                    break
                time.sleep(0.1)
            assert services == {"todo-backend", "todo-mcp"}
            assert any(
                service == "todo-mcp" and span.name == "list_todos"
                for service, span in concurrent
            )
    finally:
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=15)
        for log in logs:
            log.close()
        collector.shutdown()
        collector.server_close()
        thread.join(timeout=5)
