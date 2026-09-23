from __future__ import annotations

from task_agent.tasks.store import task_store


async def test_reset_demo_tasks_restores_pending_state() -> None:
    await task_store.execute("task-1")
    await task_store.execute("task-2")

    snapshot = await task_store.reset()

    assert [task.id for task in snapshot] == ["task-1", "task-2"]
    assert all(task.status == "pending" for task in snapshot)
    assert all(task.status == "pending" for task in await task_store.list())