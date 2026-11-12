# TODO app

A small TODO app with a plain JavaScript frontend, FastAPI, SQLModel/SQLite, and a Gemini agent. The agent calls a separate MCP service. OpenTelemetry tracing cover the service flow.

## Run

Requires Docker with Docker Compose 2.20+ (for Compose includes). Allocate at least 4 GB of memory to Docker for SigNoz. From the project directory:

```sh
docker compose up
```

This starts the frontend, backend, MCP service, a shared OpenTelemetry collector, Jaeger, Grafana, and SigNoz with its storage dependencies. The first run downloads images and installs dependencies. No host Python, uv, Node.js, or npm installation is needed. With no `.env`, the key-free demo agent only lists TODOs; the UI still supports all CRUD operations.

For Gemini, optionally copy `.env.example` to `.env`, set `AI_MODEL=google:gemini-3.6-flash`, add your AI Studio `GOOGLE_API_KEY`. Existing `.env` settings are read by Compose. Never use `localhost` for calls between containers; each container has its own loopback interface.

### Ports

All published ports bind to your machine's loopback interface.

| Port | App | Local URL |
| --- | --- | --- |
| 5173 | Frontend | http://localhost:5173 |
| 8000 | FastAPI backend and API docs | http://localhost:8000/docs |
| 8001 | MCP tools, used by the agent | http://localhost:8001/mcp |
| 16686 | Jaeger UI | http://localhost:16686 |
| 3000 | Grafana (Explore → Jaeger) | http://localhost:3000/explore |
| 8080 | SigNoz | http://localhost:8080 |
| 4318 | Shared OTLP HTTP receiver | http://localhost:4318/v1/traces |

### Service URLs

These optional `.env` settings have working Compose defaults:

| Variable | Default | Used by |
| --- | --- | --- |
| `BACKEND_URL` | `http://backend:8000` | Frontend's Vite proxy |
| `MCP_URL` | `http://mcp:8001/mcp` | Backend agent |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | Both Python exporters and Vite's browser telemetry proxy |
| `DATABASE_URL` | `sqlite:////data/demo.db` | Both Python services |

The browser calls same-origin `/api/...` and `/otlp/v1/traces`. Vite forwards them to the URLs above, including streaming chat responses. Docker service names stay inside the Compose network.

Both Python services reload when files in `app/` change. Vite watches the mounted `frontend/` directory, with polling for Docker Desktop. Database writes do not trigger reloads. Frontend dependencies live in a Docker volume and `npm ci` runs when its container starts. After changing Python dependencies, run `docker compose up --build`; after changing frontend dependencies, run `docker compose restart frontend`. After editing `.env`, run `docker compose up -d --force-recreate`.

Use `docker compose logs -f` for logs and `docker compose down` to stop the stack. TODOs and conversation history remain in `data/demo.db` across container recreation. SigNoz and Grafana use persistent named volumes; Jaeger traces are held in memory. `docker compose down` preserves the volumes; adding `-v` removes the observability data and frontend dependency cache.

## TODOs

Each TODO has an ID, UTC creation time, title, text, status (`open`, `doing`, `closed`), tags, and an optional due date (`YYYY-MM-DD`). Creation time is assigned by the server and cannot be edited. New TODOs default to `open`, with empty text and tags.

The main view is a compact task list. Click a row to open its details in a drawer, or click **New task** to create one. Save changes or delete a task from the drawer. Due dates can be added, changed, or cleared there. Status and tag filters can be combined. Status changes apply immediately; tag typing applies after a 250 ms debounce. Reset clears both filters immediately. Tags match exactly and are case-sensitive; multiple tags require all of them. The UI accepts comma-separated tags.

The collapsible **Task assistant** box is in the lower-right corner. Click its header to open or collapse it; use the expand button for a larger chat. Replies stream into the box as sanitized Markdown. Enter sends a message; Shift+Enter inserts a line break. **New conversation** starts a fresh chat with a new ID and no previous context. Earlier conversations remain stored in SQLite; starting a new one does not delete them. The button is disabled while a reply is streaming. The assistant can manage the same TODOs. For example:

- “Create a TODO titled Review demo, due tomorrow, with the text Check the traces, tagged work.”
- “Show open TODOs tagged work.”
- “Move TODO 3 to doing and set its due date to November 15.”
- “Change TODO 3's text to Ready for review and tags to work, urgent.”
- “Delete TODO 3.”

The list refreshes after each chat response, respecting the active filters. Each conversation persists the full agent message history in SQLite, including tool calls and results. Follow-ups such as “mark it closed” can refer to earlier TODOs. History survives page reloads and backend restarts. Assistant replies render as sanitized Markdown; user messages remain plain text.

## HTTP API and MCP

