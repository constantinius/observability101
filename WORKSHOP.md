# OpenTelemetry workshop

The application and collector start with `docker compose up`. Open the app at http://localhost:5173 and Jaeger at http://localhost:16686. The starter runs without application tracing. With `AI_MODEL=demo`, the assistant lists TODOs without a Gemini key. Each `WORKSHOP` comment marks a paste location. Python and JavaScript dependencies are already installed by Compose; source changes reload automatically.

## Exercise 1: Initialize the Python SDK

Replace the entire contents of `app/telemetry.py` with:

```python
import os

from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
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
    return provider


def instrument_libraries():
    # Exercise 2
    pass
```

Replace the `WORKSHOP 1` comment in `app/main.py` with:

```python
from app.telemetry import init_otel, instrument_libraries

otel_provider = init_otel("todo-backend")
instrument_libraries()
```

Replace the corresponding comment in `app/mcp_server.py` with the same code, changing `todo-backend` to `todo-mcp`. Initialization must occur before the late application imports. `instrument_libraries()` is a placeholder until exercise 2.

To verify the SDK immediately, run this in a second terminal:

```sh
docker compose exec -T backend uv run --no-sync python - <<'PYCODE'
from opentelemetry import trace
from app.telemetry import init_otel

provider = init_otel("todo-backend")
with trace.get_tracer("workshop").start_as_current_span("workshop.sdk_check"):
    pass
provider.shutdown()
PYCODE
```

Find `workshop.sdk_check` under `todo-backend` in Jaeger. The provider identifies the service, the processor batches spans, and the exporter sends them to the collector.

## Exercise 2: Automatic instrumentation

Add these imports at the top of `app/telemetry.py` and replace its `instrument_libraries()` placeholder with this function:

```python
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
```

```python
def instrument_libraries():
    HTTPXClientInstrumentor().instrument()
    SQLite3Instrumentor().instrument()
```

In `app/main.py`, add `from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor` with the other imports. Replace the `WORKSHOP 2` comment after `app = FastAPI(...)` with:

```python
FastAPIInstrumentor.instrument_app(
    app, tracer_provider=otel_provider, exclude_spans=["receive", "send"]
)
```

In `app/mcp_server.py`, add `from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware` with the other imports. Replace the `WORKSHOP 2` comment after `app.router.lifespan_context = lifespan` with:

```python
app = OpenTelemetryMiddleware(
    app, tracer_provider=otel_provider, exclude_spans=["receive", "send"]
)
```

In `app/agent.py`, add `from pydantic_ai.capabilities import Instrumentation` with the imports. Replace the `WORKSHOP 2` comment inside `Agent(...)` with:

```text
capabilities=[Instrumentation()],
```

Create a TODO and ask the assistant to list TODOs. Find HTTP, SQLite, agent and MCP spans in Jaeger. Inspect `gen_ai.conversation.id` on agent spans. Follow the HTTP client/server spans across backend and MCP; they share a trace ID.

## Exercise 3: Add a manual span

Choose a business operation, for example inside a function in `app/todos.py`, and wrap the work you want to measure:

```python
from opentelemetry import trace

tracer = trace.get_tracer("todo-workshop")
with tracer.start_as_current_span("todo.process") as span:
    span.set_attribute("todo.operation", "process")
    span.set_attribute("todo.tag_count", 2)
    # Put the operation you want to measure here.
```

Run that operation and find `todo.process` and its attributes in Jaeger. Use values from your chosen operation instead of the example values.

## Exercise 4: Instrument the browser

Replace the entire contents of `frontend/telemetry.js` with:

```javascript
// Explicit span contexts keep this demo small: no global async patching needed.
import {ROOT_CONTEXT, SpanStatusCode, propagation, trace} from '@opentelemetry/api';
import {resourceFromAttributes} from '@opentelemetry/resources';
import {BatchSpanProcessor} from '@opentelemetry/sdk-trace-base';
import {WebTracerProvider} from '@opentelemetry/sdk-trace-web';
import {OTLPTraceExporter} from '@opentelemetry/exporter-trace-otlp-http';

const provider = new WebTracerProvider({
  resource: resourceFromAttributes({'service.name': 'todo-frontend'}),
  spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter({
    // Vite proxies this same-origin URL to the collector; no collector CORS needed.
    url: new URL('/otlp/v1/traces', window.location.origin).href,
  }), {scheduledDelayMillis: 1000})],
});
provider.register();

export async function otelSpan(name, callback, parent = ROOT_CONTEXT) {
  const span = trace.getTracer('todo-frontend').startSpan(name, {}, parent);
  try {
    return await callback(trace.setSpan(parent, span));
  } catch (error) {
    span.recordException(error);
    span.setStatus({code: SpanStatusCode.ERROR, message: error.message});
    throw error;
  } finally {
    span.end();
  }
}

export function otelHeaders(context) {
  const headers = {};
  propagation.inject(context, headers);
  return headers;
}
```

Reload the page and send a chat message. Open **Trace details**, copy the trace ID and find it in Jaeger. The trace starts in `todo-frontend` and continues through the API, agent and MCP service. `otelHeaders()` injects W3C `traceparent`; the Vite proxy forwards requests and collector exports.

## Checks

Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest` and `npm run build --prefix frontend`. After exercise 2, run `uv run pytest -m instrumentation` to check the distributed Python trace. The default test run checks app functionality and excludes this exercise completion check. The original completed implementation is available in Git commit `1f1d84a` for reference.
