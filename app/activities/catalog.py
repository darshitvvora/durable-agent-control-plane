"""Tool catalog — maps the tool names a manifest declares to activities.

An agent package says `tools: [issue_payment]`; this is where that name becomes a
real activity with the retry policy and timeout appropriate to its class. Adding
a tool means adding an entry here plus naming it in a manifest — never editing
AgentJobWorkflow (CLAUDE.md §2, §11).

Retry policy is per tool *class*, not per tool:

- Consequential (moves money, mutates external state): few attempts, and the
  activity itself must be idempotent. Retrying a non-idempotent side effect is
  how you double-pay.
- Read-only (lookups, enrichment): retry freely, they are safe to repeat.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio.common import RetryPolicy

from app.activities.dispute import fetch_dispute_evidence, submit_dispute_response
from app.activities.payment import issue_payment


@dataclass(frozen=True)
class ToolSpec:
    activity: Any
    options: dict[str, Any] = field(default_factory=dict)
    # Consequential tools are guarded (E7.1 T3) — checked against the payment
    # guardrail before they run. Not derived from `options` (both retry
    # classes share the same dict shape); explicit here instead.
    guarded: bool = False


CONSEQUENTIAL = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}

READ_ONLY = {
    "start_to_close_timeout": timedelta(seconds=15),
    "retry_policy": RetryPolicy(maximum_attempts=5),
}

TOOL_CATALOG: dict[str, ToolSpec] = {
    # issue_payment is NOT guarded: its input (invoice_id, amount_usd, payee)
    # is pure structured data with no free-text field, and Guardrails' denied-
    # topic classifier — a natural-language tool — produced false positives
    # on it regardless of amount (see docs/DECISIONS.md). The threshold check
    # that actually matters for payments is ApprovalGate, which is exact, not
    # a language classifier guessing at a numeric policy.
    "issue_payment": ToolSpec(activity=issue_payment, options=CONSEQUENTIAL),
    "fetch_dispute_evidence": ToolSpec(activity=fetch_dispute_evidence, options=READ_ONLY),
    # submit_dispute_response IS guarded: its `rationale` field is real free
    # text an LLM authored from dispute evidence, a genuine surface for
    # injected bypass-approval language to appear on. Confirmed discriminates
    # correctly: a normal rationale passes, an injected one blocks.
    "submit_dispute_response": ToolSpec(
        activity=submit_dispute_response, options=CONSEQUENTIAL, guarded=True
    ),
}

GUARDED_TOOLS: frozenset[str] = frozenset(
    name for name, spec in TOOL_CATALOG.items() if spec.guarded
)


def all_activities() -> list[Any]:
    """Every tool activity, for Worker(activities=...) registration."""
    return [spec.activity for spec in TOOL_CATALOG.values()]
