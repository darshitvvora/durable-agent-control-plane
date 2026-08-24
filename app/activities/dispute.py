"""Chargeback dispute tools, for the Dispute Resolution reference agent.

Two tools, one per retry class (see catalog.py):

- `fetch_dispute_evidence` is read-only — a lookup, safe to repeat, so it
  retries freely.
- `submit_dispute_response` files a response with the card network. That is
  consequential and externally visible, so it runs through the same
  once-only mechanism as the payment activity. Filing a contest twice is the
  same class of bug as paying twice.
"""

import httpx
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.activities.idempotency import run_once
from app.config import get_settings

DISPUTE_TIMEOUT_SECONDS = 10.0


class DisputeEvidence(BaseModel):
    dispute_id: str
    reason_code: str
    amount_usd: float
    merchant: str
    cardholder_claim: str
    prior_disputes: int
    delivery_confirmed: bool


class DisputeResponseRequest(BaseModel):
    dispute_id: str
    # "accept" concedes the chargeback; "contest" files a rebuttal.
    decision: str
    rationale: str
    # Carried on the filing itself, and what the manifest's approval_policy
    # gates on — a policy field the tool input never contains would silently
    # never match (resolve_field returns None), so the gate would not fire.
    amount_usd: float


class DisputeResponseResult(BaseModel):
    case_id: str
    dispute_id: str
    decision: str
    deduplicated: bool = False


@activity.defn
async def fetch_dispute_evidence(dispute_id: str) -> DisputeEvidence:
    """Look up what is known about a chargeback. Read-only, safe to retry."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=DISPUTE_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{settings.mockoon_base_url}/disputes/{dispute_id}/evidence")
    if response.status_code == 404:
        raise ApplicationError(
            f"no such dispute: {dispute_id}",
            type="DisputeNotFound",
            non_retryable=True,
        )
    if response.status_code >= 400:
        raise ApplicationError(f"dispute lookup failed: {response.status_code}")
    return DisputeEvidence(**response.json())


@activity.defn
async def submit_dispute_response(request: DisputeResponseRequest) -> DisputeResponseResult:
    """File the accept/contest decision at most once, however many retries."""
    if request.decision not in ("accept", "contest"):
        raise ApplicationError(
            f"decision must be 'accept' or 'contest', got {request.decision!r}",
            type="InvalidDecision",
            non_retryable=True,
        )

    async def perform(idempotency_key: str) -> dict:
        settings = get_settings()
        case_id = f"case-{idempotency_key.rsplit(':', 1)[-1]}"
        async with httpx.AsyncClient(timeout=DISPUTE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{settings.mockoon_base_url}/dispute-responses",
                json={
                    "case_id": case_id,
                    "dispute_id": request.dispute_id,
                    "decision": request.decision,
                    "rationale": request.rationale,
                    "amount_usd": request.amount_usd,
                },
                headers={"Idempotency-Key": idempotency_key},
            )
        if response.status_code >= 500:
            raise ApplicationError(f"dispute service unavailable: {response.status_code}")
        if response.status_code >= 400:
            raise ApplicationError(
                f"dispute response rejected: {response.status_code} {response.text}",
                type="DisputeResponseRejected",
                non_retryable=True,
            )
        return DisputeResponseResult(
            case_id=case_id,
            dispute_id=request.dispute_id,
            decision=request.decision,
        ).model_dump(exclude={"deduplicated"})

    recorded, deduplicated = await run_once("dispute", perform)
    return DisputeResponseResult(**recorded, deduplicated=deduplicated)
