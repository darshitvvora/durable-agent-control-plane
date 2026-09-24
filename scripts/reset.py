"""Demo reset — return the whole demo to a clean state between deliveries (E8.1 T4).

Six things, and only these six:

  1. Job rows and their result rows.
  2. AgentCore Memory events per tenant.
  3. Fairness setting, restored to ON.
  4. Kill switch, disarmed.
  5. Any active Worker Deployment ramp, cleared.
  6. Mockoon's `payments` and `dispute-responses` buckets, emptied (via `PUT`
     with body `[]`, not `DELETE` — see `_clear_mockoon_bucket`'s docstring).

Tenants, agent packages, installs and idempotency records are fixtures, not
run state — wiping them would mean re-seeding the registry before every
rehearsal.

Why the memory purge matters: every flood job writes one AgentCore Memory
event for its tenant. After a rehearsal, acme/globex/initech each hold several
near-identical `incident-triage` summaries about `checkout-api`. The demo's
opening beat runs `invoice-exception` for acme and its first visible action is
a memory recall — which would surface load-test noise about an unrelated
incident instead of a genuine prior decision. Purging memory between
deliveries is what keeps that recall meaningful.

Why the job-row purge matters: `tenant_wait_p95` is windowed by *sample count*
(the tenant's most recent 200 jobs), not by time, so the metric is sticky. A
fairness-OFF round's long waits stay in the window and flatten the contrast on
the next fairness-ON round, which is exactly the comparison proof 1 turns on.

Tenants come from the registry, never a hardcoded list (CLAUDE.md §11).

    uv run python -m scripts.reset              # dry run — prints the plan
    uv run python -m scripts.reset --yes        # actually reset
    uv run python -m scripts.reset --tenant initech --yes   # scope job/memory purge to one tenant

Fairness, the kill switch, the ramp, and the Mockoon buckets are global demo
state, not tenant-scoped, so `--tenant` only narrows the job-row and
memory-event purge — those four are always restored on `--yes`.
"""

import argparse
import asyncio
import time
from typing import Any

import boto3
import httpx
from botocore.config import Config as BotoConfig

from app.config import get_settings
from app.demo import clear_ramp
from app.registry import repository as repo
from app.registry.models import FairnessSetting, KillSwitch

PAGE = 100
MOCKOON_BUCKETS = ("payments", "dispute-responses")


def _job_ids(tenant_id: str) -> list[str]:
    """Every job id for a tenant, paged past `list_jobs_for_tenant`'s limit."""
    seen: list[str] = []
    known: set[str] = set()
    while True:
        page = repo.list_jobs_for_tenant(tenant_id, limit=PAGE)
        fresh = [j.job_id for j in page if j.job_id not in known]
        if not fresh:
            return seen
        seen.extend(fresh)
        known.update(fresh)
        if len(page) < PAGE:
            return seen


def _agentcore_client() -> Any:
    """Same profile-explicit session shape as every other AWS client in this
    app (`app/activities/memory.py`, `app/temporal_client.py`). An unprofiled
    session resolves through the `[default]` profile, which fails outright
    when that profile is configured for `aws login` (docs/DECISIONS.md,
    2026-09-02)."""
    settings = get_settings()
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    # Adaptive retries with a high attempt count, because the purge is a burst
    # of DeleteEvent calls and AgentCore rate-limits them. botocore's default
    # (4 attempts, `legacy` mode) gave up mid-purge on a 445-event reset and
    # left the demo half-cleaned; `adaptive` adds client-side rate limiting
    # that backs off instead of hammering. See docs/DECISIONS.md, 2026-09-21.
    return session.client(
        "bedrock-agentcore",
        config=BotoConfig(retries={"max_attempts": 10, "mode": "adaptive"}),
    )


def _memory_events(tenant_id: str) -> list[dict]:
    """Every AgentCore Memory event currently stored for this tenant. Empty if
    Memory isn't configured (same no-op convention as
    `app/activities/memory.py::recall_tenant_memory`) or the tenant has none.
    """
    settings = get_settings()
    if not settings.agentcore_memory_id:
        return []
    client = _agentcore_client()
    events: list[dict] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "memoryId": settings.agentcore_memory_id,
            "actorId": tenant_id,
            "sessionId": tenant_id,
            "maxResults": PAGE,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        response = client.list_events(**kwargs)
        events.extend(response.get("events", []))
        next_token = response.get("nextToken")
        if not next_token:
            return events


