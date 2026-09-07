from fastapi import APIRouter, HTTPException, Request
from temporalio.service import RPCError

from app.registry import repository as repo
from app.registry.models import Job
from app.sessions import start_session
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import SessionState

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(tenant_id: str) -> list[Job]:
    return repo.list_jobs_for_tenant(tenant_id)


@router.post("")
async def create_job(agent_id: str, tenant_id: str, prompt: str) -> dict:
    """Start one agent session. The demo's only way to run a non-flood agent
    from the browser — beats 0 and 2 depend on it."""
    try:
        job_id = await start_session(agent_id, tenant_id, prompt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"job_id": job_id}


@router.get("/{job_id}")
def get_job(job_id: str) -> Job:
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id!r}")
    return job


@router.post("/{job_id}/approval")
async def submit_approval(request: Request, job_id: str, interrupt_id: str, decision: str) -> dict:
    """Answer a paused tool call. "approve" lets it run; any other text is
    treated as a denial and handed back to the model as the reason.
    """
    client = request.app.state.temporal_client
    handle = client.get_workflow_handle(job_id)
    try:
        await handle.signal(AgentJobWorkflow.submit_approval, args=[interrupt_id, decision])
    except RPCError as e:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id!r}") from e
    return {"job_id": job_id, "interrupt_id": interrupt_id, "decision": decision}


@router.get("/{job_id}/state")
async def get_job_state(request: Request, job_id: str) -> SessionState:
    """Polled fallback for the session pane when the event stream is
    unavailable (E4.2 T3). Real Temporal calls only — describe() plus the
    workflow's own query.
    """
    client = request.app.state.temporal_client
    handle = client.get_workflow_handle(job_id)
    try:
        desc = await handle.describe()
    except RPCError as e:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id!r}") from e

    info = desc.raw_description.workflow_execution_info
    version = info.versioning_info.deployment_version
    worker_version = (
        f"{version.deployment_name}:{version.build_id}" if version.build_id else None
    )

    # Only a running workflow can answer a query; a closed one has no worker to
    # serve it. Reporting status without the approval detail is the honest
    # answer there, not an error.
    pending = None
    if desc.status is not None and desc.status.name == "RUNNING":
        pending = await handle.query(AgentJobWorkflow.pending_approval)

    return SessionState(
        job_id=job_id,
        status=desc.status.name if desc.status is not None else "UNKNOWN",
        worker_version=worker_version,
        pending_approval=pending,
        history_size_bytes=info.history_size_bytes,
        external_payload_size_bytes=info.external_payload_size_bytes,
        external_payload_count=info.external_payload_count,
    )
