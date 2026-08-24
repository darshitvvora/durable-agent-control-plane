"""The payment activity — proof 3's mechanism.

Exactly-once side effects are NOT something Temporal gives you for free. Temporal
guarantees the activity *function* is retried until it reports success; it says
nothing about how many times the HTTP call inside it reached the provider. If the
worker dies after the call succeeds but before Temporal records
ActivityTaskCompleted, the activity runs again from scratch. The only thing that
makes the money move once is a durable, externally-checkable dedupe key.

The key-stability trick that makes this work lives in `app/activities/idempotency.py`,
shared with every other consequential tool.
"""

import httpx
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.activities.idempotency import run_once
from app.config import get_settings

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

    async def perform(idempotency_key: str) -> dict:
        result = await _call_payment_service(request, idempotency_key)
        # `deduplicated` describes how a caller got the result, not the payment
        # itself, so it is never part of the recorded result.
        return result.model_dump(exclude={"deduplicated"})

    recorded, deduplicated = await run_once("payment", perform)
    return PaymentResult(**recorded, deduplicated=deduplicated)