Both interfaces use the same functions in `app/todos.py`. The MCP service calls these functions directly; it does not duplicate the business logic or proxy HTTP requests. Application MCP calls go through the agent.

| HTTP | MCP tool | Operation |
| --- | --- | --- |
| `POST /todos` | `create_todo(data)` | Create |
| `GET /todos?status=open&tags=work&tags=urgent` | `list_todos(status, tags)` | List and filter |
| `GET /todos/{id}` | `get_todo(todo_id)` | Read |
| `PATCH /todos/{id}` | `update_todo(todo_id, data)` | Update selected fields |
| `DELETE /todos/{id}` | `delete_todo(todo_id)` | Delete |

Create body:

```json
{"title": "Review demo", "text": "Check the traces", "status": "open", "tags": ["work"], "due_date": "2026-11-15"}
```

Update body, for example: `{"status": "doing"}`. Omitted update fields stay unchanged. Set `"due_date": null` to remove a due date. Null values for other fields stay unchanged. Send `"text": ""` or `"tags": []` to clear those fields. Missing TODOs return HTTP 404; invalid API inputs return 422. Successful deletion returns 204.

MCP is available at http://127.0.0.1:8001/mcp. Tools accept the same create/update schemas and filter semantics. Chat endpoints are `POST /chat` (complete JSON response), `POST /chat/stream` (streamed response), `GET /messages?conversation_id=<UUID>`, and `GET /health`. Message history is scoped to the supplied conversation ID; no history is accepted from the browser.

## Tracing

This checkout is the workshop starter: application instrumentation is intentionally incomplete. Follow [WORKSHOP.md](WORKSHOP.md) to enable OpenTelemetry for the browser, API, agent, and MCP service. It sends OTLP traces to `http://otel-collector:4318`; `OTEL_EXPORTER_OTLP_ENDPOINT` configures both Python exporters and the frontend telemetry proxy. Applications know only the collector URL. Backend destinations live in `observability/collector.yaml`. No frontend environment file or enable flags are needed.

```text
Browser → Vite /otlp ─┐
Backend / agent ─────┼→ OTLP collector ─┬→ Jaeger ← Grafana
MCP ─────────────────┘                 └→ SigNoz collector → ClickHouse ← SigNoz
```

The shared collector batches each trace and exports it independently to Jaeger and SigNoz, preserving trace IDs and span relationships. Grafana queries Jaeger directly, so no additional trace store is needed. Jaeger is pinned to 2.20 because 2.21 removed the legacy service-search HTTP endpoint used by Grafana's Jaeger data source. Upgrade them together once the data source supports Jaeger's newer API. Each exporter has its own queue and retries; the demo uses in-memory queues, so a collector restart can lose queued traces.

- **Jaeger:** open http://localhost:16686 and select `todo-frontend`, `todo-backend`, or `todo-mcp`.
- **Grafana:** open http://localhost:3000/explore. The Jaeger data source is provisioned and selected by default. Sign in with `admin` / `admin` on first use; Grafana prompts you to change the password. Explore requires an authenticated Editor or Admin; anonymous sessions have read-only Viewer access. Search by service or paste the same trace ID shown in the app.
- **SigNoz:** open http://localhost:8080 and sign in with `admin@admin.at` / `Admin123456!`. Compose provisions this root admin in the `default` organization automatically at startup. Find the services under APM or search the trace ID in Traces. Its collector converts OTLP to SigNoz's ClickHouse schema and derives APM metrics from spans; service metrics can take about a minute to appear.

The SigNoz root account is configured with `SIGNOZ_USER_ROOT_ENABLED`, `SIGNOZ_USER_ROOT_EMAIL`, `SIGNOZ_USER_ROOT_PASSWORD`, and `SIGNOZ_USER_ROOT_ORG_NAME` in `.env`; their Compose defaults work without an `.env` file. SigNoz v0.147.0 requires a password with at least 12 characters, uppercase, lowercase, a digit, and a symbol, so `admin` is rejected. Change root credentials in `.env` and run `docker compose up -d --no-deps signoz` to recreate the container; the UI cannot edit or delete the root account. For an existing SigNoz database, the configured organization name must match the existing organization to keep the root account in the same workspace. This installation's unnamed organization was set to `default`, preserving its users and data. See the [root-user research note](observability/signoz/root-user.md) for sources and migration details.

The app exports traces only. This setup does not add application logs or host metrics. All published ports remain loopback-only; ClickHouse, ZooKeeper, and the SigNoz collector are internal services.

