"""Task domain models shared by the store and NeMo tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field


TaskStatus = Literal["pending", "completed"]
TaskStatusFilter = Literal["all", "pending", "completed"]


class TaskRecord(BaseModel):
    id: str
    title: str
    status: TaskStatus


class ListTasksInput(BaseModel):
    status: TaskStatusFilter = "all"


class ExecuteTaskInput(BaseModel):
    task_id: str = Field(min_length=1)


class ResetTasksInput(BaseModel):
    confirm: Literal[True] = True