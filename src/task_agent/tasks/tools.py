"""Expose task-store operations as NeMo Agent Toolkit functions."""

from __future__ import annotations

import json

from nat.builder.builder import Builder
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig

from task_agent.tasks.models import ExecuteTaskInput
from task_agent.tasks.models import ListTasksInput
from task_agent.tasks.models import ResetTasksInput
from task_agent.tasks.store import task_store


class ListTasksConfig(FunctionBaseConfig, name="list_tasks"):
    """Configuration for listing demo tasks."""


class ExecuteTaskConfig(FunctionBaseConfig, name="execute_task"):
    """Configuration for executing a demo task."""


class ResetTasksConfig(FunctionBaseConfig, name="reset_tasks"):
    """Configuration for resetting demo task state."""


@register_function(config_type=ListTasksConfig)
async def list_tasks_function(_config: ListTasksConfig, _builder: Builder):
    async def _list_tasks(request: ListTasksInput) -> str:
        """List tasks, optionally filtered by status."""
        tasks = await task_store.list(request.status)
        return json.dumps([task.model_dump() for task in tasks], sort_keys=True)

    yield FunctionInfo.from_fn(_list_tasks, description=_list_tasks.__doc__)


@register_function(config_type=ExecuteTaskConfig)
async def execute_task_function(_config: ExecuteTaskConfig, _builder: Builder):
    async def _execute_task(request: ExecuteTaskInput) -> str:
        """Execute one pending task by its ID."""
        result = await task_store.execute(request.task_id)
        if result.outcome == "not_found":
            return json.dumps({"error": "Task not found", "task_id": result.task_id})
        if result.outcome == "already_completed":
            return json.dumps({"message": "Task was already completed", "task": result.task.model_dump()})
        return json.dumps({"message": "Task completed", "task": result.task.model_dump()})

    yield FunctionInfo.from_fn(_execute_task, description=_execute_task.__doc__)


@register_function(config_type=ResetTasksConfig)
async def reset_tasks_function(_config: ResetTasksConfig, _builder: Builder):
    async def _reset_tasks(request: ResetTasksInput) -> str:
        """Reset all demo tasks to pending for a repeatable authorization test."""
        del request
        tasks = await task_store.reset()
        return json.dumps(
            {"message": "Demo tasks reset", "tasks": [task.model_dump() for task in tasks]},
            sort_keys=True,
        )

    yield FunctionInfo.from_fn(_reset_tasks, description=_reset_tasks.__doc__)