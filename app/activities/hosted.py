"""The hosted lane — invoking an agent that runs on AgentCore Runtime (E7.3).

Everything else in this control plane runs the agent loop *itself*, turn by
turn, with each model call and tool call dispatched as its own Temporal
activity. A hosted agent is the opposite: someone else's loop, in someone
else's container, reached through one request. This module is that request.

The durability consequence is real and is the whole point of the tier-3
distinction — see `docs/MULTI_AGENT.md`. Temporal's unit of recovery here is
the *invocation*, not the turn: a worker crash mid-call re-runs the entire
hosted session from the beginning, and nothing inside it (its tool calls, its
reasoning) is visible to the session terminal, gated by `ApprovalGate`, or
screened by `GuardrailGate`. You get durability *around* the hosted agent, not
*inside* it.

No-ops cleanly if `AGENTCORE_RUNTIME_ENDPOINT` is unset, same convention as
Memory and the Gateway, so a fork without a deployed runtime still runs.
"""

import asyncio
import json
from functools import lru_cache
from typing import Any

import boto3
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.config import get_settings

# runtimeSessionId has a 33-character minimum (confirmed against the boto3
# service model, not guessed). This prefix both satisfies it for any job id
# and names the calling system in the hosted runtime's own logs and traces.
SESSION_PREFIX = "durable-agent-control-plane-job-"


@lru_cache
def _client() -> Any:
    settings = get_settings()
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    return session.client("bedrock-agentcore")


def _session_id(job_id: str) -> str:
    """Stable per job, deliberately not per attempt: a retry is the same
    logical invocation, so it should land on the same hosted session rather
    than opening a new one."""
    return f"{SESSION_PREFIX}{job_id}".ljust(33, "0")[:256]


def _extract(body: bytes, content_type: str) -> str:
    """Pull the answer out of whatever the runtime returned.

    Our own hosted agent replies with the documented JSON shape
    (`{"response": ..., "status": ...}`, runtime-http-protocol-contract), but
    a third-party agent registered by ARN (E7.3 T2) is under no obligation to,
    and the contract also permits SSE. Handle those, and fall back to raw text
    rather than failing a job over a response shape we did not anticipate.
    """
    text = body.decode("utf-8", errors="replace").strip()
    if "text/event-stream" in content_type:
        chunks = [
            line[len("data: "):]
            for line in text.splitlines()
            if line.startswith("data: ")
        ]
        text = "\n".join(chunks).strip() or text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict):
        for key in ("response", "result", "output", "completion"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                return value
    return text


@activity.defn
async def invoke_hosted_agent(runtime_arn: str, job_id: str, prompt: str) -> str:
    """Send one prompt to a hosted AgentCore Runtime agent, return its answer.

    Blocking boto3 wrapped in a thread (same reasoning as the sandbox
    activity): a hosted agent runs a full loop, so this can occupy real
    seconds and must not stall the worker's event loop for every other
    activity sharing the process.
    """
    if not runtime_arn:
        raise ApplicationError(
            "hosted agent has no runtime ARN — set AGENTCORE_RUNTIME_ENDPOINT or "
            "register the agent with a runtime_arn (docs/AWS_SETUP.md, E7.3)",
            type="HostedRuntimeNotConfigured",
            non_retryable=True,
        )

    response = await asyncio.to_thread(
        _client().invoke_agent_runtime,
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=_session_id(job_id),
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({"prompt": prompt}).encode(),
    )

    status = response.get("statusCode")
    if status is not None and status >= 400:
        # The runtime answered, but the hosted agent rejected the request.
        # Retrying a 4xx just repeats it; a 5xx is worth another attempt.
        raise ApplicationError(
            f"hosted agent returned status {status}",
            type="HostedAgentRejected",
            non_retryable=status < 500,
        )

    body = await asyncio.to_thread(response["response"].read)
    return _extract(body, response.get("contentType", ""))
