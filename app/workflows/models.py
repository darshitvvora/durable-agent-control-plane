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


class SessionState(BaseModel):
    """A point-in-time snapshot of a session, for the UI's polled fallback when
    the event stream is unavailable (E4.2 T3). Every field comes from a real
    Temporal call — describe() for status and version, a workflow query for the
    pending approval — never from cached or synthesised state.
    """

    job_id: str
    status: str
    # The session's worker deployment version — proof 2's per-session version badge.
    worker_version: str | None = None
    pending_approval: PendingApproval | None = None
    # External Storage claim-check numbers (E7.2 T3/T4, Preview) — straight off
    # WorkflowExecutionInfo, never computed client-side. history_size_bytes is
    # always present; the external_* pair is 0 unless something in this
    # session's payloads actually crossed the offload threshold.
    history_size_bytes: int | None = None
    external_payload_size_bytes: int | None = None
    external_payload_count: int | None = None


class UIEvent(BaseModel):
    """One item on AgentJobWorkflow's Workflow Stream (E4.1) — published
    directly from workflow code, delivered to SSE subscribers in order by the
    stream's own offset, no timestamp field needed."""

    job_id: str
    kind: str
    payload: dict = {}
