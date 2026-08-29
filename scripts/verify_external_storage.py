"""Verify E7.2 T3/T4 — the S3 External Storage claim-check (Public Preview).

Runs one real dispute-resolution job. Its `analyze_dispute_risk` tool returns
a ~84 KiB audit ledger (app/activities/sandbox.py), over this project's
configured 64 KiB threshold (`PAYLOAD_SIZE_THRESHOLD_BYTES`), so that payload
— and the model-call payloads carrying it through the turn — should be
offloaded to S3 and replaced in Event History by small references, while
ordinary small payloads stay inline.

Asserts what the UI's storage readout (T4) actually reads: the
`external_payload_count` / `external_payload_size_bytes` / `history_size_bytes`
fields on `WorkflowExecutionInfo`, straight off `describe()`. Also confirms
objects really landed in the bucket, so a passing run can't be explained by
Temporal bookkeeping alone.

Requires `make worker` and Mockoon running, and `S3_BUCKET_NAME` set.
"""

import asyncio
import uuid
from typing import Any

import boto3

from app.config import get_settings
from app.registry import repository as repo
from app.registry.manifest import load_manifest, to_package
from app.registry.priority import resolve_priority
from app.temporal_client import PAYLOAD_SIZE_THRESHOLD_BYTES, connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput
from scripts._approval import result_with_auto_approval

JOB_TIMEOUT_S = 300


def _s3() -> Any:
    settings = get_settings()
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    return session.client("s3")


def _object_count(bucket: str) -> int:
    paginator = _s3().get_paginator("list_objects_v2")
    return sum(page.get("KeyCount", 0) for page in paginator.paginate(Bucket=bucket))


async def main() -> None:
    settings = get_settings()
    assert settings.s3_bucket_name, (
        "S3_BUCKET_NAME is unset — create the bucket per docs/AWS_SETUP.md "
        "(2026-08-29) first; without it payloads stay inline by design."
    )
    print(f"build={settings.build_id} bucket={settings.s3_bucket_name}")

    before = _object_count(settings.s3_bucket_name)
    print(f"objects in bucket at start = {before}")

    manifest, sop = load_manifest("dispute-resolution")
    repo.put_agent_package(to_package(manifest, sop))

    client = await connect()
    dispute_id = f"DSP-{uuid.uuid4().hex[:6]}"
    job_id = f"verify-extstore-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id, tenant_id="globex", agent_id="dispute-resolution",
            agent_version=manifest.version,
            prompt=(
                f"Chargeback {dispute_id} has been filed against us. Work out "
                "whether to accept or contest it, and file the response."
            ),
        ),
        id=job_id, task_queue=settings.task_queue, priority=resolve_priority("globex"),
    )
    print(f"started {job_id} ({dispute_id}) — waiting up to {JOB_TIMEOUT_S}s...")
    outcome = await asyncio.wait_for(result_with_auto_approval(handle), timeout=JOB_TIMEOUT_S)
    print(f"  finished: {outcome.output[:100]!r}")

    # The exact fields the session terminal's storage line renders (T4).
    info = (await handle.describe()).raw_description.workflow_execution_info
    print(
        f"external_payload_count={info.external_payload_count} "
        f"external_payload_size_bytes={info.external_payload_size_bytes} "
        f"history_size_bytes={info.history_size_bytes}"
    )
    assert info.external_payload_count > 0, (
        "no payload was offloaded — is the driver wired on the client the worker "
        "was built from, and did analyze_dispute_risk actually run?"
    )
    assert info.external_payload_size_bytes >= PAYLOAD_SIZE_THRESHOLD_BYTES, (
        f"offloaded {info.external_payload_size_bytes} bytes, expected at least the "
        f"{PAYLOAD_SIZE_THRESHOLD_BYTES}-byte threshold — something smaller than "
        "intended was offloaded"
    )
    # The claim-check's whole point: the bulk is NOT sitting in Event History.
    assert info.history_size_bytes < info.external_payload_size_bytes, (
        f"history ({info.history_size_bytes}) is larger than the offloaded bytes "
        f"({info.external_payload_size_bytes}) — the payloads did not leave history"
    )

    after = _object_count(settings.s3_bucket_name)
    print(f"objects in bucket after = {after}")
    assert after > before, f"bucket object count did not grow ({before} -> {after})"

    print("all checks passed — large payload offloaded to S3, history stayed small")


if __name__ == "__main__":
    asyncio.run(main())
