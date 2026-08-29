"""Temporal Cloud client factory and the shared Strands model registry.

StrandsPlugin must be attached to BOTH the client and the worker — the failure
converter that carries Interrupt payloads across the activity boundary is
installed via the client's data converter. Client-only or worker-only and
activity-tool interrupts silently stop working (CLAUDE.md §4).
"""

import asyncio
import dataclasses
from collections.abc import Callable
from typing import Any

import boto3
from botocore.exceptions import ClientError as BotoClientError
from mcp.client.streamable_http import streamablehttp_client
from strands.models import Model
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from temporalio.client import Client
from temporalio.contrib.aws.s3driver import S3StorageDriver, S3StorageDriverClient
from temporalio.contrib.strands import StrandsPlugin
from temporalio.converter import ExternalStorage

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


class _SyncBoto3S3Client(S3StorageDriverClient):
    """Adapts the plain (sync) boto3 S3 client — the same client style used
    everywhere else in this codebase (memory.py, guardrail.py) — to
    `S3StorageDriverClient`'s small async interface via `asyncio.to_thread`.

    Not `aioboto3` (the SDK's own suggested client, per its README): its
    `aiobotocore` dependency pins boto3 to ranges far behind this project's
    `boto3>=1.43.77`, and `uv lock` cannot resolve both at once. This adapter
    needs zero new dependencies and reuses a client library already pinned
    for Bedrock/AgentCore/DynamoDB calls (docs/DECISIONS.md).
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def put_object(self, *, bucket: str, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._client.put_object, Bucket=bucket, Key=key, Body=data)

    async def object_exists(self, *, bucket: str, key: str) -> bool:
        def _check() -> bool:
            try:
                self._client.head_object(Bucket=bucket, Key=key)
                return True
            except BotoClientError as e:
                if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

        return await asyncio.to_thread(_check)

    async def get_object(self, *, bucket: str, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)


# Below the SDK's 256 KiB default. Set from measurement, not taste: this
# system's biggest real payload is the sandbox's dispute-risk audit ledger at
# ~84 KiB (app/activities/sandbox.py), and that ledger rides along in every
# subsequent model-call payload of the same turn — which is what pushed one
# job's Event History to ~700 KiB before offloading. 64 KiB catches the ledger
# and those carrier payloads while leaving ordinary tool results, job inputs,
# and outcomes (all well under 10 KiB) inline. Raising the ledger past 256 KiB
# instead would have worked too, but it is fed to the model as a tool result,
# so it would have added ~64K tokens per turn for no analytical benefit.
PAYLOAD_SIZE_THRESHOLD_BYTES = 64 * 1024


def external_storage() -> ExternalStorage | None:
    """S3 claim-check config for large payloads (E7.2 T3, Preview) — `None`
    (payloads stay inline) if no bucket is provisioned yet, same no-op
    convention as Memory and the Gateway."""
    settings = get_settings()
    if not settings.s3_bucket_name:
        return None
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    driver = S3StorageDriver(
        client=_SyncBoto3S3Client(session.client("s3")),
        bucket=settings.s3_bucket_name,
    )
    return ExternalStorage(
        drivers=[driver], payload_size_threshold=PAYLOAD_SIZE_THRESHOLD_BYTES
    )


async def connect() -> Client:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        api_key=settings.temporal_cloud_api_key,
        tls=True,
        plugins=[strands_plugin()],
    )

    storage = external_storage()
    if storage is None:
        return client

    # Layered on *after* connect rather than passed to Client.connect(), since
    # StrandsPlugin's own data-converter hook (verified by reading
    # `_plugin.py`) only replaces a converter whose `payload_converter_class`
    # is still the SDK default — a converter pre-modified here to add
    # `external_storage` would still look "default" on that one field and get
    # silently discarded, dropping the External Storage config. Rebuilding
    # the client from its own post-plugin config (`active_config=True`) means
    # `dataclasses.replace` starts from the exact converter Strands already
    # produced (pydantic converter + its Interrupt-aware failure converter),
    # so nothing about CLAUDE.md §4's plugin wiring changes.
    config = client.config(active_config=True)
    config["data_converter"] = dataclasses.replace(
        config["data_converter"], external_storage=storage
    )
    return Client(**config)
