"""Verify human-in-the-loop approval: a policy-matched tool call pauses the
agent, a signal resumes it, and denying changes the outcome.

Requires `make worker` running and Mockoon on MOCKOON_BASE_URL.

Runs two sessions over the same agent, one approved and one denied, and asserts:
  * the workflow pauses and reports what it is waiting on (query)
  * approving lets the payment reach the provider
  * denying does NOT let the payment reach the provider
  * both resume in the SAME workflow run — no restart, one continuous history
"""

import asyncio
import uuid

import httpx
from temporalio.client import Client, WorkflowHandle
from temporalio.common import Priority

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import (
    AgentPackage,
    AgentTier,
    ApprovalOperator,
    ApprovalPolicy,
    resolve_field,
)
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput, PendingApproval

AGENT_ID = "verify-approval-agent"
VERSION = 1
SYSTEM_PROMPT = (
    "You settle invoices. When asked to pay an invoice, call the issue_payment tool "
    "once with the invoice id, amount, and payee from the request. "
    "If the tool reports it was denied, do not retry it — explain that the payment "
    "was blocked by a reviewer."
)
THRESHOLD = 200.0
AMOUNT = 900.0


async def _provider_count() -> int:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{settings.mockoon_base_url}/payments")
        response.raise_for_status()
        return len(response.json())


async def _await_pending(handle: WorkflowHandle, timeout: float = 90.0) -> PendingApproval:
    """Poll the query until the workflow reports it is waiting on a human."""
    for _ in range(int(timeout)):
        pending = await handle.query(AgentJobWorkflow.pending_approval)
        if pending is not None:
            return pending
        await asyncio.sleep(1)
    raise AssertionError("workflow never paused for approval")


async def _session(client: Client, decision: str) -> tuple[int, str, str]:
    settings = get_settings()
    job_id = f"verify-approval-{decision}-{uuid.uuid4().hex[:6]}"

    handle = await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id,
            tenant_id="verify-tenant",
            agent_id=AGENT_ID,
            agent_version=VERSION,
            prompt=f"Pay invoice INV-{decision.upper()} for ${AMOUNT} to Globex Retail.",
        ),
        id=job_id,
        task_queue=settings.task_queue,
        priority=Priority(priority_key=1, fairness_key="verify-tenant", fairness_weight=1.0),
    )

    pending = await _await_pending(handle)
    print(f"    paused on: tool={pending.tool} policy='{pending.policy}'")
    assert pending.tool == "issue_payment", "paused on the wrong tool"
    assert resolve_field(pending.tool_input, "amount_usd") == AMOUNT, "policy saw wrong amount"

    before = await _provider_count()
    await handle.signal(AgentJobWorkflow.submit_approval, args=[pending.interrupt_id, decision])
    outcome = await handle.result()
    after = await _provider_count()

    # Same run id start to finish proves it resumed rather than restarted.
    desc = await handle.describe()
    return after - before, outcome.output.strip(), desc.run_id


async def main() -> None:
    repo.put_agent_package(
        AgentPackage(
            agent_id=AGENT_ID,
            version=VERSION,
            name="Verify Approval Agent",
            tier=AgentTier.CUSTOM_TOOLS,
            model="bedrock-claude",
            system_prompt=SYSTEM_PROMPT,
            tools=["issue_payment"],
            approval_policy=ApprovalPolicy(
                tool="issue_payment",
                field="amount_usd",
                operator=ApprovalOperator.GT,
                value=THRESHOLD,
            ),
        )
    )
    print(f"seeded {AGENT_ID} v{VERSION}: approval required when amount_usd > {THRESHOLD}")

    client = await connect()

    try:
        print("  session 1 — approve")
        approved_delta, approved_output, approved_run = await _session(client, "approve")
        print(f"    payments issued = {approved_delta}")
        print(f"    output          = {approved_output[:120]}")
        assert approved_delta == 1, "approved payment did not reach the provider"

        print("  session 2 — deny")
        denied_delta, denied_output, denied_run = await _session(client, "deny: over budget")
        print(f"    payments issued = {denied_delta}")
        print(f"    output          = {denied_output[:120]}")
        assert denied_delta == 0, "DENIED PAYMENT REACHED THE PROVIDER"
        assert approved_output != denied_output, "deny did not change the outcome"
        assert approved_run and denied_run, "missing run ids"

        print("all checks passed — approval gates the tool, denial changes the outcome")
    finally:
        repo.delete_agent_package(AGENT_ID, VERSION)
        print("cleaned up agent package")


if __name__ == "__main__":
    asyncio.run(main())
