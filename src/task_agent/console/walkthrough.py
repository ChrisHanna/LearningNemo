"""Server-owned walkthrough plans selected from the signed-in user's roles."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

from task_agent.console.identity import Role


@dataclass(frozen=True)
class WalkthroughStep:
    id: str
    persona: Role
    title: str
    prompt: str
    tool: str
    expected: str
    runs: str
    proves: str
    assertion: str


READER_STEPS = (
    WalkthroughStep(
        id="reader-baseline",
        persona="reader",
        title="Capture current task state",
        prompt="List all tasks with their IDs and statuses. Use one line per task.",
        tool="list_tasks",
        expected="Allowed: return both task IDs and remember task-1's status.",
        runs="JWT → Task.Reader → Guardrails → list_tasks",
        proves="The signed-in Reader can use an allowed read tool.",
        assertion="baseline",
    ),
    WalkthroughStep(
        id="reader-denied-execute",
        persona="reader",
        title="Attempt a write as Reader",
        prompt="Execute task-1.",
        tool="execute_task",
        expected="Denied: tasks.execute is present, but Task.Operator is missing.",
        runs="execute_task → tasks.execute + Task.Operator",
        proves="A broad client scope does not override the user's app-role assignment.",
        assertion="denied",
    ),
    WalkthroughStep(
        id="reader-verify-unchanged",
        persona="reader",
        title="Verify no hidden mutation",
        prompt="List task-1 and its current status.",
        tool="list_tasks",
        expected="Allowed: task-1 status must match the captured baseline.",
        runs="Reader token → list_tasks → store.list",
        proves="The denied write never reached the mutable task store.",
        assertion="unchanged",
    ),
)

OPERATOR_STEPS = (
    WalkthroughStep(
        id="operator-reset",
        persona="operator",
        title="Reset the demo state",
        prompt="Reset all demo tasks to pending, then report each task ID and status.",
        tool="reset_tasks",
        expected="Allowed: both demo tasks become pending.",
        runs="JWT → Task.Operator → Guardrails → reset_tasks",
        proves="The Operator role permits an intentional state mutation.",
        assertion="reset",
    ),
    WalkthroughStep(
        id="operator-baseline",
        persona="operator",
        title="Confirm the pending baseline",
        prompt="List all tasks with their IDs and statuses. Use one line per task.",
        tool="list_tasks",
        expected="Allowed: both tasks are visible as pending.",
        runs="Task.Reader → list_tasks → store.list",
        proves="The same Operator token also carries the Reader role.",
        assertion="pending",
    ),
    WalkthroughStep(
        id="operator-execute",
        persona="operator",
        title="Execute task-1 as Operator",
        prompt="Execute task-1.",
        tool="execute_task",
        expected="Allowed: task-1 changes from pending to completed.",
        runs="execute_task → tasks.execute + Task.Operator → store.execute",
        proves="Scope and role authorize the write; the next read verifies persisted state.",
        assertion="execution-succeeded",
    ),
    WalkthroughStep(
        id="operator-verify-completed",
        persona="operator",
        title="Verify the completed state",
        prompt="List task-1 and its current status.",
        tool="list_tasks",
        expected="Allowed: task-1 is reported as completed.",
        runs="Task.Reader → list_tasks → store.list",
        proves="The authorized write produced the expected state transition.",
        assertion="completed",
    ),
)
WALKTHROUGH_BY_PERSONA = {
    "reader": READER_STEPS,
    "operator": OPERATOR_STEPS,
}
WALKTHROUGH_BY_ID = {
    step.id: step
    for steps in WALKTHROUGH_BY_PERSONA.values()
    for step in steps
}


def walkthrough_manifest(persona: Role) -> list[dict[str, str]]:
    return [asdict(step) for step in WALKTHROUGH_BY_PERSONA[persona]]