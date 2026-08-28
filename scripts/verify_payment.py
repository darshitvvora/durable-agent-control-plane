"""Verify the payment activity is idempotent for real — proof 3's mechanism.

Runs against the real Mockoon payment service and the real DynamoDB table, with
no Temporal involved, so the dedupe logic is tested on its own before it is wired
into a workflow. Requires Mockoon on MOCKOON_BASE_URL.

Checks:
  1. A first call reaches the provider and records a completed key.
  2. A retry of the SAME scheduled activity (same activity_id, higher attempt)
     does NOT reach the provider again — the counter stays at 1.
  3. A genuinely different activity_id DOES reach the provider — legitimate
     second payments are not swallowed.
"""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import httpx
from temporalio.testing import ActivityEnvironment

from app.activities.payment import PaymentRequest, PaymentResult, issue_payment
from app.config import get_settings
from app.registry import repository as repo

# Randomized per run, not fixed: the payment activity now checks the provider
# itself by a natural key derived from (workflow_id, activity_id) (E6.2) — a
# fixed id here would collide with a real leftover record from an earlier
# run and be (correctly) treated as already paid.
WORKFLOW_ID = f"verify-payment-wf-{uuid.uuid4().hex[:8]}"
ACTIVITY_ID = "pay-1"
OTHER_ACTIVITY_ID = "pay-2"


async def _provider_count() -> int:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{settings.mockoon_base_url}/payments")
        response.raise_for_status()
        return len(response.json())


async def _run(activity_id: str, attempt: int) -> PaymentResult:
    env = ActivityEnvironment()
    env.info = replace(env.info, workflow_id=WORKFLOW_ID, activity_id=activity_id, attempt=attempt)
    return await env.run(
        issue_payment,
        PaymentRequest(invoice_id="INV-1001", amount_usd=250.0, payee="Globex"),
    )


def _cleanup() -> None:
    for activity_id in (ACTIVITY_ID, OTHER_ACTIVITY_ID):
        repo.delete_idempotency_record(f"payment:{WORKFLOW_ID}:{activity_id}")


async def main() -> None:
    settings = get_settings()
    print(f"mockoon={settings.mockoon_base_url} table={settings.dynamodb_table_name}")
    _cleanup()

    baseline = await _provider_count()
    print(f"  provider count at start   = {baseline}")

    first = await _run(ACTIVITY_ID, attempt=1)
    after_first = await _provider_count()
    print(f"  after first call          = {after_first} (deduplicated={first.deduplicated})")
    assert after_first == baseline + 1, "first payment should reach the provider"
    assert first.deduplicated is False, "first payment must not be marked deduplicated"

    # The proof-3 branch: same scheduled activity, retried after a crash.
    retry = await _run(ACTIVITY_ID, attempt=2)
    after_retry = await _provider_count()
    print(f"  after retry (same act id) = {after_retry} (deduplicated={retry.deduplicated})")
    assert after_retry == after_first, "RETRY REACHED THE PROVIDER — proof 3 would fail"
    assert retry.deduplicated is True, "retry must be marked deduplicated"
    assert retry.confirmation_id == first.confirmation_id, "retry must return the same confirmation"

    # A different scheduled activity is a different logical payment.
    other = await _run(OTHER_ACTIVITY_ID, attempt=1)
    after_other = await _provider_count()
    print(f"  after different act id    = {after_other} (deduplicated={other.deduplicated})")
    assert after_other == after_retry + 1, "a distinct payment must reach the provider"
    assert other.deduplicated is False

    record = repo.get_idempotency_record(f"payment:{WORKFLOW_ID}:{ACTIVITY_ID}")
    assert record is not None and record.status == "completed"
    assert record.created_at <= datetime.now(UTC).isoformat()
    print("  idempotency record        = completed")

    _cleanup()
    print("all checks passed — payment is idempotent across retries")


if __name__ == "__main__":
    asyncio.run(main())
