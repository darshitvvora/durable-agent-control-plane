"""Verify E7.2 T2 — sandbox isolation: kill the worker mid-flight during one
job's Code Interpreter session, restart it, and confirm both of two
concurrent dispute-resolution jobs resume and complete correctly, each still
tied to its own dispute — a killed sandbox session for one job must not
corrupt, stall, or merge with another job's session running on the same
worker process. Run with `make verify-sandbox-isolation`.

Owns its own worker process, like verify_kill_resume.py. The kill switch is
the same DynamoDB flag proof 3 uses (E6.2); `analyze_dispute_risk` fires it
via the shared `maybe_kill()` helper (app/activities/idempotency.py) right
after its Code Interpreter session starts — the same "crash right after the
external call succeeds" boundary proof 3 exercises for payments. Because the
SOP always calls `analyze_dispute_risk` before `submit_dispute_response`,
whichever job reaches a kill checkpoint first is guaranteed to be there, not
at the filing step.

Requires Mockoon running.
"""

import asyncio
import sys
import uuid

import httpx

from app.config import get_settings
from app.registry import repository as repo
from app.registry.manifest import load_manifest, to_package
from app.registry.models import KillSwitch
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput
from scripts._approval import result_with_auto_approval

WORKER_EXIT_TIMEOUT_S = 120
# Two full concurrent dispute flows (each: memory recall, several LLM turns,
# a real Code Interpreter session, a filing) after a cold worker restart —
# genuinely heavier than the single-job budget verify_kill_resume.py uses.
# This was briefly raised further while chasing intermittent timeouts that
# turned out to be the approval race in `scripts/_approval.py`, not slowness.
JOB_RESULT_TIMEOUT_S = 300


def _dispute_prompt(dispute_id: str) -> str:
    return (
        f"Chargeback {dispute_id} has been filed against us. Work out whether "
        "to accept or contest it, and file the response."
    )


async def _spawn_worker():
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.worker",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )


async def _start_dispute_job(client, settings, agent_version: int, dispute_id: str):
    job_id = f"verify-sandbox-{uuid.uuid4().hex[:8]}"
    return await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id, tenant_id="globex", agent_id="dispute-resolution",
            agent_version=agent_version, prompt=_dispute_prompt(dispute_id),
        ),
        id=job_id, task_queue=settings.task_queue, priority=resolve_priority("globex"),
    )


async def _finish(handle):
    """Wait for the result, auto-approving if Mockoon's randomised evidence
    amount happens to land above the review threshold."""
    return await result_with_auto_approval(handle)


async def _dispute_responses() -> list[dict]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.get(f"{settings.mockoon_base_url}/dispute-responses")
    return response.json()


async def main() -> None:
    settings = get_settings()
    print(f"build={settings.build_id} mockoon={settings.mockoon_base_url}")

    manifest, sop = load_manifest("dispute-resolution")
    repo.put_agent_package(to_package(manifest, sop))

    repo.put_kill_switch(KillSwitch(armed=False))  # clean slate
    baseline = len(await _dispute_responses())

    worker = await _spawn_worker()
    client = await connect()
    try:
        print("arming the kill switch — the first sandbox session to start crashes the worker")
        repo.put_kill_switch(KillSwitch(armed=True))

        dispute_a = f"DSP-{uuid.uuid4().hex[:6]}"
        dispute_b = f"DSP-{uuid.uuid4().hex[:6]}"
        handle_a = await _start_dispute_job(client, settings, manifest.version, dispute_a)
        handle_b = await _start_dispute_job(client, settings, manifest.version, dispute_b)
        print(f"started two concurrent dispute jobs: {dispute_a}, {dispute_b}")

        print(f"waiting up to {WORKER_EXIT_TIMEOUT_S}s for the worker to crash itself...")
        try:
            returncode = await asyncio.wait_for(worker.wait(), timeout=WORKER_EXIT_TIMEOUT_S)
        except TimeoutError:
            raise AssertionError(
                "worker did not exit — kill switch never fired "
                "(did either job reach analyze_dispute_risk?)"
            ) from None
        assert returncode != 0, f"worker exited cleanly (code={returncode}), expected a crash"
        print(f"  worker process exited with code={returncode} — confirmed dead")
        assert not repo.get_kill_switch().armed, "kill switch should disarm itself when it fires"

        print("starting a fresh worker process to resume both sessions...")
        worker = await _spawn_worker()

        print(f"waiting up to {JOB_RESULT_TIMEOUT_S}s for both sessions to finish...")
        outcome_a, outcome_b = await asyncio.wait_for(
            asyncio.gather(_finish(handle_a), _finish(handle_b)), timeout=JOB_RESULT_TIMEOUT_S
        )
        print(f"  job A ({dispute_a}) finished: {outcome_a.output[:100]!r}")
        print(f"  job B ({dispute_b}) finished: {outcome_b.output[:100]!r}")

        # The isolation property that actually matters: a mid-flight crash on
        # a shared worker process must not merge or cross-contaminate two
        # jobs that happened to be running on it, only force a clean retry.
        # Checked against Mockoon's filed records (structured `dispute_id`/
        # `case_id` fields), not the LLM's free-text summary — the SOP only
        # requires restating the returned case id, not the original dispute
        # id, so scanning prose for a substring is not a reliable signal.
        records = await _dispute_responses()
        assert len(records) == baseline + 2, (
            f"expected exactly two new filings (baseline {baseline} -> {baseline + 2}), "
            f"got {len(records)}"
        )
        filed_a = [r for r in records if r["dispute_id"] == dispute_a]
        filed_b = [r for r in records if r["dispute_id"] == dispute_b]
        assert len(filed_a) == 1, f"expected exactly one filing for {dispute_a}, got {len(filed_a)}"
        assert len(filed_b) == 1, f"expected exactly one filing for {dispute_b}, got {len(filed_b)}"
        assert filed_a[0]["case_id"] != filed_b[0]["case_id"], (
            f"both jobs got case_id {filed_a[0]['case_id']!r} — collision, not isolation"
        )
        print("all checks passed — kill mid-sandbox, both jobs resume, no cross-contamination")
    finally:
        worker.terminate()
        await worker.wait()


if __name__ == "__main__":
    asyncio.run(main())
