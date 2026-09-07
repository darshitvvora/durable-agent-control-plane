import asyncio

from fastapi import APIRouter, Request

from app.demo import payment_count, ramp_status
from app.registry import fleet
from app.registry import repository as repo
from app.registry.metrics import tenant_wait_p95

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/fleet")
def fleet_metrics() -> dict[str, float | None]:
    """Every tenant's current p95 wait, in seconds (proof 1).

    Plain `def`: Starlette already runs sync routes in a threadpool, so the
    blocking boto3 calls inside `repo.list_tenants`/`tenant_wait_p95` never
    touch the event loop here — no `asyncio.to_thread` needed.
    """
    return {t.tenant_id: tenant_wait_p95(t.tenant_id) for t in repo.list_tenants()}


@router.get("/lanes")
async def lanes(request: Request) -> list[fleet.Lane]:
    """Per-tenant lanes for the process monitor: running, queued, p95, tier."""
    return await fleet.lanes(request.app.state.temporal_client)


@router.get("/status")
async def status(request: Request) -> dict:
    """Status-strip numbers. Only what is really measurable today — sandbox
    count and S3 offload land with E7.2.

    This is `async def`, so unlike `/fleet` above, the blocking
    `repo.get_fairness_setting()` boto3 call would otherwise run inline on the
    event loop. Every independent piece — the two Temporal RPCs, the
    threaded DynamoDB read, the ramp status, and the payment count — runs
    concurrently, since a measured ~2.55s response with an EMPTY queue was
    entirely this route serializing work that has no dependency on itself
    (docs/DECISIONS.md, E8.1 T5's API-side counterpart).
    """
    client = request.app.state.temporal_client
    workers, jobs_by_status, fairness_enabled, ramp, payments = await asyncio.gather(
        fleet.worker_count(client),
        fleet.status_counts(client),
        asyncio.to_thread(lambda: repo.get_fairness_setting().enabled),
        ramp_status(),
        payment_count(),
    )
    return {
        "workers": workers,
        "jobs_by_status": jobs_by_status,
        "fairness_enabled": fairness_enabled,
        "ramp": ramp,
        "payment_count": payments,
    }
