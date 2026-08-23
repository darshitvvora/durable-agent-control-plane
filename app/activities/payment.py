"""The payment activity — proof 3's mechanism.

Exactly-once side effects are NOT something Temporal gives you for free. Temporal
guarantees the activity *function* is retried until it reports success; it says
nothing about how many times the HTTP call inside it reached the provider. If the
worker dies after the call succeeds but before Temporal records
ActivityTaskCompleted, the activity runs again from scratch. The only thing that
makes the money move once is a durable, externally-checkable dedupe key.

Key stability is the whole trick:

    idempotency_key = payment:{workflow_id}:{activity_id}

`activity_id` is assigned when the activity is *scheduled* and stays fixed while
`attempt` increments across retries, so every retry of the same scheduled call
derives the same key and dedupes. A genuinely new tool call gets a new
activity_id, so legitimate second payments still go through. Deriving the key
inside the activity is what makes it survive the crash — a key generated per
invocation would differ on every retry and dedupe nothing.
"""

from datetime import UTC, datetime

import httpx
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.config import get_settings
from app.registry import repository as repo

PAYMENT_TIMEOUT_SECONDS = 10.0


class PaymentRequest(BaseModel):
    invoice_id: str
    amount_usd: float
    payee: str


class PaymentResult(BaseModel):
    confirmation_id: str
    invoice_id: str
    amount_usd: float
    deduplicated: bool = False


def _idempotency_key(info: activity.Info) -> str:
    return f"payment:{info.workflow_id}:{info.activity_id}"


async def _call_payment_service(request: PaymentRequest, idempotency_key: str) -> PaymentResult:
    settings = get_settings()
    confirmation_id = f"conf-{idempotency_key.rsplit(':', 1)[-1]}"
    payload = {
        "confirmation_id": confirmation_id,
        "invoice_id": request.invoice_id,
        "amount_usd": request.amount_usd,
        "payee": request.payee,
    }
    async with httpx.AsyncClient(timeout=PAYMENT_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{settings.mockoon_base_url}/payments",
            json=payload,
            # A real provider (Stripe et al.) dedupes on this header. Our mock
            # deliberately does not — it counts every call that reaches it, so the
            # proof-3 counter measures reality rather than our own bookkeeping.
            headers={"Idempotency-Key": idempotency_key},
        )
    if response.status_code >= 500:
        raise ApplicationError(f"payment service unavailable: {response.status_code}")
    if response.status_code >= 400:
        raise ApplicationError(
            f"payment rejected: {response.status_code} {response.text}",
            type="PaymentDeclined",
            non_retryable=True,
        )
    return PaymentResult(
        confirmation_id=confirmation_id,
        invoice_id=request.invoice_id,
        amount_usd=request.amount_usd,
    )


@activity.defn
async def issue_payment(request: PaymentRequest) -> PaymentResult:
    """Pay an invoice at most once, however many times this activity is retried."""
    info = activity.info()
    key = _idempotency_key(info)
    activity.logger.info("payment attempt %s key=%s", info.attempt, key)

    existing = repo.get_idempotency_record(key)
    if existing is not None and existing.status == "completed" and existing.result:
        # Already paid on an earlier attempt. Return the recorded result without
        # touching the provider — this is the branch proof 3 exercises.
        activity.logger.info("payment already issued, deduplicated: key=%s", key)
        return PaymentResult(**{**existing.result, "deduplicated": True})

    claimed = repo.claim_idempotency_key(key, datetime.now(UTC).isoformat())
    if not claimed and existing is None:
        # Lost a race with a concurrent attempt that has not finished yet.
        raise ApplicationError("payment already in flight for this key")

    result = await _call_payment_service(request, key)
    # `deduplicated` describes how a caller got the result, not the payment itself.
    repo.complete_idempotency_record(key, result.model_dump(exclude={"deduplicated"}))
    activity.logger.info("payment issued: %s", result.confirmation_id)
    return result
