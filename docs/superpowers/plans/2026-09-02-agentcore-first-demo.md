# AgentCore-First Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear every blocker that prevents the AgentCore-first run of show, then produce the timed demo script and a recorded fallback.

**Architecture:** No new workflow type and no new datastore. One new API route and one new UI panel make arbitrary agents launchable from the browser; the remaining work is defect fixes, operational scripts (`reset`, `seed`, `preflight`), and documentation. Only Task 3 touches workflow-adjacent code, and it changes activity implementations rather than workflow code, so no `BUILD_ID` bump is required anywhere in this plan.

**Tech Stack:** Python 3.12 · `uv` · Temporal Python SDK (Temporal Cloud only) · Strands via `temporalio.contrib.strands` · FastAPI · React + Vite + Tailwind · Playwright · boto3 against real AWS · Mockoon

**Spec:** `docs/superpowers/specs/2026-09-02-agentcore-first-demo-design.md`

## Global Constraints

Copied verbatim from CLAUDE.md and the spec. Every task's requirements implicitly include these.

- **No unit tests.** No `pytest`, no `tests/` directory, no mocking libraries. Verification is scripts under `scripts/` run against real AWS and real Temporal Cloud. **This overrides the writing-plans skill's default TDD-with-pytest cycle**; the equivalent cycle here is *write the verification script → run it → watch it fail → implement → run it → watch it pass*.
- **Temporal Cloud only.** Never `temporal server start-dev`, never a local server, in any environment.
- **Real AWS.** No DynamoDB Local, no Docker sandbox fallback, no offline path.
- **One generic workflow.** `AgentJobWorkflow` serves every agent. No second workflow type. No agent-specific logic inside it.
- **No fake data.** Every number on screen comes from a real Temporal or AWS call.
- **State rule.** If state does not belong in Temporal event history, it goes in DynamoDB. No Redis, no Postgres, no S3-as-database.
- **AWS mutations are manual.** Never run AWS commands that create or mutate resources. Write the exact steps into `docs/AWS_SETUP.md` for a human. Read-only `describe-*`/`list-*`/`get-caller-identity` are fine to run directly.
- **`make replay` must pass** after any workflow change, and `make ci` (ruff + mypy) after every task.
- **Preview features must be labelled** as Preview in code comments and docs. Never claim GA.
- **The API is the sole Temporal client.** The UI never talks to Temporal directly.
- **Do not hardcode tenant or agent lists** — they come from the registry.
- **Append every non-obvious choice to `docs/DECISIONS.md`.** Never rewrite existing entries.
- **Current state:** `BUILD_ID=v18`, current deployment version `agent-control-plane.v18`, region `us-east-1`, table `agent-control-plane`, Mockoon on `:3001`, API on `:8000`, UI on `:5173`.

---

## File Structure

**Created:**
- `app/sessions.py` — the single place a non-flood agent job is started. Used by the new API route *and* by `dos agent test`, so both write a `Job` row.
- `ui/src/components/RunSession.tsx` — agent picker, prompt field, Run button.
- `scripts/seed_demo.py` — runs the real prior sessions beat 0's memory recall depends on.
- `scripts/preflight.py` — read-only readiness check for every external dependency.
- `scripts/verify_run_session.py` — verification for Task 1.
- `scripts/verify_flood_health.py` — verification for Task 3.
- `docs/RUNBOOK.md` — laptop-hosted operating guide.

**Modified:**
- `app/api/routes/jobs.py` — add `POST /api/jobs`.
- `cli/main.py:234-275` — `_run_test` delegates to `app/sessions.py`.
- `app/activities/registry.py`, `app/activities/memory.py` — offload blocking boto3.
- `app/worker.py:32` — explicit `max_concurrent_activities`.
- `ui/src/App.tsx:51-70` — stop re-targeting the session terminal mid-session.
- `ui/src/api.ts`, `ui/src/types.ts` — the new endpoint and its types.
- `scripts/reset.py` — memory purge plus the rest of clean state.
- `mocks/payment-service.json`, `mocks/README.md` — reset routes and real documentation.
- `Makefile` — new targets.
- `docs/DEMO_SCRIPT.md` — the run of show.
- `docs/BACKLOG.md`, `docs/DECISIONS.md`, `README.md` — bookkeeping.

---

## Task 1: Start an agent session from the API

Beats 0 and 2 cannot start their agents from the browser. The UI can only trigger floods, and the flood agent is `incident-triage` — tier 1, toolless — so the only browser-launchable agent touches no AgentCore services. Separately, `dos agent test` never writes a `Job` row, which is why CLI sessions are invisible in the UI. One shared helper fixes both.

**Files:**
- Create: `app/sessions.py`
- Create: `scripts/verify_run_session.py`
- Modify: `app/api/routes/jobs.py`
- Modify: `cli/main.py:234-275`
- Modify: `Makefile`

