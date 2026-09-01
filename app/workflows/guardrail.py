"""Bedrock Guardrails gate — checks a proposed consequential tool call against
the payment guardrail before it runs (E7.1 T3).

Unlike `ApprovalGate`, this hook is async: `BeforeToolCallEvent` is dispatched
via Strands' `invoke_callbacks_async`, which supports a mix of sync and async
callbacks (confirmed by reading `strands.hooks.HookRegistry` source), so the
guardrail check can `await workflow.execute_activity(...)` directly here
rather than needing a synchronous interrupt/resume round trip like approval.
"""

import json
from collections.abc import Callable
from datetime import timedelta

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import BeforeToolCallEvent
from temporalio import workflow
from temporalio.common import RetryPolicy

from app.activities.guardrail import GuardrailVerdict, apply_guardrail

GUARDRAIL_START_TO_CLOSE = timedelta(seconds=10)


class GuardrailGate(HookProvider):
    """`on_verdict` hands each real verdict back to the workflow, which publishes
    it on its Workflow Stream for the session terminal (E5.1 T4). The activity
    result surfaces here in *workflow* context, so the workflow can publish it
    directly — unlike token deltas, which never leave the activity and so need
    `WorkflowStreamClient.from_within_activity()` instead.
    """

    def __init__(
        self,
        guarded_tools: frozenset[str],
        on_verdict: Callable[[str, GuardrailVerdict], None] | None = None,
    ) -> None:
        self._guarded_tools = guarded_tools
        self._on_verdict = on_verdict

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        registry.add_callback(BeforeToolCallEvent, self._gate)

    async def _gate(self, event: BeforeToolCallEvent) -> None:
        tool_name = event.tool_use.get("name", "")
        if tool_name not in self._guarded_tools:
            return

        tool_input = event.tool_use.get("input", {}) or {}
        verdict = await workflow.execute_activity(
            apply_guardrail,
            args=[json.dumps({"tool": tool_name, "input": tool_input})],
            start_to_close_timeout=GUARDRAIL_START_TO_CLOSE,
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        if self._on_verdict is not None and verdict.evaluated:
            self._on_verdict(tool_name, verdict)

        if verdict.blocked:
            # Cancelling hands the model a tool result explaining the block,
            # so it can choose a different course rather than silently
            # stopping — same shape as ApprovalGate's denial.
            event.cancel_tool = f"blocked by guardrail: {verdict.reason}"
            workflow.logger.info("tool %s blocked by guardrail: %s", tool_name, verdict.reason)
        else:
            workflow.logger.info("tool %s passed guardrail", tool_name)
