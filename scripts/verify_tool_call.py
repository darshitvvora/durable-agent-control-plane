"""Verify a manifest-declared tool is called as a Temporal activity from inside
the agent loop, and that the payment actually reached the provider.

Requires `make worker` running and Mockoon on MOCKOON_BASE_URL.
"""

import asyncio
import uuid

import httpx
from temporalio.common import Priority

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import AgentPackage, AgentTier
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

AGENT_ID = "verify-payment-agent"
VERSION = 1
SYSTEM_PROMPT = (
    "You settle invoices. When asked to pay an invoice, call the issue_payment tool "
    "exactly once with the invoice id, amount, and payee from the request. "
    "After it returns, reply with just the confirmation id."
)
INVOICE_ID = "INV-2002"
AMOUNT = 425.5


async def _provider_count() -> int:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{settings.mockoon_base_url}/payments")
        response.raise_for_status()
        return len(response.json())


async def main() -> None:
    settings = get_settings()
    job_id = f"verify-tool-{uuid.uuid4().hex[:8]}"

    repo.put_agent_package(
        AgentPackage(
            agent_id=AGENT_ID,
            version=VERSION,
            name="Verify Payment Agent",
            tier=AgentTier.CUSTOM_TOOLS,
            model="bedrock-claude",
            system_prompt=SYSTEM_PROMPT,
            tools=["issue_payment"],
        )
    )
    print(f"seeded {AGENT_ID} v{VERSION} with tools=[issue_payment]")

    client = await connect()
    before = await _provider_count()
    print(f"  provider count before = {before}")

    try:
        handle = await client.start_workflow(
            AgentJobWorkflow.run,
            AgentJobInput(
                job_id=job_id,
                tenant_id="verify-tenant",
                agent_id=AGENT_ID,
                agent_version=VERSION,
                prompt=f"Pay invoice {INVOICE_ID} for ${AMOUNT} to Globex Retail.",
            ),
            id=job_id,
            task_queue=settings.task_queue,
            priority=Priority(priority_key=1, fairness_key="verify-tenant", fairness_weight=1.0),
        )
        print(f"  started {job_id}, waiting...")
        outcome = await handle.result()

        after = await _provider_count()
        print(f"  provider count after  = {after}")
        print(f"  stop_reason = {outcome.stop_reason}")
        print(f"  output      = {outcome.output.strip()[:160]}")

        assert after == before + 1, f"expected exactly one payment, got {after - before}"

        # Confirm the tool really ran as an activity, not as model chatter.
        activity_types = set()
        async for event in handle.fetch_history_events():
            if event.HasField("activity_task_scheduled_event_attributes"):
                activity_types.add(
                    event.activity_task_scheduled_event_attributes.activity_type.name
                )
        print(f"  activities scheduled  = {sorted(activity_types)}")
        assert "issue_payment" in activity_types, "issue_payment did not run as an activity"

        print("all checks passed — tool ran as an activity, payment issued once")
    finally:
        repo.delete_agent_package(AGENT_ID, VERSION)
        print("cleaned up agent package")


if __name__ == "__main__":
    asyncio.run(main())
