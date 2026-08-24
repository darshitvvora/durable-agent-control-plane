"""Verify E4.2 token streaming end to end, over the real SSE route.

Starts a real tool-using job, subscribes to its SSE stream, and asserts that
real `token` events arrive and accumulate to non-empty text, that a `tool_call`
event names the tool the model actually chose, and that the polled-state
fallback endpoint answers with a real status and worker version.

Requires `make worker`, `make api`, and Mockoon running.
"""

import asyncio
import json
import uuid

import httpx

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import AgentPackage, AgentTier
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

API_BASE = "http://localhost:8000"
AGENT_ID = "verify-streaming-agent"
VERSION = 1
TENANT = "acme"
SYSTEM_PROMPT = (
    "You settle invoice exceptions. When asked to pay an invoice, call the "
    "issue_payment tool, then briefly confirm what you did."
)
PROMPT = "Invoice INV-STREAM-1 for $40 to Globex Retail is approved. Issue the payment."


async def consume(job_id: str, seen: list[dict]) -> None:
    """Read the real SSE stream until the job finishes."""
    async with httpx.AsyncClient(timeout=None) as http:
        async with http.stream("GET", f"{API_BASE}/api/jobs/{job_id}/events") as response:
            response.raise_for_status()
            kind: str | None = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    kind = line.removeprefix("event: ").strip()
                elif line.startswith("data: "):
                    seen.append({"kind": kind, "data": json.loads(line.removeprefix("data: "))})
                    if kind == "job_finished":
                        return


async def main() -> None:
    settings = get_settings()
    job_id = f"verify-stream-{uuid.uuid4().hex[:8]}"

    repo.put_agent_package(
        AgentPackage(
            agent_id=AGENT_ID,
            version=VERSION,
            name="Verify Streaming Agent",
            tier=AgentTier.CUSTOM_TOOLS,
            model="bedrock-claude",
            system_prompt=SYSTEM_PROMPT,
            tools=["issue_payment"],
        )
    )
    print(f"seeded agent package {AGENT_ID} v{VERSION}")

    client = await connect()
    try:
        handle = await client.start_workflow(
            AgentJobWorkflow.run,
            AgentJobInput(
                job_id=job_id,
                tenant_id=TENANT,
                agent_id=AGENT_ID,
                agent_version=VERSION,
                prompt=PROMPT,
            ),
            id=job_id,
            task_queue=settings.task_queue,
            priority=resolve_priority(TENANT),
        )
        print(f"started {job_id}")

        # Subscribe only after start_workflow returns — the workflow must exist
        # before WorkflowStreamClient can attach (see docs/DECISIONS.md).
        seen: list[dict] = []
        consumer = asyncio.create_task(consume(job_id, seen))

        # The polled fallback has to answer while the job is still running,
        # since that is exactly when the UI would fall back to it. Wait for the
        # first streamed event before asking: a workflow that no worker has
        # picked up yet has no deployment version assigned, so polling any
        # earlier races the first workflow task rather than testing anything.
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.1)
        async with httpx.AsyncClient(timeout=30) as http:
            state = (await http.get(f"{API_BASE}/api/jobs/{job_id}/state")).json()
        print(f"  /state -> status={state['status']} version={state['worker_version']}")

        await handle.result()
        await asyncio.wait_for(consumer, timeout=15)
    finally:
        repo.delete_agent_package(AGENT_ID, VERSION)

    tokens = [e for e in seen if e["kind"] == "token"]
    tool_calls = [e for e in seen if e["kind"] == "tool_call"]
    text = "".join(e["data"]["text"] for e in tokens)

    print(f"  token events     = {len(tokens)}")
    print(f"  tool_call events = {[e['data']['tool'] for e in tool_calls]}")
    print(f"  streamed text    = {text.strip()[:100]!r}")

    assert tokens, "no token events arrived over SSE"
    assert text.strip(), "token events carried no text"
    assert tool_calls, "no tool_call event arrived over SSE"
    assert any(e["data"]["tool"] == "issue_payment" for e in tool_calls), (
        f"expected issue_payment in {[e['data']['tool'] for e in tool_calls]}"
    )
    assert any(e["kind"] == "job_finished" for e in seen), "no job_finished event"
    assert state["status"] == "RUNNING", f"expected RUNNING while in flight, got {state['status']}"
    assert state["worker_version"], "no worker version on the polled state"

    print("all checks passed — tokens and tool calls stream live; polled fallback answers")
    print("cleaned up agent package")


if __name__ == "__main__":
    asyncio.run(main())
