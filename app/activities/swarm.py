"""A real Strands Swarm handoff, run entirely inside one Activity (E7.4 T1).

Not inside workflow code: `strands.multiagent.swarm.Swarm.__init__` fires a
hook event via `run_async` unconditionally — the same thread-spawning sync
call `temporalio.contrib.strands`'s README warns "the workflow sandbox
blocks." Swarm's own handoff/timeout bookkeeping also runs on real
`time.time()`, a determinism risk for replay if it ever ran in workflow code.
Both are non-issues inside an Activity, which has no determinism or sandbox
constraints — the cost is that this whole multi-turn, two-agent session is
one coarse retry unit rather than individually durable per model call, unlike
every other tool in this codebase. See docs/DECISIONS.md.

No `TemporalAgent` here either: nothing inside a single Activity needs its
own per-call durability, so these are plain `strands.Agent` instances with a
real `BedrockModel` — the activity itself is what Temporal retries.
"""

from functools import lru_cache

from pydantic import BaseModel
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.multiagent.swarm import Swarm
from temporalio import activity

from app.config import get_settings
from app.temporal_client import bedrock_session

REVIEWER_PROMPT = """You are an accounts-payable fraud reviewer. You are handed
one flagged invoice: a vendor whose payment-risk tier is not "low", combined
with an amount above the standard escalation threshold. That combination is
already worth a second opinion.

Read the invoice details and hand off to the "fraud_specialist" agent with a
concise summary of what makes this worth their attention: the vendor's risk
tier and history, the amount, and the discrepancy. You do not produce the
final verdict yourself — that is the specialist's job."""

SPECIALIST_PROMPT = """You are a fraud specialist reviewing a handoff from an
accounts-payable reviewer. Weigh the vendor's risk tier and history, the
invoice amount, and the discrepancy together. Decide whether this looks like
fraud (a discrepancy that does not fit an honest billing error, e.g. amounts
or patterns inconsistent with the vendor's own history) or looks clear
(concerning risk tier alone, but nothing about this specific invoice adds up
to fraud). Produce your final answer as your structured output — do not hand
off again."""

SWARM_TASK_TEMPLATE = (
    "Invoice {invoice_id} for ${amount_usd:.2f} to {vendor} (risk tier: "
    "{risk_tier}). Discrepancy: {discrepancy_notes}"
)


class FraudSwarmVerdict(BaseModel):
    suspected_fraud: bool
    rationale: str


@lru_cache
def _model() -> BedrockModel:
    # Shares `bedrock_session()` with the main model registry — an unprofiled
    # session resolves through the `[default]` profile, which fails outright
    # when that profile is configured for `aws login`. Note BedrockModel
    # rejects `region_name` and `boto_session` together; the session carries it.
    settings = get_settings()
    return BedrockModel(
        model_id=settings.bedrock_claude_model_id,
        boto_session=bedrock_session(),
    )


@activity.defn
async def investigate_fraud_swarm(
    invoice_id: str, amount_usd: float, vendor: str, risk_tier: str, discrepancy_notes: str
) -> FraudSwarmVerdict:
    """Hand a flagged invoice from a reviewer agent to a fraud specialist
    agent via a real Strands Swarm, and return the specialist's verdict.
    Safe to retry from scratch: nothing outside this activity is mutated."""
    reviewer = Agent(model=_model(), name="reviewer", system_prompt=REVIEWER_PROMPT)
    specialist = Agent(
        model=_model(),
        name="fraud_specialist",
        system_prompt=SPECIALIST_PROMPT,
        structured_output_model=FraudSwarmVerdict,
    )
    swarm = Swarm(nodes=[reviewer, specialist], entry_point=reviewer)

    task = SWARM_TASK_TEMPLATE.format(
        invoice_id=invoice_id,
        amount_usd=amount_usd,
        vendor=vendor,
        risk_tier=risk_tier,
        discrepancy_notes=discrepancy_notes,
    )
    result = await swarm.invoke_async(task)

    specialist_result = result.results["fraud_specialist"].result
    verdict = getattr(specialist_result, "structured_output", None)
    if not isinstance(verdict, FraudSwarmVerdict):
        # The specialist never got a turn (e.g. the reviewer never handed
        # off) — report that plainly rather than fabricating a verdict.
        return FraudSwarmVerdict(
            suspected_fraud=False,
            rationale=f"fraud specialist did not produce a verdict (swarm status: {result.status})",
        )
    return verdict
