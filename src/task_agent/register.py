"""Import component modules so NeMo Agent Toolkit discovers registrations."""

from task_agent.security import authorization
from task_agent.security import semantic_guardrail
from task_agent.tasks import tools

__all__ = ["authorization", "semantic_guardrail", "tools"]