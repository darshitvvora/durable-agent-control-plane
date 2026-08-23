from fastapi import APIRouter

from app.registry import repository as repo
from app.registry.metrics import tenant_wait_p95

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/fleet")
def fleet_metrics() -> dict[str, float | None]:
    """Every tenant's current p95 wait, in seconds (proof 1)."""
    return {t.tenant_id: tenant_wait_p95(t.tenant_id) for t in repo.list_tenants()}
