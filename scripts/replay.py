"""Non-determinism guard: replay real histories from Temporal Cloud against
current workflow code. Run with `make replay` after any workflow change.

This is not a unit test — it replays real recorded executions, which is the only
thing that actually catches a determinism break (CLAUDE.md §2, §9).

Scoped to the CURRENT build id on purpose. AgentJobWorkflow is PINNED, so a run
started on build v1 is only ever replayed by v1 code — v2 code will never see it.
Replaying older builds' histories would therefore fail on every intentional
behaviour change while proving nothing about what can actually happen in
production. The corollary is a discipline, not a loophole: **if you change what
the workflow does, bump BUILD_ID.** Editing behaviour while reusing a build id is
what makes old and new code share one version identity, and that is a real bug
this guard will (correctly) refuse to bless.
"""

import asyncio
from collections.abc import AsyncIterator

from temporalio.client import Client, WorkflowHistory
from temporalio.worker import Replayer

from app.config import get_settings
from app.temporal_client import connect, strands_plugin
from app.worker import DEPLOYMENT_NAME
from app.workflows.agent_job import AgentJobWorkflow

MAX_HISTORIES = 20


async def _histories(client: Client, counter: list[int]) -> AsyncIterator[WorkflowHistory]:
    version = f"{DEPLOYMENT_NAME}:{get_settings().build_id}"
    async for wf in client.list_workflows(
        f'WorkflowType = "{AgentJobWorkflow.__name__}" '
        f'AND TemporalWorkerDeploymentVersion = "{version}"',
        page_size=MAX_HISTORIES,
    ):
        handle = client.get_workflow_handle(wf.id, run_id=wf.run_id)
        yield await handle.fetch_history()
        counter[0] += 1
        if counter[0] >= MAX_HISTORIES:
            return


async def main() -> None:
    client = await connect()
    counter = [0]
    version = f"{DEPLOYMENT_NAME}:{get_settings().build_id}"
    print(f"replaying histories for {version}")

    # Reuse the *client's* converter, not a fresh default one. Once External
    # Storage is configured (E7.2 T3), histories contain claim-check
    # references instead of the payloads themselves, and a replayer without
    # the S3 driver fails every such history with
    # "[TMPRL1105] Detected externally stored payload(s) but external storage
    # is not configured" — a config gap that looks like a determinism failure
    # but isn't. Passing an already-composed converter is also safe for the
    # Strands plugin: its hook leaves any non-default converter untouched.
    replayer = Replayer(
        workflows=[AgentJobWorkflow],
        plugins=[strands_plugin()],
        data_converter=client.data_converter,
    )
    results = await replayer.replay_workflows(
        _histories(client, counter),
        raise_on_replay_failure=False,
    )

    if counter[0] == 0:
        print(
            f"no {AgentJobWorkflow.__name__} histories for {version} — "
            "run a job on this build first (e.g. `make verify-agent`), then re-run replay"
        )
        raise SystemExit(1)

    failures = {
        run_id: failure
        for run_id, failure in results.replay_failures.items()
        if failure is not None
    }
    for run_id, failure in failures.items():
        print(f"  FAIL {run_id}: {failure}")

    if failures:
        print(f"{len(failures)}/{counter[0]} histories failed to replay")
        raise SystemExit(1)

    print(f"all {counter[0]} histories replayed cleanly — no non-determinism")


if __name__ == "__main__":
    asyncio.run(main())
