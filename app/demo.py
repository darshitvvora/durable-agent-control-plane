"""Demo controls shared by the CLI (`dos demo flood`) and the API
(`POST /api/demo/flood`) — one implementation, not two (E4.1).
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import Job, JobStatus
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
    """
    priority = resolve_priority(tenant_id)

    versions = repo.list_agent_package_versions(agent_id)
    if not versions:
        raise ValueError(
            f"agent {agent_id!r} is not published — run `dos agent publish {agent_id}` first"
        )
    agent_version = max(package.version for package in versions)

    settings = get_settings()
    client = await connect()

    async def submit_one() -> None:
        job_id = f"flood-{tenant_id}-{uuid.uuid4().hex[:8]}"
        repo.put_job(
            Job(
                job_id=job_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                workflow_id=job_id,
                status=JobStatus.QUEUED,
                priority_key=priority.priority_key,
                created_at=datetime.now(UTC).isoformat(),
            )
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
