"""SQLite tables shared by the API and MCP service."""

import os
from datetime import UTC, date, datetime
from enum import StrEnum

from dotenv import load_dotenv
from pydantic import field_serializer
from sqlalchemy import JSON, Column, inspect
from sqlmodel import Field, SQLModel, create_engine

load_dotenv()
engine = create_engine(
    os.getenv("DATABASE_URL", "sqlite:////data/demo.db"),
    connect_args={"check_same_thread": False, "timeout": 10},
)


class TodoStatus(StrEnum):
    open = "open"
    doing = "doing"
    closed = "closed"


class Todo(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    title: str
    text: str = ""
    status: TodoStatus = TodoStatus.open
    due_date: date | None = None
    tags: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        # SQLite returns naive datetimes; all stored creation times are UTC.
        return value.replace(tzinfo=UTC).isoformat()


class Conversation(SQLModel, table=True):
    id: str = Field(primary_key=True)
    messages_json: str = "[]"


def init_db() -> None:
    # Serialize schema setup when the API and MCP service start together.
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        SQLModel.metadata.create_all(connection)
        columns = {column["name"] for column in inspect(connection).get_columns("todo")}
        if "due_date" not in columns:
            connection.exec_driver_sql("ALTER TABLE todo ADD COLUMN due_date DATE")
