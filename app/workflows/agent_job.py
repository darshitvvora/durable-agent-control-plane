"""AgentJobWorkflow — the ONLY workflow type in this system.

One generic workflow serves every agent. Agent-specific behaviour comes from the
manifest in the registry, never from a branch in here (CLAUDE.md §2, §11).

PINNED is what keeps in-flight sessions on the version they started on — that is
proof 2. Do not change it without reading CLAUDE.md §1.
"""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy, VersioningBehavior
from temporalio.contrib.workflow_streams import WorkflowStream
from temporalio.exceptions import ActivityError, ApplicationError

from app.workflows.models import AgentJobInput, JobOutcome, PendingApproval, UIEvent

with workflow.unsafe.imports_passed_through():
    from strands.types.interrupt import InterruptResponseContent
    from temporalio.contrib.strands import TemporalAgent
    from temporalio.contrib.strands import workflow as strands_workflow

    from app.activities.catalog import GUARDED_TOOLS, TOOL_CATALOG
    from app.activities.hosted import invoke_hosted_agent
    from app.activities.memory import recall_tenant_memory, record_tenant_memory
    from app.activities.registry import mark_job_started, resolve_agent_package
    from app.registry.manifest import render_sop
    from app.registry.mcp_servers import MCP_SERVER_CATALOG
    from app.registry.models import AgentTier
    from app.registry.output_models import OUTPUT_MODEL_CATALOG
    from app.workflows.approval import ApprovalGate
    from app.workflows.guardrail import GuardrailGate

MODEL_START_TO_CLOSE = timedelta(seconds=120)
REGISTRY_START_TO_CLOSE = timedelta(seconds=10)
JOB_STARTED_START_TO_CLOSE = timedelta(seconds=10)
MEMORY_START_TO_CLOSE = timedelta(seconds=10)
# A hosted agent runs its whole loop inside this one call (E7.3), so it needs
# far more headroom than a single model call — and only 2 attempts, since a
# retry re-runs that entire loop from scratch.
HOSTED_START_TO_CLOSE = timedelta(seconds=300)
JOB_EVENTS_TOPIC = "job_events"
# Raw Strands StreamEvents land here, published from inside the plugin's
# invoke_model_streaming activity (E4.2). Separate topic from job_events
# because the payload type differs — one stream, two heterogeneous topics.
MODEL_STREAM_TOPIC = "model_stream"


