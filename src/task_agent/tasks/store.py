"""Concurrency-safe in-memory task store used by the learning example."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from task_agent.tasks.models import TaskRecord
from task_agent.tasks.models import TaskStatusFilter


ExecutionOutcome = Literal["not_found", "already_completed", "completed"]


@dataclass(frozen=True)
class TaskExecutionResult:
    outcome: ExecutionOutcome
    task_id: str
    task: TaskRecord | None = None


class InMemoryTaskStore:
    """Own task state and serialize all reads and writes with one lock."""

    def __init__(self, tasks: Iterable[TaskRecord]) -> None:
        self._tasks = {task.id: task.model_copy(deep=True) for task in tasks}
        self._lock = asyncio.Lock()

    async def list(self, status: TaskStatusFilter = "all") -> list[TaskRecord]:
        async with self._lock:
            return [
                task.model_copy(deep=True)
                for task in self._tasks.values()
                if status == "all" or task.status == status
            ]

    async def execute(self, task_id: str) -> TaskExecutionResult:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return TaskExecutionResult(outcome="not_found", task_id=task_id)
            if task.status == "completed":
                return TaskExecutionResult(
                    outcome="already_completed",
                    task_id=task_id,
                    task=task.model_copy(deep=True),
                )
            task.status = "completed"
            return TaskExecutionResult(
                outcome="completed",
                task_id=task_id,
                task=task.model_copy(deep=True),
            )

    async def reset(self) -> list[TaskRecord]:
        async with self._lock:
            for task in self._tasks.values():
                task.status = "pending"
            return [task.model_copy(deep=True) for task in self._tasks.values()]


task_store = InMemoryTaskStore(
    [
        TaskRecord(id="task-1", title="Prepare daily report", status="pending"),
        TaskRecord(id="task-2", title="Verify backup", status="pending"),
    ]
)