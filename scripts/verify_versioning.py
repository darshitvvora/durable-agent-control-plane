"""Verify E6.1 (proof 2) end to end against real Temporal Cloud.

Two things this proves, both for real:

1. Structured output (T4): invoice-exception v2 (current BUILD_ID) returns a
   validated InvoiceDecision object, not free text.
2. Ramp control (T2) is a real routing lever: setting a ramp and reading it
   back reflects on the real Worker Deployment's routing config.

The in-flight-pinning half of proof 2 (a session started before a deploy stays
on its original build through the deploy) needs a worker actually restarted
mid-session — that is E6.1 T5's live rehearsal, not something a script can
exercise without a second, differently-built worker process running
concurrently. This script proves the two mechanisms that rehearsal depends on.
"""

import asyncio
import json

from app.config import get_settings
from app.registry import repository as repo
from app.registry.manifest import load_manifest, to_package
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

INVOICE_PROMPT = (
    "Invoice INV-8001 for $60 to Globex Retail failed PO matching on quantity "
    "(received 12, ordered 10). Settle it."
)


async def verify_structured_output() -> None:
    manifest, sop = load_manifest("invoice-exception")
    assert manifest.version >= 2, f"expected invoice-exception v2+, got v{manifest.version}"
    assert manifest.output_model == "InvoiceDecision", "v2 manifest lost its output_model"
    repo.put_agent_package(to_package(manifest, sop))

    settings = get_settings()
    client = await connect()
    job_id = "verify-versioning-structured"
    handle = await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id,
            tenant_id="acme",
            agent_id=manifest.id,
            agent_version=manifest.version,
            prompt=INVOICE_PROMPT,
        ),
        id=job_id,
        task_queue=settings.task_queue,
        priority=resolve_priority("acme"),
    )
    outcome = await handle.result()
    print(f"  raw output: {outcome.output}")

    decision = json.loads(outcome.output)
    assert decision["invoice_id"] == "INV-8001", decision
    assert decision["decision"] == "settle", decision
    assert decision["confirmation_id"], "no confirmation_id on a settled invoice"
    print(f"  parsed: decision={decision['decision']} confirmation={decision['confirmation_id']}")
    print("  structured output verified — v2 returns a validated object, not prose")


async def verify_ramp_control() -> None:
    from app.demo import clear_ramp, ramp_status, set_ramp

    # Ramping toward the already-current build is rejected server-side
    # ("requested ramping version ... is already current") — confirmed live,
    # not assumed. A ramp target has to be a real, distinct prior build; v7
    # genuinely exists as an earlier Worker Deployment Version in this namespace.
    ramp_target = "v7"

    before = await ramp_status()
    print(f"  before: current={before.current_version} ramping={before.ramping_version}")
    assert not before.current_version.endswith(f".{ramp_target}"), (
        f"test needs a ramp target that isn't current; current is {before.current_version}"
    )

    await set_ramp(ramp_target, 25.0)
    during = await ramp_status()
    assert during.ramping_version.endswith(f".{ramp_target}"), during
    assert during.ramping_percentage == 25.0, during
    print(f"  ramping 25% -> {during.ramping_version} confirmed on the real Worker Deployment")

    await clear_ramp()
    after = await ramp_status()
    assert after.ramping_version == "", after
    print("  ramp cleared, confirmed")


async def main() -> None:
    print("=== structured output (T4) ===")
    await verify_structured_output()
    print("=== ramp control (T2) ===")
    await verify_ramp_control()
    print("all checks passed — structured output and ramp control are real")


if __name__ == "__main__":
    asyncio.run(main())
