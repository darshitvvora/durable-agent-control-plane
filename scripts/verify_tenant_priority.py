"""Prove that a tenant's real registry values reach Temporal Cloud as the
workflow's actual Priority, not just that resolve_priority() doesn't error.

Seeds two throwaway tenants with deliberately different priority_key /
fairness_weight, starts one real workflow per tenant via resolve_priority(),
then reads back workflow_execution_info.priority from a real describe() call
and asserts it matches each tenant and differs between the two.

Requires `make worker` running.
"""

import asyncio
import uuid

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import AgentPackage, AgentTier, Tenant, TenantTier
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

AGENT_ID = "verify-priority-agent"
VERSION = 1
SYSTEM_PROMPT = (
    "You are a terse verification agent. Reply with exactly the word OK and nothing else."
)

TENANTS = [
    Tenant(
        tenant_id="verify-tenant-a",
        name="Verify Tenant A",
        tier=TenantTier.PLATINUM,
        priority_key=2,
        fairness_weight=3.0,
        memory_namespace="verify-tenant-a",
        s3_prefix="tenants/verify-tenant-a/",
    ),
    Tenant(
        tenant_id="verify-tenant-b",
        name="Verify Tenant B",
        tier=TenantTier.FREE,
        priority_key=4,
        fairness_weight=1.0,
        memory_namespace="verify-tenant-b",
        s3_prefix="tenants/verify-tenant-b/",
    ),
]


async def _run_for_tenant(client, settings, tenant: Tenant) -> None:
    priority = resolve_priority(tenant.tenant_id)
    job_id = f"verify-priority-{tenant.tenant_id}-{uuid.uuid4().hex[:8]}"

    handle = await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id,
            tenant_id=tenant.tenant_id,
            agent_id=AGENT_ID,
            agent_version=VERSION,
            prompt="Say OK.",
        ),
        id=job_id,
        task_queue=settings.task_queue,
        priority=priority,
    )
    print(f"started {job_id} for {tenant.tenant_id}")

    desc = await handle.describe()
    actual = desc.raw_description.workflow_execution_info.priority
    assert actual.priority_key == tenant.priority_key, (
        f"{tenant.tenant_id}: priority_key {actual.priority_key} != {tenant.priority_key}"
    )
    assert actual.fairness_key == tenant.tenant_id, (
        f"{tenant.tenant_id}: fairness_key {actual.fairness_key!r} != {tenant.tenant_id!r}"
    )
    assert actual.fairness_weight == tenant.fairness_weight, (
        f"{tenant.tenant_id}: fairness_weight {actual.fairness_weight} != {tenant.fairness_weight}"
    )
    print(
        f"  confirmed on Temporal Cloud: priority_key={actual.priority_key} "
        f"fairness_key={actual.fairness_key} fairness_weight={actual.fairness_weight}"
    )

    outcome = await handle.result()
    assert outcome.output.strip(), "model returned empty output"


async def main() -> None:
    settings = get_settings()

    package = AgentPackage(
        agent_id=AGENT_ID,
        version=VERSION,
        name="Verify Priority Agent",
        tier=AgentTier.NO_CODE,
        model="bedrock-claude",
        system_prompt=SYSTEM_PROMPT,
    )
    repo.put_agent_package(package)
    for tenant in TENANTS:
        repo.put_tenant(tenant)
    print(f"seeded agent package {AGENT_ID} v{VERSION} and {len(TENANTS)} tenants")

    client = await connect()
    print(f"connected to {settings.temporal_namespace}")

    try:
        for tenant in TENANTS:
            await _run_for_tenant(client, settings, tenant)

        assert TENANTS[0].priority_key != TENANTS[1].priority_key
        assert TENANTS[0].fairness_weight != TENANTS[1].fairness_weight
        print("all checks passed — two tenants produced two distinct real Priority payloads")
    finally:
        repo.delete_agent_package(AGENT_ID, VERSION)
        for tenant in TENANTS:
            repo.delete_tenant(tenant.tenant_id)
        print("cleaned up agent package and tenants")


if __name__ == "__main__":
    asyncio.run(main())
