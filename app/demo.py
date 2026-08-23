"""Demo controls shared by the CLI (`dos demo flood`) and the API
(`POST /api/demo/flood`) — one implementation, not two (E4.1).
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import AgentPackage, AgentTier, Job, JobStatus
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

# A trivial, no-tools agent used purely to generate real queue load — seeded in
# code like the verify scripts' throwaway packages, not authored under agents/,
# since nobody hand-edits its SOP.
FLOOD_AGENT_ID = "flood-load-agent"
FLOOD_AGENT_VERSION = 1
FLOOD_SYSTEM_PROMPT = (
    "You are a load-generation agent. Reply with exactly the word OK and nothing else."
)


async def submit_flood(tenant_id: str, count: int) -> None:
    """Submit `count` jobs for `tenant_id` concurrently, to build real queue backlog.

    Concurrent, not sequential: awaiting each job's full completion before
    starting the next would never build a backlog — start_workflow returns once
    accepted, while actual processing is rate-limited by worker capacity and
    (with fairness on) round-robin dispatch. Raises ValueError for an unknown
    tenant, same as resolve_priority().
    """
    priority = resolve_priority(tenant_id)

    repo.put_agent_package(
        AgentPackage(
            agent_id=FLOOD_AGENT_ID,
            version=FLOOD_AGENT_VERSION,
            name="Flood Load Agent",
            tier=AgentTier.NO_CODE,
            model="bedrock-claude",
            system_prompt=FLOOD_SYSTEM_PROMPT,
        )
    )

    settings = get_settings()
    client = await connect()

    async def submit_one() -> None:
        job_id = f"flood-{tenant_id}-{uuid.uuid4().hex[:8]}"
        repo.put_job(
            Job(
                job_id=job_id,
                tenant_id=tenant_id,
                agent_id=FLOOD_AGENT_ID,
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
                agent_id=FLOOD_AGENT_ID,
                agent_version=FLOOD_AGENT_VERSION,
                prompt="Say OK.",
            ),
            id=job_id,
            task_queue=settings.task_queue,
            priority=priority,
        )

    await asyncio.gather(*(submit_one() for _ in range(count)))