**Interfaces:**
- Produces: `app.sessions.start_session(agent_id: str, tenant_id: str, prompt: str) -> str` returning the `job_id`; `POST /api/jobs?agent_id=&tenant_id=&prompt=` returning `{"job_id": str}`.
- Consumes: `app.registry.priority.resolve_priority`, `tenant_search_attributes`; `app.registry.repository.put_job`, `list_agent_package_versions`.

- [ ] **Step 1: Write the verification script**

Create `scripts/verify_run_session.py`:

```python
"""E8/beat-0 prerequisite: a session started through the API is a first-class
job — it gets a Job row, appears in the UI's job list, and runs to completion.

Requires `make worker`, `make api` and Mockoon running.
"""

import asyncio

import httpx

from app.registry import repository as repo

API = "http://localhost:8000"
TENANT = "acme"
AGENT = "returns-triage"  # tier 1: no tools, no payment side effect, cheap
PROMPT = "Order A-1002, opened box, buyer says wrong size, 12 days since delivery."


async def main() -> int:
    async with httpx.AsyncClient(timeout=180.0) as http:
        response = await http.post(
            f"{API}/api/jobs",
            params={"agent_id": AGENT, "tenant_id": TENANT, "prompt": PROMPT},
        )
        response.raise_for_status()
        job_id = response.json()["job_id"]
        print(f"started {job_id}")

        row = repo.get_job(job_id)
        assert row is not None, f"no Job row written for {job_id}"
        assert row.tenant_id == TENANT, f"wrong tenant on Job row: {row.tenant_id}"
        print("Job row written")

        listed = (await http.get(f"{API}/api/jobs", params={"tenant_id": TENANT})).json()
        assert any(j["job_id"] == job_id for j in listed), "job absent from /api/jobs"
        print("job visible in the UI's job list")

        for _ in range(90):
            state = (await http.get(f"{API}/api/jobs/{job_id}/state")).json()
            if state["status"] != "RUNNING":
                break
            await asyncio.sleep(2)

        assert state["status"] == "COMPLETED", f"ended {state['status']}"
        print(f"completed on {state['worker_version']}")

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python -m scripts.verify_run_session`
Expected: HTTP 405 or 404 — `POST /api/jobs` does not exist.

- [ ] **Step 3: Create the shared session starter**

Create `app/sessions.py`:

```python
"""Starting one agent job — the single implementation shared by the API's
POST /api/jobs and the CLI's `dos agent test`.

Why this exists: `dos agent test` used to call start_workflow directly and
never wrote a Job row, so CLI-started sessions were invisible in the UI's
session list, absent from p95, and missing from queued counts, while
`demo.submit_flood` wrote one correctly. Two callers, two behaviours, one of
them wrong. See docs/DECISIONS.md (2026-09-02).

Every DynamoDB call goes through asyncio.to_thread: boto3 is synchronous and
this runs on the API's event loop.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import Job, JobStatus
from app.registry.priority import resolve_priority, tenant_search_attributes
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput


async def start_session(agent_id: str, tenant_id: str, prompt: str) -> str:
    """Start one agent job and return its job id. Raises ValueError for an
    unknown tenant or an unpublished agent — the caller's mistake, not
    something to paper over with a default."""
    priority = await asyncio.to_thread(resolve_priority, tenant_id)

    versions = await asyncio.to_thread(repo.list_agent_package_versions, agent_id)
    if not versions:
        raise ValueError(
            f"agent {agent_id!r} is not published — run `dos agent publish {agent_id}` first"
        )
    agent_version = max(package.version for package in versions)

    settings = get_settings()
    job_id = f"session-{agent_id}-{uuid.uuid4().hex[:8]}"

    await asyncio.to_thread(
        repo.put_job,
        Job(
            job_id=job_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            workflow_id=job_id,
            status=JobStatus.QUEUED,
            priority_key=priority.priority_key,
            created_at=datetime.now(UTC).isoformat(),
        ),
    )

    client = await connect()
    await client.start_workflow(
        AgentJobWorkflow.run,
        AgentJobInput(
            job_id=job_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version=agent_version,
            prompt=prompt,
        ),
        id=job_id,
        task_queue=settings.task_queue,
        priority=priority,
        search_attributes=tenant_search_attributes(tenant_id),
    )
    return job_id
```

- [ ] **Step 4: Add the API route**

In `app/api/routes/jobs.py`, add the import and the route after `list_jobs`:

```python
from app.sessions import start_session


@router.post("")
async def create_job(agent_id: str, tenant_id: str, prompt: str) -> dict:
    """Start one agent session. The demo's only way to run a non-flood agent
    from the browser — beats 0 and 2 depend on it."""
    try:
        job_id = await start_session(agent_id, tenant_id, prompt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"job_id": job_id}
```

- [ ] **Step 5: Run the verification script**

