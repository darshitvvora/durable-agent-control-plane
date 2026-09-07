"""Registry activities — all DynamoDB I/O for the workflow lives here."""

import asyncio
from datetime import UTC, datetime

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.registry import repository as repo
from app.registry.models import AgentPackage


@activity.defn
async def resolve_agent_package(agent_id: str, version: int) -> AgentPackage:
    """Load an agent's manifest from the registry.

    A missing package is a permanent failure — retrying will not conjure the row,
    so it is raised non-retryable rather than burning the retry budget.

    boto3 is synchronous, so the call goes to a thread: an async activity that
    blocks freezes the worker's whole event loop, and under a flood that turns a
    millisecond read into a start-to-close timeout (docs/DECISIONS.md, E8.1 T5).
    """
    package = await asyncio.to_thread(repo.get_agent_package, agent_id, version)
    if package is None:
        raise ApplicationError(
            f"agent package not found: {agent_id} v{version}",
            type="AgentPackageNotFound",
            non_retryable=True,
        )
    return package


@activity.defn
async def mark_job_started(job_id: str) -> None:
    """Stamp when a job actually began executing (E3.2's wait-time metric).

    Outside the workflow sandbox, so datetime.now() here is fine — the value it
    records is the real thing being measured, not a workflow decision. Threaded
    for the same reason as resolve_agent_package above; this one doubly so,
    since it is the activity that *defines* the measured wait.
    """
    await asyncio.to_thread(
        repo.mark_job_started, job_id, started_at=datetime.now(UTC).isoformat()
    )
