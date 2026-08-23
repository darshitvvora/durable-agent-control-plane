"""Human-in-the-loop approval gate.

The hook runs in workflow context, so it must be deterministic — policy
evaluation is pure comparison, no I/O, no clock, no randomness.

Mechanics worth understanding before editing: `event.interrupt(...)` does not
return on the first pass — it suspends the agent, and `invoke_async` returns an
AgentResult with `stop_reason == "interrupt"`. When the workflow resumes the
agent with an interruptResponse, the hook runs *again* and this time
`event.interrupt(...)` returns the human's decision. So the same code path both
raises the question and consumes the answer.

Waiting costs nothing: the workflow sits in `wait_condition` with no worker
resources held, for as long as the human takes.
"""

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import BeforeToolCallEvent
from temporalio import workflow

from app.registry.models import ApprovalPolicy

APPROVE = "approve"
INTERRUPT_NAME = "approval"


class ApprovalGate(HookProvider):
    def __init__(self, policy: ApprovalPolicy | None) -> None:
        self._policy = policy

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        registry.add_callback(BeforeToolCallEvent, self._gate)

    def _gate(self, event: BeforeToolCallEvent) -> None:
        if self._policy is None:
            return

        tool_name = event.tool_use.get("name", "")
        tool_input = event.tool_use.get("input", {}) or {}
        if not self._policy.requires_approval(tool_name, tool_input):
            return

        reason = {
            "tool": tool_name,
            "input": tool_input,
            "policy": f"{self._policy.field} {self._policy.operator} {self._policy.value}",
        }
        decision = event.interrupt(INTERRUPT_NAME, reason=reason)

        if decision != APPROVE:
            # Cancelling hands the model a tool result explaining the refusal, so
            # it can choose a different course rather than silently stopping.
            event.cancel_tool = f"denied by human reviewer: {decision}"
            workflow.logger.info("tool %s denied: %s", tool_name, decision)
        else:
            workflow.logger.info("tool %s approved", tool_name)
