"""E8.1 T5: a flood must not starve the worker's event loop.

Fails if any flood job fails, or if any activity needed more than one attempt.
Attempt > 1 on a trivial DynamoDB read is the starvation signature — the task
timed out in the worker's queue, not in AWS.

Requires `make worker` and Mockoon running. Costs `COUNT` real tier-1 jobs.
"""

import asyncio
import sys

from app.demo import submit_flood
from app.registry import repository as repo
from app.temporal_client import connect

TENANT = "initech"
COUNT = 30


async def main() -> int:
    before = {j.job_id for j in repo.list_jobs_for_tenant(TENANT, limit=200)}
    print(f"flooding {COUNT} jobs for {TENANT}...")
    await submit_flood(TENANT, COUNT)

    client = await connect()
    new_ids = [
        j.job_id
        for j in repo.list_jobs_for_tenant(TENANT, limit=200)
        if j.job_id not in before
    ]
    assert len(new_ids) == COUNT, f"expected {COUNT} new jobs, got {len(new_ids)}"

    failed, retried = [], []
    for job_id in new_ids:
        handle = client.get_workflow_handle(job_id)
        for _ in range(150):
            desc = await handle.describe()
            if desc.status is not None and desc.status.name != "RUNNING":
                break
            await asyncio.sleep(2)
        if desc.status is None or desc.status.name != "COMPLETED":
            failed.append((job_id, desc.status.name if desc.status else "UNKNOWN"))
            continue
        async for event in handle.fetch_history_events():
            if event.WhichOneof("attributes") == "activity_task_started_event_attributes":
                attempt = event.activity_task_started_event_attributes.attempt
                if attempt > 1:
                    retried.append((job_id, attempt))

    print(f"\ncompleted: {COUNT - len(failed)}/{COUNT}")
    print(f"failed:    {len(failed)}")
    print(f"activities needing more than one attempt: {len(retried)}")
    for job_id, detail in (failed + retried)[:10]:
        print(f"  {job_id} {detail}")

    ok = not failed and not retried
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
