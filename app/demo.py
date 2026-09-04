"""Demo controls shared by the CLI (`dos demo flood`) and the API
(`POST /api/demo/flood`) — one implementation, not two (E4.1).
"""

import asyncio
import uuid
from datetime import UTC, datetime

import httpx

from app.config import get_settings
from app.registry import deployment as deployment_registry
from app.registry import repository as repo
from app.registry.deployment import RoutingStatus
from app.registry.models import Job, JobStatus, KillSwitch
from app.registry.priority import resolve_priority, tenant_search_attributes
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

# Incident Triage is the flood generator (E2.3 T2): a real, published, tier-1
# agent with no tools, so 200 concurrent jobs stay fast and cheap while still
# being genuine agent work rather than a synthetic placeholder.
DEFAULT_FLOOD_AGENT_ID = "incident-triage"
FLOOD_PROMPT = (
    "Alert: checkout-api p99 latency crossed 2s at 14:02 UTC and error rate is "
    "4%. Recent deploys: checkout-api v412 at 13:58 UTC, search-api v88 at "
    "11:20 UTC. Triage it."
)


async def submit_flood(
    tenant_id: str, count: int, agent_id: str = DEFAULT_FLOOD_AGENT_ID
) -> None:
    """Submit `count` jobs for `tenant_id` concurrently, to build real queue backlog.

    Concurrent, not sequential: awaiting each job's full completion before
    starting the next would never build a backlog — start_workflow returns once
    accepted, while actual processing is rate-limited by worker capacity and
    (with fairness on) round-robin dispatch. Raises ValueError for an unknown
    tenant (same as resolve_priority) or an unpublished agent.

    Every DynamoDB call here goes through `asyncio.to_thread`, because boto3 is
    synchronous and this runs on the API's event loop. Without it a 50-job
    flood performs 50 blocking writes inline and freezes the whole API for the
    duration — the fairness toggle and the flood button itself stop responding
    for minutes, which is a stage-visible failure, not just slowness. See
    docs/DECISIONS.md (2026-09-02); the worker has the same class of problem in
    its activities, tracked separately as E8.1 T5.
    """
    priority = await asyncio.to_thread(resolve_priority, tenant_id)

    versions = await asyncio.to_thread(repo.list_agent_package_versions, agent_id)
    if not versions:
        raise ValueError(
            f"agent {agent_id!r} is not published — run `dos agent publish {agent_id}` first"
        )
    agent_version = max(package.version for package in versions)

    settings = get_settings()
    client = await connect()

    async def submit_one() -> None:
        job_id = f"flood-{tenant_id}-{uuid.uuid4().hex[:8]}"
        await asyncio.to_thread(
            repo.put_job,
            Job(
                job_id=job_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                workflow_id=job_id,
                status=JobStatus.QUEUED,
                priority_key=priority.priority_key,
                created_at=datetime.now(UTC).isoformat(),
            ),
        )
        await client.start_workflow(
            AgentJobWorkflow.run,
            AgentJobInput(
                job_id=job_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                agent_version=agent_version,
                prompt=FLOOD_PROMPT,
            ),
            id=job_id,
            task_queue=settings.task_queue,
            priority=priority,
            search_attributes=tenant_search_attributes(tenant_id),
        )

    await asyncio.gather(*(submit_one() for _ in range(count)))


async def ramp_status() -> RoutingStatus:
    client = await connect()
    return await deployment_registry.routing_status(client)


async def set_ramp(build_id: str, percentage: float) -> None:
    """Route `percentage`% of NEW workflow starts to `build_id` (proof 2, E6.1).
    In-flight PINNED sessions on other versions keep running unaffected —
    that's the whole point of the proof."""
    client = await connect()
    if percentage >= 100.0:
        # >=100% is "make it current", not "ramp toward it" — set-current is
        # the correct RPC for that, not a 100% ramp (see docs/DECISIONS.md).
        await deployment_registry.set_current_version(client, build_id)
        await deployment_registry.clear_ramp(client)
    else:
        await deployment_registry.set_ramp(client, build_id, percentage)


async def arm_kill_switch() -> KillSwitch:
    """Arm the next consequential-tool call to crash the worker right after
    its external side effect succeeds (E6.2, proof 3). Fires inside
    `app.activities.idempotency.run_once`, wherever a job hits it next.

    boto3 is synchronous, so the put_item goes to a thread — same defect
    class as the rest of E8.1 T5, just off the flood hot path (docs/DECISIONS.md).
    """
    setting = KillSwitch(armed=True)
    await asyncio.to_thread(repo.put_kill_switch, setting)
    return setting


async def payment_count() -> int:
    """How many payment calls actually reached the mocked provider — proof 3's
    visible counter. Mockoon's `payments` bucket counts every call with no
    dedupe of its own, so this measures reality, not our own bookkeeping."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{settings.mockoon_base_url}/payments")
    response.raise_for_status()
    return len(response.json())


async def clear_ramp() -> None:
    client = await connect()
    await deployment_registry.clear_ramp(client)
