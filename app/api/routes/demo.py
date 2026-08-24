"""Demo control endpoints — only what already exists (flood, fairness).
ramp/kill aren't wrapped: those CLI commands don't exist yet (E6, unbuilt).
"""

from fastapi import APIRouter, HTTPException

from app.demo import submit_flood
from app.registry import repository as repo
from app.registry.models import FairnessSetting

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