# Bounded so a few hundred events don't fire as a few hundred concurrent AWS
# calls. Lowered 12 -> 5 on 2026-09-21: 12 throttled outright on a 445-event
# purge (ThrottledException, "Rate exceeded", after botocore's retries), which
# aborted the reset with the demo half-cleaned. The DeleteEvent rate limit is
# the binding constraint here, not local parallelism, so the extra in-flight
# calls bought nothing once AWS started refusing them.
MEMORY_DELETE_CONCURRENCY = 5


async def _purge_memory(tenant_id: str, semaphore: asyncio.Semaphore) -> int:
    """Delete this tenant's AgentCore Memory events, up to `semaphore`'s
    concurrency limit at a time — shared across all tenants, not per-tenant,
    so the total in-flight `delete_event` call count stays bounded regardless
    of how many tenants are being purged at once.

    Every flood job writes one, so after a rehearsal a tenant's recall is
    dominated by identical load-test summaries. Beat 0's memory moment
    depends on recall surfacing genuine, relevant prior decisions instead.

    One `delete_event` call per event, sequentially, measured at several
    minutes for 432 events across three tenants (docs/DECISIONS.md,
    2026-09-04) — long enough to matter for a reset that runs between live
    deliveries with an audience waiting. Bounded concurrency (not unbounded:
    432 at once risks AWS throttling) turns that into seconds; see
    docs/DECISIONS.md for the measured before/after.
    """
    settings = get_settings()
    events = _memory_events(tenant_id)
    if not events:
        return 0
    client = _agentcore_client()

    async def _delete(event: dict) -> None:
        async with semaphore:
            await asyncio.to_thread(
                client.delete_event,
                memoryId=settings.agentcore_memory_id,
                sessionId=tenant_id,
                eventId=event["eventId"],
                actorId=tenant_id,
            )

    await asyncio.gather(*(_delete(event) for event in events))
    return len(events)


async def _mockoon_bucket_size(client: httpx.AsyncClient, bucket: str) -> int | None:
    """Record count in a Mockoon CRUD bucket, or None if Mockoon isn't
    reachable, or the bucket is unparseable — a reset must not crash just
    because Mockoon is down or a bucket is in the broken state described in
    `_clear_mockoon_bucket`'s docstring."""
    settings = get_settings()
    try:
        response = await client.get(f"{settings.mockoon_base_url}/{bucket}")
        response.raise_for_status()
        return len(response.json())
    except (httpx.HTTPError, ValueError):
        return None


