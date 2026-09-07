"""Worker entrypoint for local runs against Temporal Cloud.

The deployed lane is a Serverless Worker on Lambda (E9) — same workflows,
activities, and versioning config, different host. Worker Versioning is enabled
here too so local behaviour matches Lambda, where it is mandatory.

Worker Versioning / Serverless Workers are Public Preview (CLAUDE.md §2).
"""

import asyncio

from temporalio.common import VersioningBehavior
from temporalio.worker import Worker, WorkerDeploymentConfig, WorkerDeploymentVersion
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from app.activities.catalog import all_activities
from app.activities.guardrail import apply_guardrail
from app.activities.hosted import invoke_hosted_agent
from app.activities.memory import recall_tenant_memory, record_tenant_memory
from app.activities.registry import mark_job_started, resolve_agent_package
from app.config import get_settings
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow

DEPLOYMENT_NAME = "agent-control-plane"


async def main() -> None:
    settings = get_settings()
    client = await connect()

    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[AgentJobWorkflow],
        activities=[
            resolve_agent_package,
            mark_job_started,
            recall_tenant_memory,
            record_tenant_memory,
            apply_guardrail,
            # Not a tool — the workflow calls it directly for tier-3 agents
            # (E7.3), like memory/guardrail, so it isn't in TOOL_CATALOG.
            invoke_hosted_agent,
            *all_activities(),
        ],
        # No plugins= here on purpose: the worker inherits StrandsPlugin from the
        # client. Passing it again registers the plugin's model/tool activities
        # twice and the worker dies with "More than one activity named
        # invoke_model" (see docs/DECISIONS.md).
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(
                deployment_name=DEPLOYMENT_NAME,
                build_id=settings.build_id,
            ),
            use_worker_versioning=True,
            default_versioning_behavior=VersioningBehavior.PINNED,
        ),
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules("strands", "boto3")
        ),
        # The SDK default is 100. One laptop worker running 100 concurrent
        # activities against Bedrock and AgentCore is not throughput, it is a
        # queue with extra steps — and every one of them competes for the same
        # event loop. Sized for the demo's single-worker lane (E8.1 T5).
        max_concurrent_activities=20,
    )

    print(
        f"worker up: queue={settings.task_queue} "
        f"deployment={DEPLOYMENT_NAME}:{settings.build_id}"
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