Run: `uv run python -m scripts.verify_run_session`
Expected: PASS — Job row written, job visible in `/api/jobs`, status `COMPLETED`.

- [ ] **Step 6: Point the CLI at the same helper**

In `cli/main.py`, replace the body of `_run_test` from `job_id = ...` through `handle = await client.start_workflow(...)` with:

```python
    from app.sessions import start_session

    job_id = await start_session(manifest.id, tenant, prompt)
    typer.echo(f"running {job_id} on {settings.temporal_namespace}...")
    client = await connect()
    handle = client.get_workflow_handle_for(AgentJobWorkflow.run, job_id)
```

Keep the existing `validate_package` / `resolve_priority` guard and `repo.put_agent_package(to_package(manifest, sop))` publish step ahead of it; delete the now-duplicated priority, job-id and `start_workflow` code.

- [ ] **Step 7: Confirm the CLI now registers its sessions**

Run: `uv run dos agent test returns-triage --tenant globex --prompt "Order A-1003, unopened, 5 days since delivery."`
Then: `uv run python -c "from app.registry import repository as repo; print([j.job_id for j in repo.list_jobs_for_tenant('globex', limit=5)])"`
Expected: the new `session-returns-triage-*` id appears. Before this change it would not have.

- [ ] **Step 8: Add the Makefile target**

```makefile
# needs `make worker` and `make api` running
verify-run-session:
	uv run python -m scripts.verify_run_session
```

Add `verify-run-session` to the `.PHONY` list.

- [ ] **Step 9: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy app cli scripts
git add app/sessions.py app/api/routes/jobs.py cli/main.py scripts/verify_run_session.py Makefile
git commit -m "feat: start an agent session from the API, shared with the CLI"
```

---

## Task 2: "Run session" control in the UI

**Files:**
- Create: `ui/src/components/RunSession.tsx`
- Modify: `ui/src/api.ts`, `ui/src/types.ts`, `ui/src/App.tsx`, `ui/e2e/shell.spec.ts`

**Interfaces:**
- Consumes: `POST /api/jobs` from Task 1.
- Produces: `api.runSession(agentId, tenantId, prompt): Promise<{job_id: string}>`; a `RunSession` component taking `{agents, tenantId, onStarted}`.

- [ ] **Step 1: Add the client call**

In `ui/src/api.ts`, inside the `api` object:

```ts
  runSession: (agentId: string, tenantId: string, prompt: string) =>
    post<{ job_id: string }>(
      `/api/jobs?agent_id=${encodeURIComponent(agentId)}&tenant_id=${encodeURIComponent(
        tenantId,
      )}&prompt=${encodeURIComponent(prompt)}`,
    ),
```

- [ ] **Step 2: Write the component**

Create `ui/src/components/RunSession.tsx`:

```tsx
import { useState } from "react";
import { api } from "../api";
import type { AgentPackage } from "../types";
import { Panel } from "./Panel";

/** Launch one agent session. Without this the UI can only start floods, whose
 * agent is tier 1 and toolless — i.e. the only browser-launchable agent
 * touches no AgentCore services at all. Beats 0 and 2 need this. */
