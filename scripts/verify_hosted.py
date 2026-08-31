"""Verify E7.3 — the hosted lane end to end. Run with `make verify-hosted`.

Exercises both halves of the story against the real deployed AgentCore
Runtime:

- **T2** registers a hosted agent by ARN alone (`dos agent register-hosted`'s
  code path), with no package read from disk — the "no repo access" claim.
- **T1** runs a real job on it through `AgentJobWorkflow`, which must take the
  tier-3 branch: `invoke_hosted_agent` scheduled, and *no* model activity,
  because the agent loop happens on the far side of the boundary.

Asserts the hosted agent reached a correct verdict on two vendors with
opposite expected outcomes, so a pass means the lane actually carried a real
answer rather than any string at all.

Requires `make worker` running and `AGENTCORE_RUNTIME_ENDPOINT` set — see
`docs/AWS_SETUP.md` (2026-08-29) for deploying the runtime.
"""

import asyncio
import uuid

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import AgentPackage, AgentTier
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

JOB_TIMEOUT_S = 300
# Two vendors from the hosted agent's own watchlist with opposite verdicts, so
# this can't pass on a canned or empty answer.
CASES = [
    ("Meridian Holdings", "blocked"),
    ("Globex Retail", "clear"),
]


async def _run(client, settings, agent_id: str, version: int, vendor: str) -> tuple[str, list[str]]:
    job_id = f"verify-hosted-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id, tenant_id="acme", agent_id=agent_id, agent_version=version,
            prompt=f"Screen {vendor} for compliance.",
        ),
        id=job_id, task_queue=settings.task_queue, priority=resolve_priority("acme"),
    )
    outcome = await asyncio.wait_for(handle.result(), timeout=JOB_TIMEOUT_S)
    scheduled = sorted(
        {
            e.activity_task_scheduled_event_attributes.activity_type.name
            async for e in handle.fetch_history_events()
            if e.HasField("activity_task_scheduled_event_attributes")
        }
    )
    return outcome.output, scheduled


async def main() -> None:
    settings = get_settings()
    arn = settings.agentcore_runtime_endpoint
    assert arn, (
        "AGENTCORE_RUNTIME_ENDPOINT is unset — deploy the hosted agent per "
        "docs/AWS_SETUP.md (2026-08-29) and put its ARN in .env first."
    )
    print(f"build={settings.build_id}\nruntime={arn}")

    # T2: registration by ARN alone. Deliberately a throwaway id that has no
    # agents/<id>/ directory, so nothing here can fall back to reading disk.
    agent_id = f"verify-hosted-{uuid.uuid4().hex[:6]}"
    repo.put_agent_package(
        AgentPackage(
            agent_id=agent_id,
            version=1,
            name="VendorCheck (registered by ARN)",
            tier=AgentTier.HOSTED,
            model="bedrock-claude",
            system_prompt="Hosted compliance screening, registered by ARN with no package on disk.",
            runtime_arn=arn,
        )
    )
    print(f"registered {agent_id} by ARN alone — no package on disk")

    client = await connect()
    try:
        for vendor, expected in CASES:
            output, scheduled = await _run(client, settings, agent_id, 1, vendor)
            print(f"{vendor} -> expected {expected!r}")
            print(f"  activities = {scheduled}")
            print(f"  output     = {output.strip()[:160]!r}")

            assert "invoke_hosted_agent" in scheduled, (
                f"hosted lane never ran — scheduled {scheduled}"
            )
            # The tier-3 branch must skip the native agent loop entirely. If a
            # model activity shows up here, the workflow built a Strands agent
            # for a hosted package, which is the bug this asserts against.
            model_activities = [a for a in scheduled if a.startswith("invoke_model")]
            assert not model_activities, (
                f"tier-3 job ran the native agent loop too: {model_activities}"
            )
            assert expected in output.lower(), (
                f"expected verdict {expected!r} for {vendor}, got: {output[:200]!r}"
            )

        print("all checks passed — hosted agent registered by ARN, invoked, verdicts correct")
    finally:
        repo.delete_agent_package(agent_id, 1)
        print(f"cleaned up {agent_id}")


if __name__ == "__main__":
    asyncio.run(main())
