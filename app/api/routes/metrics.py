from fastapi import APIRouter, Request

from app.demo import payment_count, ramp_status
from app.registry import fleet
from app.registry import repository as repo
from app.registry.metrics import tenant_wait_p95

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/fleet")
def fleet_metrics() -> dict[str, float | None]:
    """Every tenant's current p95 wait, in seconds (proof 1)."""
    return {t.tenant_id: tenant_wait_p95(t.tenant_id) for t in repo.list_tenants()}


@router.get("/lanes")
async def lanes(request: Request) -> list[fleet.Lane]:
    """Per-tenant lanes for the process monitor: running, queued, p95, tier."""
    return await fleet.lanes(request.app.state.temporal_client)


@router.get("/status")
async def status(request: Request) -> dict:
    """Status-strip numbers. Only what is really measurable today — sandbox
    count and S3 offload land with E7.2."""
    client = request.app.state.temporal_client
    return {
        "workers": await fleet.worker_count(client),
        "jobs_by_status": await fleet.status_counts(client),
        "fairness_enabled": repo.get_fairness_setting().enabled,
        "ramp": await ramp_status(),
        "payment_count": await payment_count(),
    }
