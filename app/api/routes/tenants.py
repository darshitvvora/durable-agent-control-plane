from fastapi import APIRouter, HTTPException

from app.registry import repository as repo
from app.registry.models import Tenant

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


@router.get("")
def list_tenants() -> list[Tenant]:
    return repo.list_tenants()


@router.get("/{tenant_id}")
def get_tenant(tenant_id: str) -> Tenant:
    tenant = repo.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id!r}")
    return tenant


@router.post("/{tenant_id}/install")
def install(tenant_id: str, agent_id: str) -> Tenant:
    try:
        return repo.install_agent(tenant_id, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{tenant_id}/uninstall")
def uninstall(tenant_id: str, agent_id: str) -> Tenant:
    try:
        return repo.uninstall_agent(tenant_id, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
