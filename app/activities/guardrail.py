"""Bedrock Guardrails — a standalone `ApplyGuardrail` check on a proposed
consequential tool call, between the model proposing it and the tool actually
running (E7.1 T3).

Deliberately the standalone `ApplyGuardrail` API on the proposed call, not the
blanket `guardrailConfig` mode wired into every model turn — see
docs/DECISIONS.md for why. Hooked in next to `ApprovalGate`
(`app/workflows/guardrail.py`), on the same `BeforeToolCallEvent`, for tools
`app/activities/catalog.py` marks `guarded=True`.

No-ops cleanly (never blocks) if the Guardrail isn't configured, so a fork
without one provisioned still runs — same shape as the empty MCP catalog and
unconfigured Memory.
"""

from functools import lru_cache
from typing import Any

import boto3
from pydantic import BaseModel
from temporalio import activity

from app.config import get_settings


class GuardrailVerdict(BaseModel):
    blocked: bool
    reason: str | None = None
    # False only when no Guardrail is configured and this returned without
    # calling Bedrock at all. The session terminal keys off this so a fork
    # without a Guardrail shows nothing rather than a "passed" verdict for a
    # check that never ran (CLAUDE.md §7, no fake data).
    evaluated: bool = True


@lru_cache
def _client() -> Any:
    settings = get_settings()
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    return session.client("bedrock-runtime")


@activity.defn
async def apply_guardrail(text: str) -> GuardrailVerdict:
    """Evaluate `text` (a proposed tool call's input, serialized) against the
    payment guardrail."""
    settings = get_settings()
    if not settings.bedrock_guardrail_id or not settings.bedrock_guardrail_version:
        return GuardrailVerdict(blocked=False, evaluated=False)

    response = _client().apply_guardrail(
        guardrailIdentifier=settings.bedrock_guardrail_id,
        guardrailVersion=settings.bedrock_guardrail_version,
        source="INPUT",
        content=[{"text": {"text": text}}],
    )
    if response["action"] == "GUARDRAIL_INTERVENED":
        return GuardrailVerdict(
            blocked=True,
            reason=response.get("actionReason") or "blocked by the payment guardrail",
        )
    return GuardrailVerdict(blocked=False)
