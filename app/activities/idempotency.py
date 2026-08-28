"""Run a side effect at most once, however many times its activity is retried.

Extracted from the payment activity so every consequential tool shares one
implementation of proof 3's mechanism rather than each re-deriving it. The
subtlety worth preserving is in `key()`: `activity_id` is assigned when the
activity is *scheduled* and stays fixed while `attempt` increments across
retries, so every retry of one scheduled call derives the same key and dedupes,
while a genuinely new tool call gets a new activity_id and goes through.
Deriving the key inside the activity is what makes it survive a worker crash —
a key generated per invocation would differ on every retry and dedupe nothing.
"""

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.registry import repository as repo
from app.registry.models import KillSwitch


def key(prefix: str) -> str:
    info = activity.info()
    return f"{prefix}:{info.workflow_id}:{info.activity_id}"


async def run_once(
    prefix: str, perform: Callable[[str], Awaitable[dict]]
) -> tuple[dict, bool]:
    """Perform the side effect once. Returns (result, deduplicated).

    `perform` receives the idempotency key so it can forward it to the provider
    as a request header. `deduplicated=True` means an earlier attempt already
    did the work and the recorded result is being replayed — the branch proof 3
    exercises.
    """
    idempotency_key = key(prefix)
    activity.logger.info(
        "%s attempt %s key=%s", prefix, activity.info().attempt, idempotency_key
    )

    existing = repo.get_idempotency_record(idempotency_key)
    if existing is not None and existing.status == "completed" and existing.result:
        activity.logger.info("%s already done, deduplicated: key=%s", prefix, idempotency_key)
        return existing.result, True

    claimed = repo.claim_idempotency_key(idempotency_key, datetime.now(UTC).isoformat())
    if not claimed and existing is None:
        # Lost a race with a concurrent attempt that has not finished yet.
        raise ApplicationError(f"{prefix} already in flight for this key")

    result = await perform(idempotency_key)

    # E6.2, proof 3: if armed, crash *here* — the external call above already
    # succeeded, but completion below is not yet durably recorded. Disarm
    # first so the restarted worker's retry of this same key doesn't loop.
    if repo.get_kill_switch().armed:
        repo.put_kill_switch(KillSwitch(armed=False))
        activity.logger.warning(
            "%s: kill switch armed, exiting now, key=%s", prefix, idempotency_key
        )
        os._exit(1)

    repo.complete_idempotency_record(idempotency_key, result)
    activity.logger.info("%s completed: key=%s", prefix, idempotency_key)
    return result, False
