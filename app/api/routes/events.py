"""SSE bridge onto AgentJobWorkflow's Workflow Stream (Experimental —
temporalio.contrib.workflow_streams; CLAUDE.md §2's preview-feature labelling
rule). No separate event bus: this subscribes straight to the workflow via
WorkflowStreamClient, which is what durability here — a subscriber that drops
and reconnects resumes from an offset instead of just missing events.
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse
from temporalio.common import RawValue
from temporalio.contrib.workflow_streams import WorkflowStreamClient

from app.workflows.agent_job import JOB_EVENTS_TOPIC
from app.workflows.models import UIEvent

router = APIRouter(prefix="/api/jobs", tags=["events"])


@router.get("/{job_id}/events")
async def stream(request: Request, job_id: str) -> EventSourceResponse:
    client = request.app.state.temporal_client
    converter = client.data_converter.payload_converter
    stream = WorkflowStreamClient.create(client, job_id)

    async def event_gen() -> AsyncIterator[dict]:
        async for item in stream.subscribe([JOB_EVENTS_TOPIC], result_type=RawValue):
            event = converter.from_payload(item.data.payload, UIEvent)
            yield {"event": event.kind, "data": json.dumps(event.model_dump())}

    return EventSourceResponse(event_gen())
