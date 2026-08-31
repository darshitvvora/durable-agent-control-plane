"""Domain models for the registry (DynamoDB-backed — see docs/DECISIONS.md for the key schema).

These stay free of storage concerns (no pk/sk) — key construction and item
marshalling live in repository.py, so swapping the backend wouldn't touch these.
"""

from enum import StrEnum

from pydantic import BaseModel


class TenantTier(StrEnum):
    PLATINUM = "platinum"
    STANDARD = "standard"
    FREE = "free"


class Tenant(BaseModel):
    tenant_id: str
    name: str
    tier: TenantTier
    priority_key: int
    fairness_weight: float
    memory_namespace: str
    s3_prefix: str
    installed_agent_ids: list[str] = []


class AgentTier(StrEnum):
    NO_CODE = "1"
    CUSTOM_TOOLS = "2"
    HOSTED = "3"


class ApprovalOperator(StrEnum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NE = "ne"


def resolve_field(tool_input: dict, field: str) -> object | None:
    """Find `field` in a tool's input.

    `activity_as_tool` derives the tool schema from the activity's signature, so
    an activity taking a single Pydantic argument produces nested input like
    `{"request": {"amount_usd": 900}}`. A manifest author should not have to know
    that, so a bare name is searched at any depth. A dotted path
    ("request.amount_usd") is honoured exactly when the author wants to be
    explicit or disambiguate.

    Keys are walked in sorted order so the result is stable across replay
    regardless of how the model serialised the arguments.
    """
    if "." in field:
        current: object = tool_input
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    if field in tool_input:
        return tool_input[field]
    for key in sorted(tool_input):
        nested = tool_input[key]
        if isinstance(nested, dict):
            found = resolve_field(nested, field)
            if found is not None:
                return found
    return None


class ApprovalPolicy(BaseModel):
    """When a tool call needs a human before it runs.

    Declarative on purpose — no expression string, so nothing is `eval`'d inside
    workflow code and `dos agent validate` can check it statically.
    """

    tool: str
    field: str
    operator: ApprovalOperator
    value: float

    def requires_approval(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name != self.tool:
            return False
        actual = resolve_field(tool_input, self.field)
        if not isinstance(actual, int | float) or isinstance(actual, bool):
            return False
        match self.operator:
            case ApprovalOperator.GT:
                return actual > self.value
            case ApprovalOperator.GTE:
                return actual >= self.value
            case ApprovalOperator.LT:
                return actual < self.value
            case ApprovalOperator.LTE:
                return actual <= self.value
            case ApprovalOperator.EQ:
                return actual == self.value
            case ApprovalOperator.NE:
                return actual != self.value


class AgentPackage(BaseModel):
    agent_id: str
    version: int
    name: str
    tier: AgentTier
    model: str  # a key in StrandsPlugin(models=...), not a Bedrock model id
    system_prompt: str  # the package's procedure.sop.md, placeholders unrendered
    parameters: dict[str, str] = {}  # defaults for the SOP's {{placeholders}}
    tools: list[str] = []  # names resolved against app.activities.catalog.TOOL_CATALOG
    mcp_servers: list[str] = []
    approval_policy: ApprovalPolicy | None = None
    guardrail_arn: str | None = None
    output_model: str | None = None
    # Tier 3 (hosted) only — the AgentCore Runtime this agent lives on (E7.3).
    # Unlike the Gateway URL (worker-side only, app/temporal_client.py), this
    # lives in the registry row on purpose: registering a hosted agent by ARN
    # is the *entire* installation path for the hosted lane, with no package
    # on disk and no repo access (E7.3 T2). An ARN is an identifier, not a
    # credential — invoking it still requires IAM permission.
    runtime_arn: str | None = None


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    job_id: str
    tenant_id: str
    agent_id: str
    workflow_id: str
    status: JobStatus
    priority_key: int
    created_at: str  # ISO 8601 — set by the caller, never datetime.now() in workflow code
    started_at: str | None = None
    completed_at: str | None = None


class JobResult(BaseModel):
    job_id: str
    output: dict
    output_ref: str | None = None  # S3 URI when the payload is offloaded via External Storage
    completed_at: str


class IdempotencyRecord(BaseModel):
    key: str
    status: str
    result: dict | None = None
    created_at: str


class FairnessSetting(BaseModel):
    """Runtime on/off switch for Task Queue Fairness (E3.2, proof 1).

    Fairness isn't a Temporal Cloud setting to flip — it engages the moment any
    job uses a fairness_key. "Off" means resolve_priority() stops attaching one,
    so jobs fall back to the implicit shared empty-string key (plain FIFO).
    """

    enabled: bool = True


class KillSwitch(BaseModel):
    """Armed kill for proof 3's live rehearsal (E6.2). When armed, the next
    consequential-tool call (`run_once`, `app/activities/idempotency.py`) hard-
    exits the worker process immediately after its external call succeeds and
    before Temporal records completion — the exact window idempotency exists
    to cover. Disarms itself the instant it fires, so a restarted worker
    doesn't kill itself again."""

    armed: bool = False
