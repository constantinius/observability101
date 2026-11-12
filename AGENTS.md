# Working on this project

This is a small local TODO app used to demonstrate OpenTelemetry with Jaeger, Grafana, and SigNoz. Keep the code straightforward, idiomatic, and easy to read. Add only what the requested functionality needs.

## Structure

- `app/todos.py`: shared TODO schemas, CRUD operations, and filtering. HTTP routes and MCP tools both delegate here.
- `app/db.py`: SQLModel tables and SQLite setup.
- `app/main.py`: FastAPI routes.
- `app/mcp_server.py`: thin MCP tool adapters. Application MCP calls go through the agent.
- `app/agent.py`: one module-level agent, with a separate MCP connection per run.
- `app/conversations.py`: native Pydantic AI history persisted per conversation, including tool calls and results.
- `app/telemetry.py`: OpenTelemetry setup.
- `frontend/`: plain JavaScript, HTML, and CSS. Assistant Markdown is sanitized with DOMPurify; user messages stay plain text.

## Conventions

- Run the complete development stack with `docker compose up`. Keep Python reload mounts scoped to `app/`, frontend hot reload enabled, and SQLite shared through `data/`. Container URLs use service names and environment variables; browser calls use the same-origin Vite proxies.
- Use uv to manage Python dependencies and run Python commands. Use npm for the frontend. Commit lockfile changes with dependency changes when working in a Git checkout.
- Ruff handles Python formatting, import sorting, linting, and straightforward modernization. Use its configuration in `pyproject.toml`; avoid broad rule suppressions.
- Keep standard-library imports, third-party imports, and application imports grouped and sorted.
- Initialize instrumentation before constructing the agent/database engine or registering MCP tools. The service entrypoints intentionally have a small group of late application imports with individual `# noqa: E402` comments. Keep these exceptions narrow and explained.
- Application OTLP exporters target only `otel-collector`; keep tracing backend URLs in `observability/collector.yaml`. Grafana queries Jaeger. SigNoz storage and ingestion stay in `observability/`; do not add backend-specific application exporters.
- This checkout is the workshop starter; preserve the instrumentation placeholders until completing [WORKSHOP.md](WORKSHOP.md). In the completed exercises, OpenTelemetry is always enabled. Call `init_otel("todo-backend")` or `init_otel("todo-mcp")` with an explicit service name. Do not use service-name environment mutation or optional-provider checks.
- Keep HTTP and MCP behavior consistent. Validate inputs with Pydantic and keep persistence logic out of route/tool adapters.
- Stream agent replies with native run events so tools finish before saving history. Keep the NDJSON response protocol and sanitized Markdown rendering consistent.
- Preserve per-conversation history and tracing isolation. Never combine histories from different conversation IDs.
- Prefer small functions and direct code over new abstractions, frameworks, or configuration switches.

## Checks

Run checks relevant to the change. For Python changes:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Apply formatting with `uv run ruff format .` and safe lint fixes with `uv run ruff check --fix .`.

For frontend changes:

```sh
npm run build --prefix frontend
```

Tests should use temporary databases, deterministic models, and local telemetry collection. Preserve tests covering CRUD, MCP calls through the agent, conversation memory, concurrent requests, and distributed tracing. Keep README setup instructions current when commands or configuration change.
