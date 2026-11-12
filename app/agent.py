"""The agent discovers and calls tools over HTTP, in another process."""

import asyncio
import os
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date

from pydantic_ai import Agent, AgentRunResultEvent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits

from app.conversations import load_history, save_history

# Serialize turns within a conversation so concurrent requests do not overwrite history.
conversation_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

model: str | TestModel = os.getenv("AI_MODEL", "demo")
if model == "demo":
    # The key-free demo lists real TODOs. Gemini handles natural-language CRUD.
    model = TestModel(call_tools=["list_todos"])

agent = Agent(
    model,
    name="todo-assistant",
    defer_model_check=True,
    # WORKSHOP 2: enable agent instrumentation here.
    instructions=(
        "Help the user create, read, update, delete, and filter TODOs using the tools. "
        "Statuses are open, doing, and closed. Tags match exactly; multiple tag "
        "filters require all tags. Find existing TODO IDs with list_todos or "
        "get_todo; never invent IDs or claim changes without calling a tool. "
        "Use earlier messages and tool results to resolve references in follow-up requests. "
        "For updates send only fields the user wants changed. "
        "Due dates use YYYY-MM-DD; set due_date to null to remove it. Be brief."
    ),
    # Each run gets its own MCP connection and tracing context.
    toolsets=[
        lambda ctx: MCPToolset(
            os.getenv("MCP_URL", "http://mcp:8001/mcp"),
            read_timeout=15,
            tool_error_behavior="error",
        )
    ],
)


@agent.instructions
def current_date() -> str:
    return f"Today is {date.today().isoformat()}."


async def ask(message: str, conversation_id: str) -> str:
    async with conversation_locks[conversation_id]:
        history = load_history(conversation_id)
        result = await agent.run(
            message,
            message_history=history,
            conversation_id=conversation_id,
            usage_limits=UsageLimits(request_limit=6),
        )
        save_history(conversation_id, result.all_messages_json())
        return result.output


async def stream_reply(message: str, conversation_id: str) -> AsyncIterator[str]:
    async with conversation_locks[conversation_id]:
        history = load_history(conversation_id)
        text = ""
        async with agent.run_stream_events(
            message,
            message_history=history,
            conversation_id=conversation_id,
            usage_limits=UsageLimits(request_limit=6),
        ) as events:
            async for event in events:
                if isinstance(event, PartStartEvent) and isinstance(
                    event.part, TextPart
                ):
                    text += event.part.content
                    yield text
                elif isinstance(event, PartDeltaEvent) and isinstance(
                    event.delta, TextPartDelta
                ):
                    text += event.delta.content_delta
                    yield text
                elif isinstance(event, AgentRunResultEvent):
                    save_history(conversation_id, event.result.all_messages_json())
                    yield event.result.output
