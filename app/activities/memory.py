"""AgentCore Memory — tenant-scoped recall (E7.1 T2).

This repo's agents run inside Temporal Activities via `TemporalAgent`, never
on AgentCore Runtime, so Memory's "automatic" session-id plumbing (Runtime
passes it to the Memory service for you) does not apply here — see
`docs/DECISIONS.md`. These activities call the `bedrock-agentcore`
*data-plane* client directly (`create_event`/`list_events`), keyed by
`tenant_id` as both `actorId` and `sessionId` so every job for a tenant writes
into one recallable stream, not a per-job island with nothing to recall from.

The Memory resource has no `memory-strategies` (no semantic extraction), so
this is raw event storage/recall, not `retrieve_memory_records` (that API is
for strategy-extracted memory, which this resource does not have).

No-ops cleanly if `AGENTCORE_MEMORY_ID` is unset, so a fork without Memory
provisioned still runs — same shape as the empty `MCP_SERVER_CATALOG`.
"""

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import boto3
from temporalio import activity

from app.config import get_settings

RECALL_LIMIT = 5


@lru_cache
def _client() -> Any:
    settings = get_settings()
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    return session.client("bedrock-agentcore")


@activity.defn
async def recall_tenant_memory(tenant_id: str) -> list[str]:
    """The tenant's last few job summaries, oldest first. Empty if Memory
    isn't configured or this tenant has no history yet — never fabricated."""
    settings = get_settings()
    if not settings.agentcore_memory_id:
        return []
    response = _client().list_events(
        memoryId=settings.agentcore_memory_id,
        actorId=tenant_id,
        sessionId=tenant_id,
        includePayloads=True,
        maxResults=RECALL_LIMIT,
    )
    notes: list[str] = []
    for event in response.get("events", []):
        for item in event.get("payload", []):
            text = item.get("conversational", {}).get("content", {}).get("text")
            if text:
                notes.append(text)
    return notes


@activity.defn
async def record_tenant_memory(tenant_id: str, summary: str) -> None:
    """Append one event summarizing a completed job, for future recall by the
    same tenant. Best-effort — see agent_job.py: a memory write failing is
    never a reason to fail the job it is summarizing."""
    settings = get_settings()
    if not settings.agentcore_memory_id:
        return
    _client().create_event(
        memoryId=settings.agentcore_memory_id,
        actorId=tenant_id,
        sessionId=tenant_id,
        eventTimestamp=datetime.now(UTC),
        payload=[{"conversational": {"content": {"text": summary}, "role": "OTHER"}}],
    )
