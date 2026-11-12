"""MCP tools served by the mcp Compose service."""

import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# WORKSHOP 1: initialize the todo-mcp provider here.
# isort: split

# Register database operations and MCP tools after instrumentation is initialized.
from app import todos  # noqa: E402
from app.db import TodoStatus, init_db  # noqa: E402
from app.todos import TodoCreate, TodoUpdate  # noqa: E402

mcp = FastMCP(
    "TODO tools",
    host="127.0.0.1",
    port=int(os.getenv("MCP_PORT", "8001")),
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["mcp:8001", "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    ),
)


@mcp.tool()
def create_todo(data: TodoCreate, ctx: Context) -> dict:
    """Create a TODO with title, text, status, tags, and an optional ISO due date."""
    return todos.create_todo(data).model_dump(mode="json")


@mcp.tool()
def list_todos(
    ctx: Context, status: TodoStatus | None = None, tags: list[str] | None = None
) -> list[dict]:
    """List TODOs, optionally matching a status and every requested tag exactly."""
    return [todo.model_dump(mode="json") for todo in todos.list_todos(status, tags)]


@mcp.tool()
def get_todo(todo_id: int, ctx: Context) -> dict:
    """Get one TODO by its ID."""
    return todos.get_todo(todo_id).model_dump(mode="json")


@mcp.tool()
def update_todo(todo_id: int, data: TodoUpdate, ctx: Context) -> dict:
    """Update changed fields. Set due_date to null to clear it; other null fields stay unchanged."""
    return todos.update_todo(todo_id, data).model_dump(mode="json")


@mcp.tool()
def delete_todo(todo_id: int, ctx: Context) -> dict:
    """Delete one TODO by its ID."""
    todos.delete_todo(todo_id)
    return {"deleted": todo_id}


@asynccontextmanager
async def lifespan(app):
    init_db()
    async with mcp.session_manager.run():
        yield


app: Any = mcp.streamable_http_app()
app.router.lifespan_context = lifespan
# WORKSHOP 2: instrument the MCP ASGI app here.


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8001")))
