"""Temporal Cloud client factory and the shared Strands model registry.

StrandsPlugin must be attached to BOTH the client and the worker — the failure
converter that carries Interrupt payloads across the activity boundary is
installed via the client's data converter. Client-only or worker-only and
activity-tool interrupts silently stop working (CLAUDE.md §4).
"""

from collections.abc import Callable

from mcp.client.streamable_http import streamablehttp_client
from strands.models import Model
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin

from app.config import get_settings


def model_registry() -> dict[str, Callable[[], Model]]:
    """Manifest `model:` names → Bedrock model factories.

    Factories are lazy: called on first use on the worker, outside the sandbox,
    then cached for the worker's lifetime. Credentials come from the ambient IAM
    role (SSO locally, execution role on Lambda) — boto3 resolves them itself.
    """
    settings = get_settings()
    return {
        "bedrock-claude": lambda: BedrockModel(
            model_id=settings.bedrock_claude_model_id,
            region_name=settings.aws_region,
        ),
        "bedrock-nova": lambda: BedrockModel(
            model_id=settings.bedrock_nova_model_id,
            region_name=settings.aws_region,
        ),
    }


def mcp_client_registry() -> dict[str, Callable[[], MCPClient]]:
    """Manifest `mcp_servers:` names → MCP transport factories (E7.1 T1).

    "vendor-directory" here is the same name `TemporalMCPClient` uses
    workflow-side (`app/registry/mcp_servers.py`) — only this factory knows
    the actual Gateway URL. Empty if the Gateway isn't provisioned yet (a
    fresh fork before `docs/AWS_SETUP.md`'s Gateway step), so a manifest that
    declares an MCP server fails clearly with "not registered on this worker"
    rather than silently having no tools.
    """
    settings = get_settings()
    servers: dict[str, Callable[[], MCPClient]] = {}
    if settings.agentcore_gateway_url:
        url = settings.agentcore_gateway_url
        servers["vendor-directory"] = lambda: MCPClient(
            lambda: streamablehttp_client(url)
        )
    return servers


def strands_plugin() -> StrandsPlugin:
    return StrandsPlugin(models=model_registry(), mcp_clients=mcp_client_registry())


async def connect() -> Client:
    settings = get_settings()
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        api_key=settings.temporal_cloud_api_key,
        tls=True,
        plugins=[strands_plugin()],
    )
