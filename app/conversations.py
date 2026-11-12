"""Persist the agent's native history and expose its visible chat messages."""

from typing import Literal

from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    TextPart,
    UserPromptPart,
)
from sqlmodel import Session

from app.db import Conversation, engine


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


def load_history(conversation_id: str) -> list[ModelMessage]:
    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return []
        return ModelMessagesTypeAdapter.validate_json(conversation.messages_json)


def save_history(conversation_id: str, messages_json: bytes) -> None:
    with Session(engine) as session:
        session.merge(
            Conversation(id=conversation_id, messages_json=messages_json.decode())
        )
        session.commit()


def get_messages(conversation_id: str) -> list[ChatMessage]:
    messages = []
    for message in load_history(conversation_id):
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                messages.append(ChatMessage(role="user", content=part.content))
            elif isinstance(part, TextPart):
                messages.append(ChatMessage(role="assistant", content=part.content))
    return messages
