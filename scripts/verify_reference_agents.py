"""Run every reference agent for real, as its own tenant. Run with `make verify-agents`.

Each agent is published from disk, then executed as a real job on Temporal Cloud
against real Bedrock and the Mockoon-backed tools. Asserts the outcome each
agent is supposed to reach, not just that it returned something:

- incident-triage    (tier 1, no tools)  -> names the suspect deploy, no tool calls
- dispute-resolution (tier 2, two tools) -> reads evidence, then files a decision
- invoice-exception  (tier 2, one tool)  -> settles a below-threshold invoice

Requires `make worker` and Mockoon running.
"""

import asyncio
import uuid

import httpx

from app.config import get_settings
from app.registry import repository as repo
from app.registry.manifest import load_manifest, to_package
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

TRIAGE_PROMPT = (
    "Alert: checkout-api p99 latency crossed 2s at 14:02 UTC, error rate 4%. "
    "Recent deploys: checkout-api v412 at 13:58 UTC, search-api v88 at 11:20 UTC. "
    "Triage it."
)
def _dispute_prompt() -> str:
    # Randomized, not fixed: tenant-scoped Memory (E7.1 T2) now persists across
    # runs, so a fixed dispute id makes the agent correctly recall "I already
    # handled this" on a second run and skip filing — a real memory-recall
    # effect, not a bug, but it breaks this test's fixed-id assumption.
    dispute_id = f"DSP-{uuid.uuid4().hex[:6]}"
    return (
        f"Chargeback {dispute_id} has been filed against us. Work out whether "
        "to accept or contest it, and file the response."
    )


def _invoice_prompt() -> str:
    invoice_id = f"INV-{uuid.uuid4().hex[:6]}"
    return (
        f"Invoice {invoice_id} for $60 to Globex Retail failed PO matching on "
        "quantity (received 12, ordered 10). Settle it."
    )


async def run_agent(client, agent_id: str, tenant: str, prompt: str) -> tuple[str, list[str]]:
    """Publish from disk, run one real job, return (output, scheduled activities)."""
    manifest, sop = load_manifest(agent_id)
    repo.put_agent_package(to_package(manifest, sop))

    settings = get_settings()
    job_id = f"verify-ref-{agent_id}-{uuid.uuid4().hex[:6]}"
    handle = await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id,
            tenant_id=tenant,
            agent_id=manifest.id,
            agent_version=manifest.version,
            prompt=prompt,
        ),
        id=job_id,
        task_queue=settings.task_queue,
        priority=resolve_priority(tenant),
    )

    # Dispute Resolution's evidence amount is randomised by Mockoon (E2.3
    # DECISIONS.md) and gates approval above $500 — auto-approve if it lands
    # there, the same way a reviewer would, rather than hanging forever.
    while True:
        pending = await handle.query(AgentJobWorkflow.pending_approval)
        if pending is None:
            break
        print(f"  auto-approving {pending.tool} (policy: {pending.policy})")
        await handle.signal(
            AgentJobWorkflow.submit_approval, args=[pending.interrupt_id, "approve"]
        )
        await asyncio.sleep(1)

    outcome = await handle.result()

    scheduled = sorted(
        {
            e.activity_task_scheduled_event_attributes.activity_type.name
            async for e in handle.fetch_history_events()
            if e.HasField("activity_task_scheduled_event_attributes")
        }
    )
    return outcome.output, scheduled


async def dispute_response_count() -> int:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.get(f"{settings.mockoon_base_url}/dispute-responses")
    return len(response.json())


async def main() -> None:
    client = await connect()

    # --- incident-triage: tier 1, Initech, no tools ---
    output, scheduled = await run_agent(client, "incident-triage", "initech", TRIAGE_PROMPT)
    print("incident-triage (initech, tier 1)")
    print(f"  activities = {scheduled}")
    print(f"  output     = {output.strip()[:120]!r}")
    assert "v412" in output or "checkout-api" in output, (
        f"triage did not name the suspect deploy: {output[:200]!r}"
    )
    tool_activities = [a for a in scheduled if a in ("issue_payment", "submit_dispute_response")]
    assert not tool_activities, f"tier-1 agent called tools: {tool_activities}"

    # --- dispute-resolution: tier 2, Globex, evidence lookup + filing ---
    before = await dispute_response_count()
    output, scheduled = await run_agent(client, "dispute-resolution", "globex", _dispute_prompt())
    after = await dispute_response_count()
    print("dispute-resolution (globex, tier 2)")
    print(f"  activities = {scheduled}")
    print(f"  filings    = {before} -> {after}")
    print(f"  output     = {output.strip()[:120]!r}")
    assert "fetch_dispute_evidence" in scheduled, "agent decided without reading the evidence"
    assert "submit_dispute_response" in scheduled, "agent never filed a response"
    assert after == before + 1, f"expected exactly one filing, got {after - before}"

    # --- invoice-exception: tier 2, Acme, below-threshold settle ---
    output, scheduled = await run_agent(client, "invoice-exception", "acme", _invoice_prompt())
    print("invoice-exception (acme, tier 2)")
    print(f"  activities = {scheduled}")
    print(f"  output     = {output.strip()[:120]!r}")
    assert "issue_payment" in scheduled, "invoice agent did not settle a below-threshold invoice"

    print("all checks passed — three reference agents run real jobs to real outcomes")


if __name__ == "__main__":
    asyncio.run(main())