@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)
class AgentJobWorkflow:
    """Hosts a Workflow Stream (temporalio.contrib.workflow_streams, Experimental
    — CLAUDE.md §2's preview-feature labelling rule) so the API's SSE route can
    subscribe live via WorkflowStreamClient, no separate event bus or internal
    HTTP hop needed. All of this workflow's UI events are published directly
    from workflow code (deterministic, in-memory — not activity-based) since
    every one of them is something the workflow itself decides. E4.2's
    token-level streaming, published from inside the model-call activity, is
    the first thing that will need WorkflowStreamClient.from_within_activity()
    on this same stream.
    """

    @workflow.init
    def __init__(self, job: AgentJobInput) -> None:
        self._pending: PendingApproval | None = None
        self._decisions: dict[str, str] = {}
        # Constructed in @workflow.init, not __init__'s usual bare form, so the
        # stream's query/update handlers are registered before run() starts —
        # required for an external subscriber to attach reliably from the start.
        self.stream = WorkflowStream()
        self.events = self.stream.topic(JOB_EVENTS_TOPIC, type=UIEvent)

    @workflow.signal
    def submit_approval(self, interrupt_id: str, decision: str) -> None:
        """Human verdict for a paused tool call. 'approve' lets it run; anything
        else is treated as a denial and its text is handed back to the model."""
        self._decisions[interrupt_id] = decision

    @workflow.query
    def pending_approval(self) -> PendingApproval | None:
        """What this session is waiting on, for the session pane."""
        return self._pending

    @workflow.run
    async def run(self, job: AgentJobInput) -> JobOutcome:
        workflow.logger.info(
            "job started: agent=%s v%s tenant=%s", job.agent_id, job.agent_version, job.tenant_id
        )

        # Best-effort wait-time instrumentation (E3.2, proof 1). A no-op for
        # callers that never created a Job row, and never fatal to the job
        # itself — a metrics stamp failing is not a reason to fail real work.
        try:
            await workflow.execute_activity(
                mark_job_started,
                args=[job.job_id],
                start_to_close_timeout=JOB_STARTED_START_TO_CLOSE,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except ActivityError:
            workflow.logger.warning("mark_job_started failed for %s, continuing", job.job_id)

        self.events.publish(
            UIEvent(
                job_id=job.job_id,
                kind="job_started",
                payload={"agent_id": job.agent_id, "agent_version": job.agent_version},
            )
        )

        package = await workflow.execute_activity(
            resolve_agent_package,
            args=[job.agent_id, job.agent_version],
            start_to_close_timeout=REGISTRY_START_TO_CLOSE,
        )

        # Tenant-scoped recall (E7.1 T2) — every agent, not manifest-gated,
        # same cross-cutting shape as mark_job_started above. Empty (not an
        # error) if Memory isn't configured or this tenant has no history yet.
        recalled = await workflow.execute_activity(
            recall_tenant_memory,
            args=[job.tenant_id],
            start_to_close_timeout=MEMORY_START_TO_CLOSE,
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        if recalled:
            self.events.publish(
                UIEvent(job_id=job.job_id, kind="memory_recall", payload={"notes": recalled})
            )

        # The one branch in this workflow, and it is on a manifest *field*, not
        # on an agent id — tier 3 means the agent runs its own loop somewhere
        # else, so there is no Strands agent to build here at all (E7.3).
        # Everything after this point is shared by both lanes.
        if package.tier == AgentTier.HOSTED:
            stop_reason, output = await self._run_hosted(job, package)
        else:
            stop_reason, output = await self._run_native(job, package, recalled)

        workflow.logger.info("job finished: stop_reason=%s", stop_reason)
        self.events.publish(
            UIEvent(
                job_id=job.job_id,
                kind="job_finished",
                payload={"stop_reason": stop_reason},
            )
        )

        # Write this job's outcome for future recall by the same tenant
        # (E7.1 T2). Best-effort: a memory write failing is never a reason to
        # fail a job it is only summarizing, so its own retries are capped low
        # and any remaining failure is swallowed, not raised.
        summary = f"{job.agent_id} v{job.agent_version}: {stop_reason} — {output[:200]}"
        try:
            await workflow.execute_activity(
                record_tenant_memory,
                args=[job.tenant_id, summary],
                start_to_close_timeout=MEMORY_START_TO_CLOSE,
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        except ActivityError:
            workflow.logger.warning("record_tenant_memory failed for %s, continuing", job.job_id)

        # Hold the run open briefly so a subscriber's next poll delivers this
        # terminal event before the workflow closes and the log is gone —
        # same reasoning as the workflow_streams samples' OrderWorkflow/LLMWorkflow.
        await workflow.sleep(timedelta(milliseconds=500))

        return JobOutcome(
            job_id=job.job_id,
            agent_id=job.agent_id,
            agent_version=job.agent_version,
            stop_reason=stop_reason,
            output=output,
        )

    async def _run_hosted(self, job: AgentJobInput, package: Any) -> tuple[str, str]:
        """Tier 3: one call to someone else's agent loop on AgentCore Runtime.

        No tools, no MCP, no approval gate, no guardrail hook, no token stream
        — none of those exist on this side of the boundary. That is the tier-3
        tradeoff, documented in docs/MULTI_AGENT.md, not an omission.
        """
        self.events.publish(
            UIEvent(
                job_id=job.job_id,
                kind="tool_call",
                payload={"tool": f"hosted:{package.agent_id}"},
            )
        )
        output = await workflow.execute_activity(
            invoke_hosted_agent,
            args=[package.runtime_arn or "", job.job_id, job.prompt],
            start_to_close_timeout=HOSTED_START_TO_CLOSE,
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return "end_turn", output

    async def _run_native(
        self, job: AgentJobInput, package: Any, recalled: list[str]
    ) -> tuple[str, str]:
        """Tiers 1 and 2: this control plane runs the agent loop itself, with
        every model and tool call dispatched as its own Temporal activity."""
        # Tools come from the manifest, resolved against the catalog. An unknown
        # name is the agent author's error and cannot be fixed by retrying, so it
        # fails the workflow rather than silently running a toolless agent.
        tools: list[Any] = []
        for tool_name in package.tools:
            spec = TOOL_CATALOG.get(tool_name)
            if spec is None:
                raise ApplicationError(
                    f"unknown tool {tool_name!r} declared by {package.agent_id}",
                    type="UnknownTool",
                    non_retryable=True,
                )
            tools.append(strands_workflow.activity_as_tool(spec.activity, **spec.options))

        # Same resolve-or-fail shape as tools above (E7.1 T1). TemporalMCPClient
        # is itself a Strands ToolProvider — it goes straight into tools=,
        # unlike TOOL_CATALOG's activities which need activity_as_tool() first.
        for server_name in package.mcp_servers:
            client = MCP_SERVER_CATALOG.get(server_name)
            if client is None:
                raise ApplicationError(
                    f"unknown mcp_server {server_name!r} declared by {package.agent_id}",
                    type="UnknownMCPServer",
                    non_retryable=True,
                )
            tools.append(client)

        # Same resolve-or-fail shape as tools above (E6.1 T4). Pure type lookup,
        # no I/O — safe directly in workflow code, unlike the tool activities.
        output_model = None
        if package.output_model is not None:
            output_model = OUTPUT_MODEL_CATALOG.get(package.output_model)
            if output_model is None:
                raise ApplicationError(
                    f"unknown output_model {package.output_model!r} declared by {package.agent_id}",
                    type="UnknownOutputModel",
                    non_retryable=True,
                )

        # SOP placeholders resolve here, per job: manifest defaults first, then
        # runtime context wins. Pure string substitution, so it is replay-safe and
        # one published package can serve every tenant.
        system_prompt = render_sop(
            package.system_prompt,
            {
                **package.parameters,
                "tenant_id": job.tenant_id,
                "agent_id": job.agent_id,
                "agent_version": str(job.agent_version),
                "job_id": job.job_id,
            },
        )
        if recalled:
            history = "\n".join(f"- {note}" for note in recalled)
            system_prompt += f"\n\n## Recent history for this tenant\n{history}"

        # Constructed from the manifest — deterministic, no I/O. The model, tool,
        # and MCP calls it makes are dispatched as Temporal activities by StrandsPlugin.
        agent = TemporalAgent(
            model=package.model,
            system_prompt=system_prompt,
            tools=tools,
            hooks=[ApprovalGate(package.approval_policy), GuardrailGate(GUARDED_TOOLS)],
            structured_output_model=output_model,
            start_to_close_timeout=MODEL_START_TO_CLOSE,
            retry_policy=RetryPolicy(maximum_attempts=3),
            # Strands' default handler prints tokens to stdout, which also fires
            # during replay. Token output belongs on the session terminal via
            # Workflow Streams, not the worker's console.
            callback_handler=None,
            # E4.2: switches the plugin from invoke_model to
            # invoke_model_streaming, which publishes each StreamEvent onto this
            # workflow's stream via WorkflowStreamClient.from_within_activity().
            # The token content originates inside the activity, so it cannot be
            # published from workflow code the way the job_events above are.
            streaming_topic=MODEL_STREAM_TOPIC,
        )

        # invoke_async, never agent(...) — the sync form spawns a thread the
        # workflow sandbox blocks.
        result = await agent.invoke_async(job.prompt)

        # A loop, not an `if`: an agent can pause more than once in a session.
        # Waiting here holds no worker resources, so a session can sit pending a
        # human for days without cost.
        while result.stop_reason == "interrupt" and result.interrupts:
            interrupt = result.interrupts[0]
            interrupt_id = interrupt.id
            reason = interrupt.reason if isinstance(interrupt.reason, dict) else {}
            self._pending = PendingApproval(
                interrupt_id=interrupt_id,
                tool=str(reason.get("tool", "")),
                tool_input=reason.get("input", {}),
                policy=str(reason.get("policy", "")),
            )
            workflow.logger.info("awaiting approval for %s", interrupt_id)
            self.events.publish(
                UIEvent(
                    job_id=job.job_id,
                    kind="approval_pending",
                    payload={"tool": self._pending.tool, "tool_input": self._pending.tool_input},
                )
            )

            # Default-arg bind: the lambda outlives this iteration's scope.
            def decided(iid: str = interrupt_id) -> bool:
                return iid in self._decisions

            await workflow.wait_condition(decided)
            decision = self._decisions[interrupt_id]
            self._pending = None
            workflow.logger.info("approval received for %s: %s", interrupt_id, decision)
            self.events.publish(
                UIEvent(job_id=job.job_id, kind="approval_resumed", payload={"decision": decision})
            )

            response: InterruptResponseContent = {
                "interruptResponse": {"interruptId": interrupt_id, "response": decision}
            }
            result = await agent.invoke_async([response])

        return str(result.stop_reason), str(result)
