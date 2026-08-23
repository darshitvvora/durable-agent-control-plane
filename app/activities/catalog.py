"""Tool catalog — maps the tool names a manifest declares to activities.

An agent package says `tools: [issue_payment]`; this is where that name becomes a
real activity with the retry policy and timeout appropriate to its class. Adding
a tool means adding an entry here plus naming it in a manifest — never editing
AgentJobWorkflow (CLAUDE.md §2, §11).

Retry policy is per tool *class*, not per tool:

- Consequential (moves money, mutates external state): few attempts, and the
  activity itself must be idempotent. Retrying a non-idempotent side effect is
  how you double-pay.
- Read-only (lookups, enrichment): retry freely, they are safe to repeat.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio.common import RetryPolicy

from app.activities.payment import issue_payment


@dataclass(frozen=True)
class ToolSpec:
    activity: Any
    options: dict[str, Any] = field(default_factory=dict)


CONSEQUENTIAL = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}

READ_ONLY = {
    "start_to_close_timeout": timedelta(seconds=15),
    "retry_policy": RetryPolicy(maximum_attempts=5),
}

TOOL_CATALOG: dict[str, ToolSpec] = {
    "issue_payment": ToolSpec(activity=issue_payment, options=CONSEQUENTIAL),
}


def all_activities() -> list[Any]:
    """Every tool activity, for Worker(activities=...) registration."""
    return [spec.activity for spec in TOOL_CATALOG.values()]