`observability/compose.yaml` contains the observability stack. SigNoz needs ClickHouse, ZooKeeper, an initial histogram helper, and a one-shot schema migration. Compose orders their startup automatically; the first run can take several minutes. These supporting services require no application configuration. The SigNoz configuration follows the [upstream deployment components](https://github.com/SigNoz/signoz/tree/v0.147.0/.devenv/docker), with versions pinned. Grafana uses its [built-in Jaeger data source](https://grafana.com/docs/grafana/latest/datasources/jaeger/configure/).

After changing collector destinations, run `docker compose restart otel-collector`; application containers do not need changes. After changing Grafana provisioning, run `docker compose restart grafana`.

```text
TODO form → HTTP API → shared TODO operations → SQLite
Chat → HTTP API → agent → MCP → shared TODO operations → SQLite
```

After completing the workshop, each UI action starts a new OTel trace. Browser requests propagate W3C `traceparent` to the API, which continues them through agent and MCP calls. Vite proxies browser OTLP exports to the shared collector. The chat displays the conversation ID and OTel trace ID.

One conversation ID is retained per browser tab session, including reloads. After enabling agent instrumentation, the API passes it to Pydantic AI so AI spans carry `gen_ai.conversation.id`. It correlates turns without merging their traces and identifies the persisted history passed to subsequent agent runs.

Jaeger uses in-memory storage; stopping it loses its traces. Stop with `docker compose down`. No model key is needed to test Jaeger with `AI_MODEL=demo`.

## Source and storage

| File | Purpose |
| --- | --- |
| `app/db.py` | TODO and conversation tables, SQLite setup |
| `app/conversations.py` | Native agent history persistence and visible chat messages |
| `app/todos.py` | Input schemas and shared CRUD/filtering logic |
| `app/main.py` | HTTP routes |
| `app/mcp_server.py` | Thin MCP tool adapters |
| `app/agent.py` | Module-level agent and conversation tracing |
| `app/telemetry.py` | Python OTel setup |
| `frontend/src.js` | TODO forms, filters, list, chat |
| `frontend/telemetry.js` | Browser OTel spans and tracing headers |
| `observability/collector.yaml` | Shared OTLP receiver and destination fan-out |
| `observability/compose.yaml` | Jaeger, Grafana, SigNoz, and storage dependencies |
| `observability/grafana/datasources.yaml` | Provisioned Jaeger data source |

Both Python services mount the same `data/` directory and use `data/demo.db`. The previous root `demo.db`, if present, is no longer the active database; copy it into `data/` with the stack stopped to migrate an earlier setup. Tables are created automatically; there is no seed data. Existing TODO tables get a nullable due-date column automatically, preserving their records. Existing database tables are preserved. To reset, run `docker compose down`, delete `data/demo.db`, and start the stack again. Published ports are configured for local use.

## Checks

For source development, install uv and Node.js 20+ locally, then run `uv sync` and `npm ci --prefix frontend`. Run checks with:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
cd frontend
npm run build
```

Tests exercise HTTP CRUD/validation/filtering, every MCP operation through an agent, due-date migration, conversation memory/isolation, streamed tools/replies/errors/cancellation, concurrent turns, and OTel distributed traces using temporary databases and local telemetry collectors. Real Gemini calls require your key and are separate from these deterministic tests.

## Chat history and Markdown

The browser retains its conversation ID in session storage. Every chat request loads that conversation's native Pydantic AI messages using `ModelMessagesTypeAdapter`, passes them as `message_history`, and saves the completed run's `all_messages_json()`. The visible transcript is derived from the same history. Tool calls and results stay available to the model without being shown as separate chat bubbles. Turns within one conversation are serialized in the single backend process.

Existing shared transcript rows remain in the database, but did not record conversation IDs or tool history. They are not automatically assigned to new conversations; memory starts with new turns.

Assistant Markdown is parsed with Marked and sanitized with DOMPurify before insertion into the page. Scripts, event handlers, unsafe link protocols, embedded media, and forms are removed. Formatting is reapplied when history is restored.

Each service calls `init_otel()` once with its explicit name: `init_otel("todo-backend")` or `init_otel("todo-mcp")`. The agent is constructed once in `app/agent.py`; each run opens its own MCP connection and loads its conversation history.

## Streaming chat

The UI posts the same `{message, conversation_id}` body to `/chat/stream`. The response uses newline-delimited JSON (`application/x-ndjson`): a `start` event with conversation/trace IDs, `text` events with the current text, and a final `done` event. Failures produce an `error` event instead of `done`; the HTTP response may already be 200 because streaming has started.

`agent.run_stream_events()` continues through model and MCP tool calls. Completed turns save the full native history; interrupted turns do not overwrite it. Disconnecting closes the agent stream and releases the conversation lock. The UI updates one assistant bubble as text arrives and refreshes the task list after completion. No live provider calls are needed for the deterministic streaming tests.
