from fastapi import APIRouter

from app.registry import repository as repo
from app.registry.models import AgentPackage

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents() -> list[AgentPackage]:
    """The latest published version of every agent."""
    latest: dict[str, AgentPackage] = {}
    for package in repo.list_agent_packages():
        current = latest.get(package.agent_id)
        if current is None or package.version > current.version:
            latest[package.agent_id] = package
    return list(latest.values())
