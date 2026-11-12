"""OpenTelemetry tracing, exported through the shared OTLP collector."""

import os

from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def init_otel(service_name: str) -> TracerProvider:
    load_dotenv()
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces"),
            schedule_delay_millis=1000,
        )
    )
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    SQLite3Instrumentor().instrument(tracer_provider=provider)
    return provider


def save_request_context(span, scope) -> None:
    # MCP dispatches tools through an internal queue. Save the HTTP context so
    # tool spans can still be children of the incoming server request.
    scope["otel.context"] = trace.set_span_in_context(span)


def tool_span(name: str, ctx):
    request = ctx.request_context.request
    parent = request.scope.get("otel.context") if request is not None else None
    return trace.get_tracer("todo-tools").start_as_current_span(
        name,
        context=parent,
        attributes={"mcp.tool.name": name},
    )
