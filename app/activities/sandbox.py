"""AgentCore Code Interpreter — a real sandboxed script run from an Activity
(E7.2 T1). Scores a chargeback's risk by generating a synthetic population of
the merchant's prior disputes inside the sandbox and comparing this one
against it.

Not Incident Triage: that agent is proof 1's flood generator and stays
toolless on purpose (see its manifest's comment) — a Code Interpreter round
trip on every one of 200 concurrent jobs risks widening its p95, the exact
metric proof 1 depends on staying small. Dispute Resolution already has a
tool boundary (`fetch_dispute_evidence`/`submit_dispute_response`) and is
never the flood tenant.

`aws.codeinterpreter.v1` is AWS's own shared identifier, not a per-account
resource — nothing to provision beyond IAM permission on the caller's role
(docs/AWS_SETUP.md). Session start/execute/stop are blocking boto3 calls;
unlike the quick calls in memory.py, this one can run for real seconds, so
each call runs in a thread (`asyncio.to_thread`) rather than directly in the
activity's coroutine — a slow sandbox call must not stall this worker
process's event loop for every other activity it happens to be running
concurrently.
"""

import asyncio
import base64
import json
from functools import lru_cache
from typing import Any

import boto3
from pydantic import BaseModel
from temporalio import activity

from app.activities.dispute import DisputeEvidence
from app.activities.idempotency import maybe_kill
from app.config import get_settings

CODE_INTERPRETER_ID = "aws.codeinterpreter.v1"
SESSION_TIMEOUT_SECONDS = 900
# 600 records ≈ 84 KiB of report — measured, not estimated. That is the
# largest payload this system produces, and it rides along in every model-call
# payload of the same turn, which is what makes it the claim-check's real
# target (E7.2 T3). It sits under the SDK's 256 KiB default threshold on
# purpose: the report is fed to the model as a tool result, so inflating it
# past that default would cost ~64K tokens per turn for no analytical gain.
# `PAYLOAD_SIZE_THRESHOLD_BYTES` in app/temporal_client.py is set to match.
SYNTHETIC_HISTORY_SIZE = 600


class DisputeRiskAnalysis(BaseModel):
    risk_score: float
    report: str


@lru_cache
def _client() -> Any:
    settings = get_settings()
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    return session.client("bedrock-agentcore")


_SCRIPT_TEMPLATE = """
import base64, json, random

evidence = json.loads(base64.b64decode("__EVIDENCE_B64__").decode())
random.seed(hash(evidence["dispute_id"]) % (2 ** 31))

history = []
for i in range(__HISTORY_SIZE__):
    history.append({
        "case": f"{evidence['merchant']}-hist-{i:04d}",
        "reason_code": random.choice(
            ["product_not_received", "duplicate_charge", "not_as_described", "fraud"]
        ),
        "amount_usd": round(random.uniform(15, 900), 2),
        "delivery_confirmed": random.random() > 0.4,
        "outcome": random.choice(["accept", "contest"]),
    })

similar = [h for h in history if h["reason_code"] == evidence["reason_code"]]
contest_rate = sum(1 for h in similar if h["outcome"] == "contest") / max(len(similar), 1)

risk_score = round(
    contest_rate * 0.6
    + (0.25 if not evidence["delivery_confirmed"]
       and evidence["reason_code"] == "product_not_received" else 0)
    + min(evidence["prior_disputes"] / 10, 0.15),
    3,
)

lines = [
    f"Risk analysis for {evidence['merchant']} -- dispute reason {evidence['reason_code']}",
    f"Similar historical cases: {len(similar)} of {len(history)}",
    f"Historical contest rate for this reason code: {contest_rate:.2%}",
    f"Computed risk_score: {risk_score}",
    "",
    "Historical case ledger (for audit):",
]
lines += [json.dumps(h) for h in history]

print(json.dumps({"risk_score": risk_score, "report": chr(10).join(lines)}))
"""


def _script(evidence: DisputeEvidence) -> str:
    encoded = base64.b64encode(json.dumps(evidence.model_dump()).encode()).decode()
    return (
        _SCRIPT_TEMPLATE.replace("__EVIDENCE_B64__", encoded)
        .replace("__HISTORY_SIZE__", str(SYNTHETIC_HISTORY_SIZE))
    )


def _extract_text(response: dict) -> str:
    return "".join(
        item["text"]
        for event in response["stream"]
        if "result" in event
        for item in event["result"].get("content", [])
        if item.get("type") == "text"
    )


@activity.defn
async def analyze_dispute_risk(evidence: DisputeEvidence) -> DisputeRiskAnalysis:
    """Score a dispute against a synthetic population of the merchant's prior
    cases, computed inside a real AgentCore Code Interpreter sandbox.
    Read-only and safe to retry — nothing outside the sandbox session is
    ever mutated, so a retried attempt just starts a fresh session."""
    client = _client()
    session = await asyncio.to_thread(
        client.start_code_interpreter_session,
        codeInterpreterIdentifier=CODE_INTERPRETER_ID,
        name="dispute-risk-analysis",
        sessionTimeoutSeconds=SESSION_TIMEOUT_SECONDS,
    )
    session_id = session["sessionId"]

    # E7.2 T2: the same kill/resume mechanism proof 3 uses (E6.2, idempotency.py),
    # applied at this activity's own external-call boundary. The session above
    # already exists in AWS; killing the worker here and resuming on a fresh
    # one must not touch any other job's in-flight sandbox session on this
    # same worker process.
    maybe_kill(f"sandbox session={session_id}")

    try:
        response = await asyncio.to_thread(
            client.invoke_code_interpreter,
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
            name="executeCode",
            arguments={"language": "python", "code": _script(evidence)},
        )
        parsed = json.loads(_extract_text(response))
    finally:
        await asyncio.to_thread(
            client.stop_code_interpreter_session,
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
        )

    return DisputeRiskAnalysis(risk_score=parsed["risk_score"], report=parsed["report"])
