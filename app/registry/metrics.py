"""Per-tenant wait-time metric (E3.2, proof 1) — computed from real Job rows,
never from anything workflow code produces synthetically.

"Wait" is the time between a job being created (submitted) and the workflow
actually starting to run, i.e. how long it sat behind other tenants' work.
"""

import math
from datetime import datetime

from app.registry import repository as repo
from app.registry.models import Job


def _wait_seconds(job: Job) -> float | None:
    if job.started_at is None:
        return None
    created = datetime.fromisoformat(job.created_at)
    started = datetime.fromisoformat(job.started_at)
    return (started - created).total_seconds()


def tenant_wait_p95(tenant_id: str, limit: int = 200) -> float | None:
    """The tenant's p95 wait, in seconds, over its most recent `limit` jobs.

    None if there is no wait data yet (no jobs, or none have started).
    Nearest-rank method — plain Python, the dataset here is never large enough
    to need a real stats library.
    """
    waits = sorted(
        w
        for job in repo.list_jobs_for_tenant(tenant_id, limit=limit)
        if (w := _wait_seconds(job)) is not None
    )
    if not waits:
        return None
    rank = math.ceil(0.95 * len(waits)) - 1
    return waits[max(0, min(rank, len(waits) - 1))]
