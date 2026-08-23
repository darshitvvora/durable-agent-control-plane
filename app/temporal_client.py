"""Temporal Cloud client factory and the shared Strands model registry.

StrandsPlugin must be attached to BOTH the client and the worker — the failure
converter that carries Interrupt payloads across the activity boundary is
installed via the client's data converter. Client-only or worker-only and
activity-tool interrupts silently stop working (CLAUDE.md §4).
"""

from collections.abc import Callable

from strands.models import Model
from strands.models.bedrock import BedrockModel
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


def strands_plugin() -> StrandsPlugin:
    return StrandsPlugin(models=model_registry())


async def connect() -> Client:
    settings = get_settings()
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        api_key=settings.temporal_cloud_api_key,
        tls=True,
        plugins=[strands_plugin()],
    )
