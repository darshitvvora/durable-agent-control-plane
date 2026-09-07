"""Starting one agent job — the single implementation shared by the API's
POST /api/jobs and the CLI's `dos agent test`.

Why this exists: `dos agent test` used to call start_workflow directly and
never wrote a Job row, so CLI-started sessions were invisible in the UI's
session list, absent from p95, and missing from queued counts, while
`demo.submit_flood` wrote one correctly. Two callers, two behaviours, one of
them wrong. See docs/DECISIONS.md (2026-09-02).

Every DynamoDB call goes through asyncio.to_thread: boto3 is synchronous and
this runs on the API's event loop.
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


async def start_session(agent_id: str, tenant_id: str, prompt: str) -> str:
    """Start one agent job and return its job id. Raises ValueError for an
    unknown tenant or an unpublished agent — the caller's mistake, not
    something to paper over with a default."""
    priority = await asyncio.to_thread(resolve_priority, tenant_id)

    versions = await asyncio.to_thread(repo.list_agent_package_versions, agent_id)
    if not versions:
        raise ValueError(
            f"agent {agent_id!r} is not published — run `dos agent publish {agent_id}` first"
        )
    agent_version = max(package.version for package in versions)

    settings = get_settings()
    job_id = f"session-{agent_id}-{uuid.uuid4().hex[:8]}"

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

    client = await connect()
    await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version=agent_version,
            prompt=prompt,
        ),
        id=job_id,
        task_queue=settings.task_queue,
        priority=priority,
        search_attributes=tenant_search_attributes(tenant_id),
    )
    return job_id