async def _clear_mockoon_bucket(client: httpx.AsyncClient, bucket: str) -> None:
    """Empty a Mockoon CRUD databucket via PUT, not DELETE.

    Verified empirically 2026-09-04 against the live Mockoon instance
    (`@mockoon/cli` 9.8.0): the CRUD route type's auto-generated bucket-level
    `DELETE` sets the databucket's live value to `undefined`
    (`databucketActions`'s `delete` case in `@mockoon/commons-server`), not
    `[]`. A follow-up `GET` on that bucket then returns an empty response
    body, which is not valid JSON and breaks every reader of this endpoint
    (`app/demo.py::payment_count`, this script's own dry-run count). The
    bucket-level `PUT` ("update all") sets the databucket's value to exactly
    the request body, so `PUT .../payments` with body `[]` deterministically
    leaves the bucket parseable as an empty list — no JSON edits to
    `mocks/payment-service.json` needed, since CRUD buckets expose this route
    for free.
    """
    settings = get_settings()
    response = await client.put(f"{settings.mockoon_base_url}/{bucket}", json=[])
    response.raise_for_status()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant", help="Only this tenant's job rows/memory (default: every registered tenant)"
    )
    parser.add_argument("--yes", action="store_true", help="Actually reset; otherwise dry run")
    args = parser.parse_args()

    tenants = [args.tenant] if args.tenant else [t.tenant_id for t in repo.list_tenants()]
    # --tenant narrows the four lines below (job rows/memory events); it does NOT narrow
    # fairness/kill-switch/ramp/Mockoon — those are global settings, not tenant-scoped, and
    # are always restored on --yes regardless of --tenant. Called out explicitly in the
    # printed plan (Finding 1, fix round 1) so an operator narrowing scope mid-demo isn't
    # surprised that an in-progress ramp or fairness toggle got reset anyway.
    GLOBAL = "(global — ignores --tenant)"

    job_plan = {t: _job_ids(t) for t in tenants}
    memory_plan = {t: _memory_events(t) for t in tenants}
    fairness = repo.get_fairness_setting()
    kill_switch = repo.get_kill_switch()

    total_jobs = sum(len(v) for v in job_plan.values())
    total_memory = sum(len(v) for v in memory_plan.values())

    print("per tenant:")
    for tenant_id in tenants:
        print(
            f"  {tenant_id:9} {len(job_plan[tenant_id]):4} job rows"
            f"  {len(memory_plan[tenant_id]):4} memory events"
        )
    print(f"  {'TOTAL':9} {total_jobs:4} job rows  {total_memory:4} memory events")

    print(f"\nfairness enabled:  {fairness.enabled}  -> True   {GLOBAL}")
    print(f"kill switch armed: {kill_switch.armed}  -> False  {GLOBAL}")
    print(f"ramp: -> cleared  {GLOBAL}")

    async with httpx.AsyncClient(timeout=10.0) as http:
        mockoon_sizes = {b: await _mockoon_bucket_size(http, b) for b in MOCKOON_BUCKETS}
        for bucket, size in mockoon_sizes.items():
            shown = "unreachable" if size is None else f"{size} records -> cleared"
            print(f"mockoon /{bucket}: {shown}  {GLOBAL}")

        if not args.yes:
            print("\ndry run — re-run with --yes to reset")
            print("(tenants, agent packages, installs and idempotency records are never touched)")
            return 0

        deleted_jobs = 0
        for job_ids in job_plan.values():
            for job_id in job_ids:
                repo.delete_job(job_id)
                repo.delete_job_result(job_id)
                deleted_jobs += 1

        # Bounded concurrency (Finding 3, fix round 1): one delete_event call per
        # in-flight slot, shared across all tenants, not 432 calls fired at once.
        memory_semaphore = asyncio.Semaphore(MEMORY_DELETE_CONCURRENCY)
        purge_started = time.monotonic()
        deleted_memory = sum(
            await asyncio.gather(*(_purge_memory(t, memory_semaphore) for t in tenants))
        )
        purge_seconds = time.monotonic() - purge_started

        repo.put_fairness_setting(FairnessSetting(enabled=True))
        repo.put_kill_switch(KillSwitch(armed=False))
        await clear_ramp()

        # Finding 2, fix round 1: bucket reachability was snapshotted before the
        # multi-minute job/memory purge above. If Mockoon died in that window, the
        # PUT below must not crash the script after fairness/kill-switch/ramp have
        # already been reset — catch it, note the bucket as not-cleared, and still
        # reach the summary print below.
        mockoon_cleared: dict[str, bool] = {}
        for bucket, size in mockoon_sizes.items():
            if size is None:  # already unreachable at snapshot time — nothing to PUT
                mockoon_cleared[bucket] = False
                continue
            try:
                await _clear_mockoon_bucket(http, bucket)
                mockoon_cleared[bucket] = True
            except httpx.HTTPError as exc:
                print(f"WARNING: mockoon /{bucket} could not be cleared: {exc}")
                mockoon_cleared[bucket] = False

        print(
            f"\ndeleted {deleted_jobs} job rows (+ result rows), "
            f"{deleted_memory} memory events ({purge_seconds:.1f}s)"
        )
        print("fairness restored to ON, kill switch disarmed, ramp cleared")
        for bucket, cleared in mockoon_cleared.items():
            print(f"mockoon /{bucket}: {'cleared' if cleared else 'NOT cleared'}")

        remaining_jobs = sum(len(_job_ids(t)) for t in tenants)
        remaining_memory = sum(len(_memory_events(t)) for t in tenants)
        remaining_mockoon = {b: await _mockoon_bucket_size(http, b) for b in MOCKOON_BUCKETS}
        print(
            f"remaining: {remaining_jobs} job rows, {remaining_memory} memory events, "
            + ", ".join(f"{b}={remaining_mockoon[b]}" for b in MOCKOON_BUCKETS)
        )

        # Finding 4, fix round 1: "unreachable" is not "clean" — the whole point of
        # running reset is to know the demo is clean, so a Mockoon outage must not
        # exit 0. Distinguish emptied (0), still-populated (>0), and unreachable
        # (None), and fail loudly (and non-zero) on the latter two.
        mockoon_ok = True
        for bucket, size in remaining_mockoon.items():
            if size is None:
                print(f"WARNING: mockoon /{bucket} is unreachable — cannot confirm it is clean")
                mockoon_ok = False
            elif size != 0:
                print(f"WARNING: mockoon /{bucket} still has {size} record(s)")
                mockoon_ok = False

        ok = (
            remaining_jobs == 0
            and remaining_memory == 0
            and repo.get_fairness_setting().enabled
            and not repo.get_kill_switch().armed
            and mockoon_ok
        )
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
