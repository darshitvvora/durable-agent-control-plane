"""Fleet state read from Temporal itself, not from our own bookkeeping.

Running counts come from Temporal's visibility store rather than the DynamoDB
job index: the index records when a job was created and started, but nothing
stamps completion, so counting "running" there would only ever climb. Temporal
already knows which executions are open — asking it is both correct and one
fewer thing to keep in sync.

Per-tenant counts need the `TenantId` custom search attribute on the namespace
(see docs/AWS_SETUP.md). Until it exists, `lane_counts` reports running=None
rather than guessing, and the queued count — which comes from the job index and
is always accurate — still renders.
"""

from pydantic import BaseModel
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client
from temporalio.service import RPCError

from app.config import get_settings
from app.registry import repository as repo
from app.registry.metrics import tenant_wait_p95
from app.registry.models import JobStatus

WORKFLOW_TYPE = "AgentJobWorkflow"
TENANT_SEARCH_ATTRIBUTE = "TenantId"


async def worker_count(client: Client) -> int:
    """Workers currently polling the workflow task queue."""
    settings = get_settings()
    response = await client.workflow_service.describe_task_queue(
        DescribeTaskQueueRequest(
            namespace=settings.temporal_namespace,
            task_queue=TaskQueue(name=settings.task_queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        )
    )
    return len(response.pollers)


async def status_counts(client: Client) -> dict[str, int]:
    """Fleet-wide job counts by execution status, straight from visibility."""
    result = await client.count_workflows(
        f'WorkflowType = "{WORKFLOW_TYPE}" GROUP BY ExecutionStatus'
    )
    return {
        # group_values is a one-element sequence for a single GROUP BY column.
        str(group.group_values[0]): group.count
        for group in result.groups
        if group.group_values
    }


async def tenant_running(client: Client, tenant_id: str) -> int | None:
    """Open executions for one tenant, or None if TenantId isn't registered yet."""
    try:
        result = await client.count_workflows(
            f'WorkflowType = "{WORKFLOW_TYPE}" '
            f'AND {TENANT_SEARCH_ATTRIBUTE} = "{tenant_id}" '
            f'AND ExecutionStatus = "Running"'
        )
    except RPCError:
        # The namespace has no TenantId search attribute yet — a setup step, not
        # a runtime error. Reported as unknown rather than as a wrong number.
        return None
    return result.count


def tenant_queued(tenant_id: str) -> int:
    """Jobs submitted but not yet picked up by a worker.

    From the job index, and accurate there: `mark_job_started` flips a row out
    of QUEUED the moment a worker begins it, so a row still marked QUEUED has
    genuinely never started. (The RUNNING rows are the ones that go stale, which
    is why running comes from Temporal instead.)
    """
    return sum(
        1
        for job in repo.list_jobs_for_tenant(tenant_id, limit=500)
        if job.status == JobStatus.QUEUED
    )


class Lane(BaseModel):
    tenant_id: str
    name: str
    tier: str
    priority_key: int
    fairness_weight: float
    # None when the TenantId search attribute is not registered yet — unknown,
    # deliberately not zero.
    running: int | None
    queued: int
    p95_wait_seconds: float | None


async def lanes(client: Client) -> list[Lane]:
    """One row per tenant for the process monitor, busiest lane first."""
    rows = [
        Lane(
            tenant_id=tenant.tenant_id,
            name=tenant.name,
            tier=tenant.tier,
            priority_key=tenant.priority_key,
            fairness_weight=tenant.fairness_weight,
            running=await tenant_running(client, tenant.tenant_id),
            queued=tenant_queued(tenant.tenant_id),
            p95_wait_seconds=tenant_wait_p95(tenant.tenant_id),
        )
        for tenant in repo.list_tenants()
    ]
    # Heaviest lane first — the flooding tenant should be visually obvious.
    rows.sort(key=lambda r: (r.running or 0) + r.queued, reverse=True)
    return rows
