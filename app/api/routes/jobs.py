from fastapi import APIRouter, HTTPException

from app.registry import repository as repo
from app.registry.models import Job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(tenant_id: str) -> list[Job]:
    return repo.list_jobs_for_tenant(tenant_id)


@router.get("/{job_id}")
def get_job(job_id: str) -> Job:
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id!r}")
    return job
