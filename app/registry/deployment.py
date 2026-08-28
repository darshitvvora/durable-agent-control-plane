"""Worker Deployment routing control (E6.1 T2, proof 2) — set-current and the
ramp percentage between two Worker Deployment Versions.

Raw workflow_service RPCs, not the CLI: this is a first-class demo control
(driven live by `dos demo ramp` / the future ramp slider), the same class of
thing as fairness and flood, not a one-time human infra step — unlike the
TenantId search attribute (docs/AWS_SETUP.md), which really is a namespace
setup action. Verified against `temporalio/samples-python`'s
`worker_versioning/app.py` (`set_worker_deployment_current_version`) and
empirically against this repo's own namespace for the ramping/clear-ramp wire
shape (docs/DECISIONS.md) — the CLI's `set-ramping-version`/`--delete` aren't
exposed as SDK methods, so this predates any documented Python shape for them.
"""

from dataclasses import dataclass

from temporalio.api.workflowservice.v1 import (
    DescribeWorkerDeploymentRequest,
    SetWorkerDeploymentCurrentVersionRequest,
    SetWorkerDeploymentRampingVersionRequest,
)
from temporalio.client import Client

from app.config import get_settings

DEPLOYMENT_NAME = "agent-control-plane"


@dataclass
class RoutingStatus:
    current_version: str
    ramping_version: str
    ramping_percentage: float


async def routing_status(client: Client) -> RoutingStatus:
    settings = get_settings()
    response = await client.workflow_service.describe_worker_deployment(
        DescribeWorkerDeploymentRequest(
            namespace=settings.temporal_namespace, deployment_name=DEPLOYMENT_NAME
        )
    )
    rc = response.worker_deployment_info.routing_config
    return RoutingStatus(
        current_version=rc.current_version,
        ramping_version=rc.ramping_version,
        ramping_percentage=rc.ramping_version_percentage,
    )


async def _conflict_token(client: Client, settings) -> bytes:
    response = await client.workflow_service.describe_worker_deployment(
        DescribeWorkerDeploymentRequest(
            namespace=settings.temporal_namespace, deployment_name=DEPLOYMENT_NAME
        )
    )
    return response.conflict_token


async def set_current_version(client: Client, build_id: str) -> None:
    """Make `build_id` current — new workflow starts route here immediately.
    In-flight PINNED sessions on other versions are unaffected (proof 2)."""
    settings = get_settings()
    token = await _conflict_token(client, settings)
    await client.workflow_service.set_worker_deployment_current_version(
        SetWorkerDeploymentCurrentVersionRequest(
            namespace=settings.temporal_namespace,
            deployment_name=DEPLOYMENT_NAME,
            build_id=build_id,
            conflict_token=token,
        )
    )


async def set_ramp(client: Client, build_id: str, percentage: float) -> None:
    """Route `percentage`% of new workflow starts to `build_id`, the rest to
    current. A gradual alternative to flipping current all at once."""
    if not 0.0 <= percentage <= 100.0:
        raise ValueError(f"percentage must be in [0, 100], got {percentage}")
    settings = get_settings()
    token = await _conflict_token(client, settings)
    await client.workflow_service.set_worker_deployment_ramping_version(
        SetWorkerDeploymentRampingVersionRequest(
            namespace=settings.temporal_namespace,
            deployment_name=DEPLOYMENT_NAME,
            build_id=build_id,
            percentage=percentage,
            conflict_token=token,
        )
    )


async def clear_ramp(client: Client) -> None:
    """Remove ramping — confirmed empirically that an empty build_id with
    percentage=0 clears it (docs/DECISIONS.md); the CLI's --delete flag isn't
    exposed as a distinct SDK parameter."""
    settings = get_settings()
    token = await _conflict_token(client, settings)
    await client.workflow_service.set_worker_deployment_ramping_version(
        SetWorkerDeploymentRampingVersionRequest(
            namespace=settings.temporal_namespace,
            deployment_name=DEPLOYMENT_NAME,
            build_id="",
            percentage=0.0,
            conflict_token=token,
        )
    )
