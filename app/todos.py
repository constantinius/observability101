"""TODO operations used by both HTTP routes and MCP tools."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.db import Todo, TodoStatus, engine


class TodoCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    text: str = ""
    status: TodoStatus = TodoStatus.open
    due_date: date | None = None
    tags: list[str] = Field(default_factory=list)


class TodoUpdate(TodoCreate):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    text: str | None = None
    status: TodoStatus | None = None
    tags: list[str] | None = None


class TodoNotFound(LookupError):
    pass


def _get(session: Session, todo_id: int) -> Todo:
    todo = session.get(Todo, todo_id)
    if todo is None:
        raise TodoNotFound(f"TODO {todo_id} not found")
    return todo


def create_todo(data: TodoCreate) -> Todo:
    with Session(engine) as session:
        todo = Todo(**data.model_dump())
        session.add(todo)
        session.commit()
        session.refresh(todo)
        return todo


def list_todos(
    status: TodoStatus | None = None, tags: list[str] | None = None
) -> list[Todo]:
    query = select(Todo).order_by(Todo.id.desc())
    if status is not None:
        query = query.where(Todo.status == status)
    with Session(engine) as session:
        todos = session.exec(query).all()
        # Exact tag matching, requiring every requested tag. Enough for this small app.
        return [todo for todo in todos if all(tag in todo.tags for tag in tags or [])]


def get_todo(todo_id: int) -> Todo:
    with Session(engine) as session:
        return _get(session, todo_id)


def update_todo(todo_id: int, data: TodoUpdate) -> Todo:
    with Session(engine) as session:
        todo = _get(session, todo_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            if value is not None or field == "due_date":
                setattr(todo, field, value)
        session.add(todo)
        session.commit()
        session.refresh(todo)
        return todo


def delete_todo(todo_id: int) -> None:
    with Session(engine) as session:
        session.delete(_get(session, todo_id))
        session.commit()
