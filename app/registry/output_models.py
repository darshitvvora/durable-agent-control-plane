"""Structured output models — resolves a manifest's `output_model` name to a
real Pydantic class, the same shape as TOOL_CATALOG resolves tool names.

Passed to `TemporalAgent(structured_output_model=...)`, which Strands
implements as a synthetic tool the model calls with its structured answer
(temporalio.contrib.strands's own docstring: "routes structured output through
stream() via the structured_output_tool"). Pure type resolution, no I/O — safe
to reference directly from workflow code, unlike TOOL_CATALOG's activities.
"""

from typing import Literal

from pydantic import BaseModel


class InvoiceDecision(BaseModel):
    """Invoice Exception v2's structured decision (E6.1 T4) — the same policy
    as v1, but the answer is a validated object instead of free text, so a
    downstream system can act on it without parsing prose."""

    invoice_id: str
    decision: Literal["settle", "hold", "credit_note"]
    reason: str
    confirmation_id: str | None = None


OUTPUT_MODEL_CATALOG: dict[str, type[BaseModel]] = {
    "InvoiceDecision": InvoiceDecision,
}
