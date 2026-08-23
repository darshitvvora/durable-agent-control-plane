"""Workflow input/output shapes. Kept separate from the registry models so the
workflow contract can evolve without touching storage.
"""

from pydantic import BaseModel


class AgentJobInput(BaseModel):
    job_id: str
    tenant_id: str
    agent_id: str
    agent_version: int
    prompt: str


class PendingApproval(BaseModel):
    """Surfaced by query so the session pane can render the approval prompt."""

    interrupt_id: str
    tool: str
    tool_input: dict
    policy: str


class JobOutcome(BaseModel):
    job_id: str
    agent_id: str
    agent_version: int
    stop_reason: str
    output: str


class UIEvent(BaseModel):
    """One item on AgentJobWorkflow's Workflow Stream (E4.1) — published
    directly from workflow code, delivered to SSE subscribers in order by the
    stream's own offset, no timestamp field needed."""

    job_id: str
    kind: str
    payload: dict = {}
