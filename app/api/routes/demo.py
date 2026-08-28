"""Demo control endpoints — flood, fairness, version ramp (E6.1), and armed
kill-worker / payment counter (E6.2, proof 3)."""

from fastapi import APIRouter, HTTPException

from app.demo import arm_kill_switch, clear_ramp, payment_count, ramp_status, set_ramp, submit_flood
from app.registry import repository as repo
from app.registry.deployment import RoutingStatus
from app.registry.models import FairnessSetting, KillSwitch

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/flood")
async def flood(tenant_id: str, count: int) -> dict:
    try:
        await submit_flood(tenant_id, count)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"submitted": count, "tenant_id": tenant_id}


@router.get("/fairness")
def get_fairness() -> FairnessSetting:
    """Current fairness state, so a toggle can render the truth on load."""
    return repo.get_fairness_setting()


@router.post("/fairness")
def fairness(enabled: bool) -> FairnessSetting:
    setting = FairnessSetting(enabled=enabled)
    repo.put_fairness_setting(setting)
    return setting


@router.get("/ramp")
async def get_ramp() -> RoutingStatus:
    """Current/ramping build IDs and percentage, so a ramp slider can render
    the truth on load rather than assuming its last-set value still holds."""
    return await ramp_status()


@router.post("/ramp")
async def ramp(build_id: str, percentage: float) -> RoutingStatus:
    """Route `percentage`% of NEW sessions to `build_id` (proof 2). In-flight
    PINNED sessions on other versions are untouched — that's the proof."""
    try:
        await set_ramp(build_id, percentage)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return await ramp_status()


@router.post("/ramp/clear")
async def clear_ramp_route() -> RoutingStatus:
    await clear_ramp()
    return await ramp_status()


@router.post("/kill-worker")
async def kill_worker() -> KillSwitch:
    """Arm the next consequential-tool call to crash the worker right after
    its side effect succeeds (proof 3). Does not kill anything itself — it
    just arms; the worker process exits itself, on its own next job."""
    return await arm_kill_switch()


@router.get("/payment-count")
async def get_payment_count() -> dict:
    """How many payments actually reached the mocked provider — proof 3's
    visible counter, always on screen per CLAUDE.md's status strip."""
    return {"count": await payment_count()}
