"""Verify E6.1 T5 — in-flight PINNED sessions survive a version switch — for
real, against Temporal Cloud, with two real worker processes.

This is the mechanism proof 2 rehearses live on stage: a session pauses for
approval on one Worker Deployment Version; while it waits, `current` moves to
a different version; a brand-new session starts and picks up the new version;
the paused session is then resumed and finishes on the version it started on,
never having moved.

Spawns a second, temporary worker process under its own Build ID (identical
code — this test proves the routing mechanism, not a content difference;
T4's structured-output test already proves a real content difference between
versions). Cleans up the temporary deployment version and restores `current`
to the primary build when done, including on failure.

Requires `make worker` (the primary, current-BUILD_ID worker) and Mockoon
already running.
"""

import asyncio
import os
import sys
import uuid

from temporalio.api.deployment.v1 import WorkerDeploymentVersion
from temporalio.api.workflowservice.v1 import (
    DeleteWorkerDeploymentVersionRequest,
    DescribeWorkerDeploymentRequest,
)

from app.config import get_settings
from app.registry import deployment as deployment_registry
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

APPROVAL_PROMPT = (
    "Invoice INV-PIN-1 for $900 to Globex Retail failed PO matching on price. Settle it."
)
NEW_SESSION_PROMPT = (
    "Invoice INV-PIN-2 for $50 to Globex Retail failed PO matching on quantity. Settle it."
)
POLLER_WAIT_TIMEOUT_S = 60


async def wait_for_poller(client, deployment_name: str, build_id: str) -> None:
    deadline = asyncio.get_event_loop().time() + POLLER_WAIT_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        response = await client.workflow_service.describe_worker_deployment(
            DescribeWorkerDeploymentRequest(
                namespace=client.namespace, deployment_name=deployment_name
            )
        )
        summaries = response.worker_deployment_info.version_summaries
        known = {v.deployment_version.build_id for v in summaries}
        if build_id in known:
            return
        await asyncio.sleep(1)
    raise TimeoutError(f"no poller for build_id={build_id!r} after {POLLER_WAIT_TIMEOUT_S}s")


async def deployment_version_of(client, job_id: str) -> str:
    handle = client.get_workflow_handle(job_id)
    desc = await handle.describe()
    versioning_info = desc.raw_description.workflow_execution_info.versioning_info
    return versioning_info.deployment_version.build_id


async def main() -> None:
    settings = get_settings()
    primary_build_id = settings.build_id
    pinned_build_id = f"{primary_build_id}-pin-{uuid.uuid4().hex[:6]}"
    deployment_name = deployment_registry.DEPLOYMENT_NAME

    print(f"primary build:  {primary_build_id} (already running via `make worker`)")
    print(f"temporary build for this test: {pinned_build_id}")

    env = {**os.environ, "BUILD_ID": pinned_build_id}
    worker_proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.worker", env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )

    client = await connect()
    try:
        print(f"waiting for the temporary worker ({pinned_build_id}) to register...")
        await wait_for_poller(client, deployment_name, pinned_build_id)

        print(f"setting {pinned_build_id} current, so the paused session pins to it")
        await deployment_registry.set_current_version(client, pinned_build_id)

        pinned_job_id = f"verify-pin-paused-{uuid.uuid4().hex[:8]}"
        handle = await client.start_workflow(
            AgentJobWorkflow.run,
            AgentJobInput(
                job_id=pinned_job_id, tenant_id="acme", agent_id="invoice-exception",
                agent_version=2, prompt=APPROVAL_PROMPT,
            ),
            id=pinned_job_id, task_queue=settings.task_queue, priority=resolve_priority("acme"),
        )
        await asyncio.wait_for(
            _wait_for_pending(handle), timeout=60
        )
        started_on = await deployment_version_of(client, pinned_job_id)
        assert started_on == pinned_build_id, f"expected {pinned_build_id}, got {started_on}"
        print(f"  paused mid-reasoning, pinned to {started_on} — free while it waits")

        print(f"'deploying': moving current from {pinned_build_id} back to {primary_build_id}")
        await deployment_registry.set_current_version(client, primary_build_id)

        new_job_id = f"verify-pin-new-{uuid.uuid4().hex[:8]}"
        new_handle = await client.start_workflow(
            AgentJobWorkflow.run,
            AgentJobInput(
                job_id=new_job_id, tenant_id="acme", agent_id="invoice-exception",
                agent_version=2, prompt=NEW_SESSION_PROMPT,
            ),
            id=new_job_id, task_queue=settings.task_queue, priority=resolve_priority("acme"),
        )
        new_outcome = await new_handle.result()
        new_ran_on = await deployment_version_of(client, new_job_id)
        assert new_ran_on == primary_build_id, (
            f"new session should run on {primary_build_id}, got {new_ran_on}"
        )
        print(f"  new session ran end-to-end on {new_ran_on} — the new deployment, not the old one")
        print(f"  new session output: {new_outcome.output[:100]}")

        print("resuming the paused session — reviewer approves")
        pending = await handle.query(AgentJobWorkflow.pending_approval)
        assert pending is not None, "session was no longer paused when we went to approve it"
        await handle.signal(
            AgentJobWorkflow.submit_approval, args=[pending.interrupt_id, "approve"]
        )
        outcome = await handle.result()
        finished_on = await deployment_version_of(client, pinned_job_id)
        assert finished_on == pinned_build_id, (
            f"expected {pinned_build_id}, ran on {finished_on} — PINNED did not hold"
        )
        print(f"  finished on {finished_on} — never moved, despite current changing mid-flight")
        print(f"  finished output: {outcome.output[:100]}")

        print("all checks passed — in-flight PINNED sessions survive a version switch")
    finally:
        print("cleaning up...")
        worker_proc.terminate()
        await worker_proc.wait()
        try:
            await deployment_registry.set_current_version(client, primary_build_id)
        except Exception as e:  # noqa: BLE001 - best-effort restore, don't mask the real failure
            print(f"  (could not restore current to {primary_build_id}: {e})")
        try:
            await client.workflow_service.delete_worker_deployment_version(
                DeleteWorkerDeploymentVersionRequest(
                    namespace=client.namespace,
                    deployment_version=WorkerDeploymentVersion(
                        deployment_name=deployment_name, build_id=pinned_build_id
                    ),
                )
            )
            print(f"  deleted temporary deployment version {pinned_build_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  (could not delete temporary version, leaving it: {e})")


async def _wait_for_pending(handle) -> None:
    while True:
        pending = await handle.query(AgentJobWorkflow.pending_approval)
        if pending is not None:
            return
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
