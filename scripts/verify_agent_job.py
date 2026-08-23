"""Run one real AgentJobWorkflow end to end. Requires `make worker` running.

Seeds a throwaway agent package in the real registry, starts a real workflow on
Temporal Cloud with a real tenant Priority, waits for the real Bedrock-backed
result, then cleans up.
"""

import asyncio
import uuid

from temporalio.common import Priority

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import AgentPackage, AgentTier
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

AGENT_ID = "verify-echo-agent"
VERSION = 1
SYSTEM_PROMPT = (
    "You are a terse verification agent. Reply with exactly the word OK and nothing else."
)


async def main() -> None:
    settings = get_settings()
    job_id = f"verify-job-{uuid.uuid4().hex[:8]}"

    package = AgentPackage(
        agent_id=AGENT_ID,
        version=VERSION,
        name="Verify Echo Agent",
        tier=AgentTier.NO_CODE,
        model="bedrock-claude",
        system_prompt=SYSTEM_PROMPT,
    )
    repo.put_agent_package(package)
    print(f"seeded agent package {AGENT_ID} v{VERSION}")

    client = await connect()
    print(f"connected to {settings.temporal_namespace}")

    try:
        handle = await client.start_workflow(
            AgentJobWorkflow.run,
            AgentJobInput(
                job_id=job_id,
                tenant_id="verify-tenant",
                agent_id=AGENT_ID,
                agent_version=VERSION,
                prompt="Say OK.",
            ),
            id=job_id,
            task_queue=settings.task_queue,
            # Priority & Fairness is Public Preview and a paid Temporal Cloud
            # add-on; proof 1 depends on it (CLAUDE.md §2).
            priority=Priority(
                priority_key=1,
                fairness_key="verify-tenant",
                fairness_weight=1.0,
            ),
        )
        print(f"started workflow {job_id}, waiting for result...")

        outcome = await handle.result()

        print(f"  stop_reason = {outcome.stop_reason}")
        print(f"  output      = {outcome.output.strip()[:120]}")
        assert outcome.job_id == job_id, "job_id mismatch"
        assert outcome.agent_version == VERSION, "agent_version mismatch"
        assert outcome.output.strip(), "model returned empty output"

        desc = await handle.describe()
        print(f"  worker version = {desc.raw_description.workflow_execution_info.versioning_info}")

        print("all checks passed")
    finally:
        repo.delete_agent_package(AGENT_ID, VERSION)
        print("cleaned up agent package")


if __name__ == "__main__":
    asyncio.run(main())
