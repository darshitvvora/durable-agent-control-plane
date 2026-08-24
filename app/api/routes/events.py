"""SSE bridge onto AgentJobWorkflow's Workflow Stream (Experimental —
temporalio.contrib.workflow_streams; CLAUDE.md §2's preview-feature labelling
rule). No separate event bus: this subscribes straight to the workflow via
WorkflowStreamClient, which is what makes it durable — a subscriber that drops
and reconnects resumes from an offset instead of just missing events.

Two topics on one stream:

- `job_events`   — UIEvents the workflow itself publishes (E4.1).
- `model_stream` — raw Strands StreamEvents the plugin's model activity
                   publishes (E4.2), translated here into session-terminal
                   shapes so the UI never sees Strands' wire format.
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse
from temporalio.common import RawValue
from temporalio.contrib.workflow_streams import WorkflowStreamClient

from app.workflows.agent_job import JOB_EVENTS_TOPIC, MODEL_STREAM_TOPIC
from app.workflows.models import UIEvent

router = APIRouter(prefix="/api/jobs", tags=["events"])


def translate_stream_event(event: dict) -> dict | None:
    """Strands StreamEvent -> a session-terminal event, or None to drop it.

    Field paths verified against the installed strands types, not guessed:
    ContentBlockDeltaEvent.delta is a ContentBlockDelta (`text`,
    `reasoningContent`, `toolUse`, `citation`); ContentBlockStartEvent.start is
    a ContentBlockStart whose `toolUse` carries `name`/`toolUseId`.

    Most of a StreamEvent is not interesting to a terminal — messageStart /
    messageStop / metadata frames, and the partial tool-input JSON that arrives
    as contentBlockDelta.delta.toolUse. Those are dropped rather than forwarded
    as noise.
    """
    delta = event.get("contentBlockDelta")
    if delta is not None:
        body = delta.get("delta") or {}
        if text := body.get("text"):
            return {"kind": "token", "text": text}
        # Claude's extended thinking arrives here, not in `text`. Surfaced as a
        # distinct kind so the terminal can style it differently instead of
        # interleaving it with the answer.
        reasoning = body.get("reasoningContent") or {}
        if reasoning_text := reasoning.get("text"):
            return {"kind": "reasoning", "text": reasoning_text}
        return None

    start = event.get("contentBlockStart")
    if start is not None:
        tool_use = (start.get("start") or {}).get("toolUse")
        if tool_use:
            return {
                "kind": "tool_call",
                "tool": tool_use.get("name"),
                "tool_use_id": tool_use.get("toolUseId"),
            }
    return None


@router.get("/{job_id}/events")
async def stream(request: Request, job_id: str) -> EventSourceResponse:
    client = request.app.state.temporal_client
    converter = client.data_converter.payload_converter
    stream = WorkflowStreamClient.create(client, job_id)

    async def event_gen() -> AsyncIterator[dict]:
        async for item in stream.subscribe(
            [JOB_EVENTS_TOPIC, MODEL_STREAM_TOPIC], result_type=RawValue
        ):
            if item.topic == JOB_EVENTS_TOPIC:
                event = converter.from_payload(item.data.payload, UIEvent)
                yield {"event": event.kind, "data": json.dumps(event.model_dump())}
                continue

            raw = converter.from_payload(item.data.payload, dict)
            translated = translate_stream_event(raw)
            if translated is None:
                continue
            yield {
                "event": translated["kind"],
                "data": json.dumps({"job_id": job_id, **translated}),
            }

    return EventSourceResponse(event_gen())
