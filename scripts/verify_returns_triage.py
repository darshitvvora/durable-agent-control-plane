"""Verify the worked tier-1 example, Returns Triage (E10.1 T3).

This agent is the proof of the extensibility claim: `agents/returns_triage/` is
a `manifest.yaml` and a `procedure.sop.md` and nothing else — no workflow code,
no UI code, no activity, no Python at all. So what is under test here is really
the SOP, and asserting "it produced some text" would test nothing.

Six real jobs on Temporal Cloud against real Bedrock, one per rule in the SOP,
including the two cases where rules deliberately conflict:

- a faulty item above the auto-approve limit  -> refund  (rule 2 beats value)
- a used item below it                        -> inspect (rule 4 beats value)

and the tier-1 property itself: zero tool activities in the history, whatever
the model decided.

Requires `make worker` running.
"""

import asyncio
import uuid

from app.config import get_settings
from app.registry import repository as repo
from app.registry.manifest import load_manifest, to_package
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

AGENT_ID = "returns-triage"
TENANT = "globex"

DECISIONS = ("refund", "refuse", "inspect", "review")

# (label, prompt, expected decision). Case ids are randomised per run because
# tenant-scoped Memory persists across runs, and a repeated case id makes the
# agent correctly recall having handled it — a real feature that breaks a
# fixed-id assumption (same lesson as verify_reference_agents.py).
CASES: list[tuple[str, str, str]] = [
    (
        "rule 2 — faulty item, above the auto-approve limit",
        "delivered 6 days ago, order value 240 USD. The blender arrived cracked "
        "and does not power on.",
        "refund",
    ),
    (
        "rule 3 — change of mind, at or below the limit",
        "delivered 9 days ago, order value 40 USD. Customer says the phone case "
        "colour is not what they expected. Unopened and unused.",
        "refund",
    ),
    (
        "rule 3 — change of mind, above the limit",
        "delivered 11 days ago, order value 180 USD. Customer changed their mind "
        "about the espresso grinder. Unopened and unused.",
        "review",
    ),
    (
        "rule 1 — outside the return window",
        "delivered 45 days ago, order value 60 USD. Customer no longer wants the "
        "desk lamp. Unopened.",
        "refuse",
    ),
    (
        "rule 4 — used item, below the limit",
        "delivered 12 days ago, order value 50 USD. Customer changed their mind "
        "about the running shoes and states they wore them outdoors twice.",
        "inspect",
    ),
    (
        "rule 5 — a fact the decision depends on is missing",
        "delivered 8 days ago. Customer changed their mind about the wall clock. "
        "The order value is not recorded anywhere in the case file.",
        "review",
    ),
]


def _decision(output: str) -> str | None:
    """The decision word from the SOP's required first line.

    Read from the `Decision:` line specifically, not by scanning the whole
    answer: the rationale legitimately names other decisions it ruled out
    ("...rather than refund"), so a substring search over the full text would
    match the wrong one and pass or fail for the wrong reason.
    """
    for line in output.splitlines():
        cleaned = line.strip().lower().replace("*", "")
        if cleaned.startswith("decision:"):
            for word in DECISIONS:
                if word in cleaned:
                    return word
    return None


async def run_case(client, prompt: str) -> tuple[str, list[str]]:
    case_id = f"RT-{uuid.uuid4().hex[:6].upper()}"
    settings = get_settings()
    job_id = f"verify-returns-{uuid.uuid4().hex[:8]}"

    handle = await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id,
            tenant_id=TENANT,
            agent_id=AGENT_ID,
            agent_version=1,
            prompt=f"Case {case_id}. Order {case_id}-A {prompt} Requesting a return.",
        ),
        id=job_id,
        task_queue=settings.task_queue,
        priority=resolve_priority(TENANT),
    )
    outcome = await handle.result()
    scheduled = sorted(
        {
            e.activity_task_scheduled_event_attributes.activity_type.name
            async for e in handle.fetch_history_events()
            if e.HasField("activity_task_scheduled_event_attributes")
        }
    )
    return outcome.output, scheduled


async def main() -> None:
    # Publish from disk, exactly as `make agent-publish` would — the point of
    # this example is that the folder is the whole agent.
    manifest, sop = load_manifest(AGENT_ID)
    repo.put_agent_package(to_package(manifest, sop))
    print(f"published {AGENT_ID} v{manifest.version} from agents/returns_triage/")

    client = await connect()
    failures: list[str] = []

    for label, prompt, expected in CASES:
        output, scheduled = await run_case(client, prompt)
        got = _decision(output)
        ok = got == expected
        print(f"{'ok  ' if ok else 'FAIL'} {label}")
        print(f"       expected={expected} got={got}")
        if not ok:
            failures.append(f"{label}: expected {expected}, got {got} — {output.strip()[:200]!r}")

        # Tier 1 means no tools, and that has to be true in the history rather
        # than merely declared in the manifest.
        tools = [a for a in scheduled if a not in ("mark_job_started", "resolve_agent_package")]
        model_and_memory = {
            "invoke_model_streaming",
            "recall_tenant_memory",
            "record_tenant_memory",
        }
        unexpected = [a for a in tools if a not in model_and_memory]
        if unexpected:
            failures.append(f"{label}: tier-1 agent scheduled tool activities {unexpected}")

    if failures:
        for f in failures:
            print(f"  - {f}")
        raise AssertionError(f"{len(failures)} of {len(CASES)} returns-triage cases failed")

    print(
        f"all {len(CASES)} cases passed — the SOP alone drives every decision, no tools scheduled"
    )


if __name__ == "__main__":
    asyncio.run(main())