export function RunSession({
  agents,
  tenantId,
  onStarted,
}: {
  agents: AgentPackage[];
  tenantId: string | null;
  onStarted: (jobId: string) => void;
}) {
  const [agentId, setAgentId] = useState<string>("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = agentId || agents[0]?.agent_id || "";

  const run = async () => {
    if (!tenantId || !selected || !prompt.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { job_id } = await api.runSession(selected, tenantId, prompt.trim());
      onStarted(job_id);
      setPrompt("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Run session">
      <div className="flex flex-col gap-2">
        <select
          className="btn font-mono text-[15px]"
          value={selected}
          onChange={(e) => setAgentId(e.target.value)}
        >
          {agents.map((a) => (
            <option key={a.agent_id} value={a.agent_id}>
              {a.name} · tier {a.tier}
            </option>
          ))}
        </select>
        <textarea
          className="raised min-h-[64px] bg-[#1b1a16] p-2 font-mono text-[15px] text-ink"
          placeholder="Prompt for this session"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <button className="btn" disabled={busy || !tenantId || !prompt.trim()} onClick={run}>
          {busy ? "Starting..." : "Run"}
        </button>
        {error && <p className="font-mono text-[14px] text-alert">{error}</p>}
      </div>
    </Panel>
  );
}
```

- [ ] **Step 3: Mount it and pin the started session**

In `ui/src/App.tsx`: import `RunSession`, add `const [pinnedJobId, setPinnedJobId] = useState<string | null>(null);`, render `<RunSession agents={agents} tenantId={tenantId} onStarted={setPinnedJobId} />` inside the left column, and pass `pinnedJobId` down per Task 4 (which is what makes the terminal follow *this* session rather than whatever is newest).

- [ ] **Step 4: Verify by hand against the real stack**

With all four processes running, open `http://localhost:5173`, pick `dispute-resolution`, enter a prompt, click Run.
Expected: the Session Terminal attaches to the new session and streams tokens and tool calls.

- [ ] **Step 5: Add an e2e spec**

Append to `ui/e2e/shell.spec.ts`:

```ts
test("a session can be started from the UI and streams", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("combobox").last().selectOption({ label: /Returns Triage/ });
  await page.getByPlaceholder("Prompt for this session").fill(
    "Order A-1004, unopened, 6 days since delivery.",
  );
  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByText(/session started/)).toBeVisible({ timeout: 60_000 });
});
```

- [ ] **Step 6: Run it, then commit**

```bash
make e2e
git add ui/src/components/RunSession.tsx ui/src/api.ts ui/src/App.tsx ui/e2e/shell.spec.ts
git commit -m "feat(ui): run any agent session from the browser"
```

---

## Task 3: Stop blocking boto3 starving the worker event loop (E8.1 T5)

Accepted on 2026-09-02 when the flood was a standalone beat. That no longer holds: the cold-open flood runs underneath beats 0–3, so the worker is loaded during every AgentCore beat. Symptoms are `Activity not found on completion` warnings, `resolve_agent_package` reaching attempt 6, and p95 partly measuring event-loop starvation rather than queue position — which makes proof 1's numbers untrustworthy.

**Files:**
- Create: `scripts/verify_flood_health.py`
- Modify: `app/activities/registry.py`, `app/activities/memory.py`, `app/worker.py:32`, `Makefile`

**Interfaces:**
- Produces: no signature changes. Activity names, parameters and return types are unchanged, so no `BUILD_ID` bump and existing histories replay untouched.

- [ ] **Step 1: Write the verification script**

Create `scripts/verify_flood_health.py`:

```python
"""E8.1 T5: a flood must not starve the worker's event loop.

Fails if any flood job fails, or if any activity needed more than one attempt.
Attempt > 1 on a trivial DynamoDB read is the starvation signature — the task
timed out in the worker's queue, not in AWS.

Requires `make worker` and Mockoon running. Costs `COUNT` real tier-1 jobs.
"""

import asyncio
import sys

from app.demo import submit_flood
from app.registry import repository as repo
from app.temporal_client import connect

TENANT = "initech"
COUNT = 30


async def main() -> int:
    before = {j.job_id for j in repo.list_jobs_for_tenant(TENANT, limit=200)}
    print(f"flooding {COUNT} jobs for {TENANT}...")
    await submit_flood(TENANT, COUNT)

    client = await connect()
    new_ids = [
        j.job_id
        for j in repo.list_jobs_for_tenant(TENANT, limit=200)
        if j.job_id not in before
    ]
    assert len(new_ids) == COUNT, f"expected {COUNT} new jobs, got {len(new_ids)}"

    failed, retried = [], []
    for job_id in new_ids:
        handle = client.get_workflow_handle(job_id)
        for _ in range(150):
            desc = await handle.describe()
            if desc.status is not None and desc.status.name != "RUNNING":
                break
            await asyncio.sleep(2)
        if desc.status is None or desc.status.name != "COMPLETED":
            failed.append((job_id, desc.status.name if desc.status else "UNKNOWN"))
            continue
        async for event in handle.fetch_history_events():
            if event.WhichOneof("attributes") == "activity_task_started_event_attributes":
                attempt = event.activity_task_started_event_attributes.attempt
                if attempt > 1:
                    retried.append((job_id, attempt))

    print(f"\ncompleted: {COUNT - len(failed)}/{COUNT}")
    print(f"failed:    {len(failed)}")
    print(f"activities needing more than one attempt: {len(retried)}")
    for job_id, detail in (failed + retried)[:10]:
        print(f"  {job_id} {detail}")

    ok = not failed and not retried
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python -m scripts.verify_flood_health`
Expected: FAIL — retried activities, and likely failures too.

- [ ] **Step 3: Offload the registry activities**

In `app/activities/registry.py`, add `import asyncio` and change the two bodies:

```python
@activity.defn
async def resolve_agent_package(agent_id: str, version: int) -> AgentPackage:
    """Load an agent's manifest from the registry.

    A missing package is a permanent failure — retrying will not conjure the row,
    so it is raised non-retryable rather than burning the retry budget.

    boto3 is synchronous, so the call goes to a thread: an async activity that
    blocks freezes the worker's whole event loop, and under a flood that turns a
    millisecond read into a start-to-close timeout (docs/DECISIONS.md, E8.1 T5).
    """
    package = await asyncio.to_thread(repo.get_agent_package, agent_id, version)
    if package is None:
        raise ApplicationError(
            f"agent package not found: {agent_id} v{version}",
            type="AgentPackageNotFound",
            non_retryable=True,
        )
    return package


@activity.defn
async def mark_job_started(job_id: str) -> None:
    """Stamp when a job actually began executing (E3.2's wait-time metric).

    Outside the workflow sandbox, so datetime.now() here is fine — the value it
    records is the real thing being measured, not a workflow decision. Threaded
    for the same reason as resolve_agent_package above; this one doubly so,
    since it is the activity that *defines* the measured wait.
    """
    await asyncio.to_thread(
        repo.mark_job_started, job_id, started_at=datetime.now(UTC).isoformat()
    )
```

- [ ] **Step 4: Offload the memory activities**

In `app/activities/memory.py`, add `import asyncio` and wrap both AgentCore calls:

```python
    response = await asyncio.to_thread(
        lambda: _client().list_events(
            memoryId=settings.agentcore_memory_id,
            actorId=tenant_id,
            sessionId=tenant_id,
            includePayloads=True,
            maxResults=RECALL_LIMIT,
        )
    )
```

and the `create_event(...)` call in `record_tenant_memory` the same way.

- [ ] **Step 5: Cap worker activity concurrency**

In `app/worker.py`, add to the `Worker(...)` call:

```python
        # The SDK default is 100. One laptop worker running 100 concurrent
        # activities against Bedrock and AgentCore is not throughput, it is a
        # queue with extra steps — and every one of them competes for the same
        # event loop. Sized for the demo's single-worker lane (E8.1 T5).
        max_concurrent_activities=20,
```

- [ ] **Step 6: Restart the worker and re-run**

Restart `make worker` (no `--reload`), then run: `uv run python -m scripts.verify_flood_health`
Expected: PASS — 30/30 completed, zero failures, zero activities past attempt 1.

- [ ] **Step 7: Confirm nothing else regressed**

```bash
make replay          # expect clean; no BUILD_ID bump, so v18 histories must still replay
make verify-agents
make verify-payment
```

- [ ] **Step 8: Add the target, record the decision, commit**

Add to `Makefile` (and `.PHONY`):

```makefile
# needs `make worker` and Mockoon running — costs 30 real tier-1 jobs
verify-flood-health:
	uv run python -m scripts.verify_flood_health
```

Append a `docs/DECISIONS.md` entry recording that E8.1 T5 was reopened because the AgentCore-first structure put a flood underneath beats 0–3, what was changed, and that no `BUILD_ID` bump was needed. Tick E8.1 T5 in `docs/BACKLOG.md`.

```bash
git add app/activities/registry.py app/activities/memory.py app/worker.py \
        scripts/verify_flood_health.py Makefile docs/DECISIONS.md docs/BACKLOG.md
git commit -m "fix: offload blocking boto3 out of the worker event loop"
```

---

## Task 4: Root-cause the consecutive-session stream drop (E8.1 T0)

Beats 0, 1, 2 and 3 are four sessions in a row on one page — the exact reproduction. Two of three causes were fixed on 2026-09-01; the third drops a stream mid-session and was never root-caused.

**REQUIRED SUB-SKILL:** Use `superpowers:systematic-debugging`. Do not skip to the fix, even though this plan states a leading hypothesis.

**Files:**
- Modify: `ui/src/App.tsx:51-70`, `ui/src/components/SessionTerminal.tsx`, `ui/e2e/shell.spec.ts`

**Interfaces:**
- Produces: `SessionTerminal` receives an explicitly pinned job rather than "whatever is newest".

- [ ] **Step 1: Reproduce before changing anything**

Remove `.fixme` from the "four consecutive sessions on one page all stream" spec and run `make e2e`. Confirm it fails, and capture *how*: which round, and whether `job_finished` ever arrives. A guard must be shown to fail before it can be cited as evidence that a fix worked — the 2026-09-01 entry records getting this wrong.

- [ ] **Step 2: Test the leading hypothesis**

`App.tsx:51-70` re-picks `jobs[0]` every 2s and hands it to `SessionTerminal`, whose effect keys on `[job]`. When a newer job appears for the selected tenant, the terminal closes its live `EventSource`, clears entries and attaches elsewhere — indistinguishable, from the operator's seat, from "the stream went quiet without `job_finished`."

Instrument to confirm or kill it: log `job.job_id` on every run of the effect, and log every `EventSource` open and close. Reproduce. If the id changes mid-session, the hypothesis holds.

- [ ] **Step 3: If confirmed, pin the session**

Add `pinnedJobId` (already introduced in Task 2 Step 3) and change the picker so an explicitly started session wins, and auto-follow only applies when nothing is pinned:

```tsx
  // Follow the newest job only while nothing is pinned. A session the operator
  // started explicitly must not be yanked away by a flood job arriving two
  // seconds later — that closes the live stream mid-session and reads on stage
  // as "the stream died" (E8.1 T0).
  useEffect(() => {
    if (!tenantId || pinnedJobId) return;
    // ...existing polling body unchanged...
  }, [tenantId, pinnedJobId]);

  useEffect(() => {
    if (!pinnedJobId) return;
    void api.job(pinnedJobId).then(setJob);
  }, [pinnedJobId]);
```

Add `job: (jobId: string) => get<Job>(\`/api/jobs/${encodeURIComponent(jobId)}\`)` to `ui/src/api.ts` (the `GET /api/jobs/{job_id}` route already exists).

- [ ] **Step 4: If the hypothesis is killed, keep investigating**

Next candidates, in order: the browser's six-connection-per-origin HTTP/1.1 cap (SSE competes with the 2s job poll, the metrics poll and the state poll on one origin); `sse_starlette`'s ping versus `request.is_disconnected`; offset resumption landing past `job_finished` after a reconnect. Do not stack fixes — one hypothesis at a time. After three failed fixes, stop and question the architecture rather than attempting a fourth.

- [ ] **Step 5: Prove the guard in both directions**

Run `make e2e` with the fix: expect PASS. Revert the fix, run again: expect FAIL. Restore the fix. Only now is the spec a real guard.

- [ ] **Step 6: Record and commit**

Append a `docs/DECISIONS.md` entry with the confirmed root cause and the evidence that identified it. Tick E8.1 T0 in `docs/BACKLOG.md`.

```bash
git add ui/src/App.tsx ui/src/components/SessionTerminal.tsx ui/src/api.ts \
        ui/e2e/shell.spec.ts docs/DECISIONS.md docs/BACKLOG.md
git commit -m "fix(ui): keep the session terminal pinned to its own session"
```

---

## Task 5: Complete the reset script (E8.1 T4)

Currently clears `Job` rows only. Measured 2026-09-02: acme, globex and initech each held five near-identical `incident-triage` summaries in AgentCore Memory, because every flood job writes one. Beat 0 on acme would open by recalling load-test noise about `checkout-api` — unrelated to any invoice, and the weakest possible version of the strongest AgentCore moment.

**Files:**
- Modify: `scripts/reset.py`, `mocks/payment-service.json`, `mocks/README.md`, `Makefile`

**Interfaces:**
- Produces: `make demo-reset` returning fairness ON, kill switch disarmed, ramp cleared, job rows gone, Mockoon buckets empty, tenant memory purged.

- [ ] **Step 1: Add Mockoon reset routes**

In Mockoon (desktop or by editing `mocks/payment-service.json`), add `DELETE /payments` and `DELETE /dispute-responses` against the existing CRUD buckets. Verify:

```bash
curl -X DELETE http://localhost:3001/payments && curl -s http://localhost:3001/payments
```

Expected: `[]`.

- [ ] **Step 2: Extend the reset script**

Add to `scripts/reset.py`, keeping the existing job-row logic and the `--yes` gate. New imports first:

```python
import boto3

from app.config import get_settings
from app.registry.models import FairnessSetting, KillSwitch


def _agentcore_client():
    """Same profile-explicit session shape as every other AWS client in this
    app. An unprofiled session resolves through the `[default]` profile, which
    fails outright when that profile is configured for `aws login`
    (docs/DECISIONS.md, 2026-09-02)."""
    settings = get_settings()
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    return session.client("bedrock-agentcore")


def _purge_memory(tenant_id: str) -> int:
    """Delete this tenant's AgentCore Memory events.

    Every flood job writes one, so after a rehearsal a tenant's recall is
    dominated by identical load-test summaries. Beat 0's memory moment depends
    on recall surfacing genuine, relevant prior decisions instead.
    """
    settings = get_settings()
    if not settings.agentcore_memory_id:
        return 0
    client = _agentcore_client()
    deleted = 0
    while True:
        response = client.list_events(
            memoryId=settings.agentcore_memory_id,
            actorId=tenant_id,
            sessionId=tenant_id,
            maxResults=100,
        )
        events = response.get("events", [])
        if not events:
            return deleted
        for event in events:
            client.delete_event(
                memoryId=settings.agentcore_memory_id,
                sessionId=tenant_id,
                eventId=event["eventId"],
                actorId=tenant_id,
            )
            deleted += 1
```

Then restore the rest of clean state: `repo.put_fairness_setting(FairnessSetting(enabled=True))`, `repo.put_kill_switch(KillSwitch(armed=False))`, `await clear_ramp(client)`, and `DELETE` both Mockoon buckets.

> Verify `delete_event`'s exact parameter names against the installed boto3 service model before writing this — `uv run python -c "import boto3; print(boto3.Session().client('bedrock-agentcore').meta.service_model.operation_model('DeleteEvent').input_shape.members.keys())"`. Do not guess AWS API shapes (CLAUDE.md §11).

- [ ] **Step 3: Run it and confirm every dimension**

```bash
uv run python -m scripts.reset --yes
uv run python -c "
import asyncio
from app.activities.memory import recall_tenant_memory
from app.registry import repository as repo
print('fairness:', repo.get_fairness_setting())
print('kill switch:', repo.get_kill_switch())
for t in ('acme','globex','initech'):
    print(t, 'memory events:', len(asyncio.run(recall_tenant_memory(t))))
"
curl -s http://localhost:3001/payments
```

Expected: fairness enabled, kill switch disarmed, zero memory events per tenant, `[]` from Mockoon.

- [ ] **Step 4: Rewrite `mocks/README.md`** (this is E8.1 T1)

Replace the stale stub with the real inventory: each service, its routes, which activity calls it (`payments` ← `app/activities/payment.py`; `disputes/:id/evidence` and `dispute-responses` ← `app/activities/dispute.py`), the reset routes, and how to run it. Confirm by grepping every `mockoon_base_url` call site that no other external service exists — do not invent mocks for services nothing calls.

- [ ] **Step 5: Commit**

```bash
git add scripts/reset.py mocks/ Makefile docs/BACKLOG.md
git commit -m "feat: full demo reset — memory, mocks, fairness, kill switch, ramp"
```

---

## Task 6: Demo seed script

**Files:**
- Create: `scripts/seed_demo.py`
- Modify: `Makefile`

- [ ] **Step 1: Write it**

```python
"""Seed the demo's starting state (beat 0's memory moment).

Runs two real invoice-exception sessions so the tenant's AgentCore Memory
holds genuine, relevant prior decisions. Beat 0 then opens by recalling real
history rather than fabricated rows — CLAUDE.md §7 forbids fake data on
screen, and a real recall is a better moment anyway.

Run after `scripts/reset.py --yes`, never instead of it.
"""

import asyncio

from app.sessions import start_session
from app.temporal_client import connect

TENANT = "acme"
AGENT = "invoice-exception"
PRIOR_SESSIONS = [
    "Invoice INV-6610 from Initech Supply for 780.00 USD failed purchase-order "
    "matching: quantity billed is 12, purchase order says 9. Decide and act.",
    "Invoice INV-6742 from Globex Retail for 95.00 USD failed purchase-order "
    "matching: unit price is 3.00 USD above the PO. Decide and act.",
]


async def main() -> int:
    client = await connect()
    for prompt in PRIOR_SESSIONS:
        job_id = await start_session(AGENT, TENANT, prompt)
        print(f"seeding {job_id}...")
        await client.get_workflow_handle(job_id).result()
        print("  done")

    from app.activities.memory import recall_tenant_memory

    notes = await recall_tenant_memory(TENANT)
    print(f"\n{TENANT} now has {len(notes)} memory events:")
    for note in notes:
        print(f"  {note[:120]}")
    assert len(notes) >= 2, "seeding did not produce recallable memory"
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Run reset then seed, and confirm the recall reads well**

```bash
uv run python -m scripts.reset --yes
uv run python -m scripts.seed_demo
```

Expected: two completed sessions and recall showing two real invoice decisions. **Read the output as an audience member** — if the summaries are not legible and clearly invoice-related, adjust the prompts and re-run. This is the text that opens the demo.

> The first seeded session runs against empty memory and the second recalls the first — that is correct and worth seeing, since it is the mechanism beat 0 demonstrates.

- [ ] **Step 3: Add targets and commit**

```makefile
demo-reset:
	uv run python -m scripts.reset --yes

demo-seed:
	uv run python -m scripts.seed_demo

demo-prepare: demo-reset demo-seed
```

```bash
git add scripts/seed_demo.py Makefile
git commit -m "feat: seed real prior sessions for beat 0's memory recall"
```

---

## Task 7: Preflight

**Files:**
- Create: `scripts/preflight.py`
- Modify: `Makefile`

- [ ] **Step 1: Write it**

Read-only only — no mutations. Each check prints `PASS`/`FAIL` plus its own remediation, and the script exits non-zero if any fail. Cover, in order:

1. `.env` — every required setting non-empty.
2. **AWS credentials valid, and remaining SSO token lifetime.** Read the expiry from the SSO cache under `~/.aws/sso/cache/`. An expired token kills every beat at once — it happened during E6.1 and again on 2026-09-02. Warn below 2 hours.
3. DynamoDB table `ACTIVE`; tenants present; agent packages published.
4. Temporal Cloud reachable; `TenantId` search attribute registered.
5. **Worker deployment current version equals `BUILD_ID`** — the CLAUDE.md §4 trap, where workflows sit at `WorkflowTaskScheduled` forever with no error.
6. Task-queue poller count > 0, and warn if > 1 (a leftover worker has broken verification runs before — E7.1).
7. Mockoon answering `payments`, `disputes/:id/evidence`, `dispute-responses`.
8. Bedrock model ids resolvable; Guardrail `READY`.
9. AgentCore Memory `ACTIVE`, Gateway `READY`, Runtime `READY`.
10. S3 bucket reachable.
11. API `/api/metrics/status` and the UI dev server responding.
12. **Demo-state checks:** fairness ON, kill switch disarmed, no active ramp, payment counter 0, seeded memory present for the beat-0 tenant.

- [ ] **Step 2: Run against a healthy stack**

Run: `uv run python -m scripts.preflight`
Expected: every check PASS, exit 0.

- [ ] **Step 3: Prove it catches a real fault**

Stop Mockoon, re-run, confirm that check FAILs with a useful remediation and the exit code is non-zero. Restart Mockoon.

- [ ] **Step 4: Add the target and commit**

```makefile
preflight:
	uv run python -m scripts.preflight
```

```bash
git add scripts/preflight.py Makefile
git commit -m "feat: preflight readiness check for the demo stack"
```

---

## Task 8: Rehearse and write the demo script (E8.1 T2)

**Files:**
- Modify: `docs/DEMO_SCRIPT.md`

- [ ] **Step 1: Prepare and rehearse once, timed**

```bash
make preflight && make demo-prepare
```

Run the full 12-beat sequence from the spec with a stopwatch. Record the **actual** duration of each beat and every place the stack hesitated. Do not write the script first and rehearse against it — write it from what actually happened.

- [ ] **Step 2: Verify the two console moments**

Confirm whether the AgentCore Memory console renders individual events. The spec marks this **unverified**; the `ListEvents` API returns them but console rendering is unconfirmed. If it does not render them, delete that moment from the script rather than narrating something that will not appear.

- [ ] **Step 3: Write the script**

Click-by-click with real timings, each beat mapped to what it proves, plus: the exact prompts to paste (especially the guardrail-triggering one, which took five attempts to find — see `docs/DECISIONS.md`, 2026-09-01), the pre-flight and reset bookends, and an abort branch per beat naming what to say and where to skip to.

- [ ] **Step 4: Rehearse again against the written script, then commit**

```bash
git add docs/DEMO_SCRIPT.md
git commit -m "docs: the 12-minute AgentCore-first run of show"
```

---

## Task 9: Recorded fallback (E8.1 T3)

**Files:**
- Create: `ui/e2e/demo-run.spec.ts`
- Modify: `ui/playwright.config.ts`, `Makefile`, `.gitignore`

- [ ] **Step 1: Enable video capture**

In `ui/playwright.config.ts`, add `use: { video: "on", viewport: { width: 1920, height: 1080 } }` for this project.

- [ ] **Step 2: Write the spec** driving the real UI through the demo script's beats in order, with the same prompts. Real data throughout — no mocks (`docs/DECISIONS.md`, 2026-08-21).

- [ ] **Step 3: Produce a recording**

```makefile
demo-recording:
	cd ui && npx playwright test e2e/demo-run.spec.ts
```

Run it, then **watch the video end to end** and confirm it is legible and shows real data.

- [ ] **Step 4: Commit** the spec and config; gitignore the video artefacts.

---

## Task 10: Runbook and bookkeeping

**Files:**
- Create: `docs/RUNBOOK.md`
- Modify: `README.md`, `docs/BACKLOG.md`

- [ ] **Step 1: Write `docs/RUNBOOK.md`** — prerequisites; the four terminals in order; `make preflight`; `make demo-prepare`; a failure-mode table (expired SSO and the worker restart it forces, worker not current for `BUILD_ID`, Mockoon not bound to 3001, stale poller inflating the worker count for minutes after a kill); and an explicit statement of how the laptop topology differs from E9's deployed one (worker local not Lambda, API and Mockoon local not App Runner, Vite not Amplify).

- [ ] **Step 2: Add Story E8.2 to `docs/BACKLOG.md`** covering preflight (Task 7) and the runbook, and tick every task this plan completed.

- [ ] **Step 3: Update the README's "Known gaps" section.** It is the public mirror of the backlog — if a gap closed here and not there, the README starts lying to people who fork the repo.

- [ ] **Step 4: Final regression**

```bash
make ci && make replay && make verify-agents && make verify-payment \
  && make verify-kill-resume && make verify-flood-health && make e2e
```

- [ ] **Step 5: Commit**

```bash
git add docs/RUNBOOK.md README.md docs/BACKLOG.md
git commit -m "docs: laptop-hosted runbook and backlog reconciliation"
```

---

## Deferred, deliberately

- **E3.2 T4's paired 50+4+4 fairness rehearsal** — blocked on Task 3. Run it immediately after Task 3 passes; until then proof 1's numbers include event-loop starvation.
- **E5.2 T3's physical ten-metre check** — needs a projector and a room.
- **E6.2 T5's ten presenter-driven kill/resume rehearsals** — three clean automated runs exist.
- **The eight-fold duplicated boto3 session block** — wants a shared `app/aws.py` helper. Noted 2026-09-02, not bundled into any task here.
- **E9 and E10.1 T5** — explicitly out of scope.
