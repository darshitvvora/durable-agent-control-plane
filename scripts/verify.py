"""Verify the registry against the real DynamoDB table. Run with `make verify`.

Writes a throwaway tenant/agent/job/result/idempotency record under a
`verify-` prefix, reads each back, then deletes them.
"""

from datetime import UTC, datetime

from app.config import get_settings
from app.registry import repository as repo
from app.registry.models import (
    AgentPackage,
    AgentTier,
    Job,
    JobResult,
    JobStatus,
    Tenant,
    TenantTier,
)

PREFIX = "verify-"


def main() -> None:
    settings = get_settings()
    now = datetime.now(UTC).isoformat()
    tenant_id = f"{PREFIX}tenant"
    agent_id = f"{PREFIX}agent"
    job_id = f"{PREFIX}job"
    idem_key = f"{PREFIX}payment"

    print(f"table={settings.dynamodb_table_name} region={settings.aws_region}")

    tenant = Tenant(
        tenant_id=tenant_id,
        name="Verify Tenant",
        tier=TenantTier.PLATINUM,
        priority_key=1,
        fairness_weight=2.5,
        memory_namespace=f"{tenant_id}-mem",
        s3_prefix=f"{tenant_id}/",
        installed_agent_ids=[agent_id],
    )
    repo.put_tenant(tenant)
    assert repo.get_tenant(tenant_id) == tenant, "tenant round-trip failed"
    print("  tenant round-trip           ok")

    package = AgentPackage(
        agent_id=agent_id,
        version=1,
        name="Verify Agent",
        tier=AgentTier.CUSTOM_TOOLS,
        model="bedrock-claude",
        system_prompt="Verification placeholder.",
    )
    repo.put_agent_package(package)
    assert repo.get_agent_package(agent_id, 1) == package, "agent package round-trip failed"
    print("  agent package round-trip    ok")

    job = Job(
        job_id=job_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        workflow_id=f"wf-{job_id}",
        status=JobStatus.RUNNING,
        priority_key=1,
        created_at=now,
    )
    repo.put_job(job)
    assert repo.get_job(job_id) == job, "job round-trip failed"
    assert job_id in [j.job_id for j in repo.list_jobs_for_tenant(tenant_id)], "gsi1 query failed"
    print("  job round-trip + gsi1 query ok")

    result = JobResult(job_id=job_id, output={"decision": "hold"}, completed_at=now)
    repo.put_job_result(result)
    assert repo.get_job_result(job_id) == result, "job result round-trip failed"
    print("  job result round-trip       ok")

    assert repo.claim_idempotency_key(idem_key, now) is True, "first claim should succeed"
    assert repo.claim_idempotency_key(idem_key, now) is False, "second claim must be rejected"
    repo.complete_idempotency_record(idem_key, {"confirmation": "conf-123"})
    record = repo.get_idempotency_record(idem_key)
    assert record is not None and record.status == "completed", "idempotency completion failed"
    print("  idempotency claim is atomic ok")

    repo.delete_tenant(tenant_id)
    repo.delete_agent_package(agent_id, 1)
    repo.delete_job(job_id)
    repo.delete_job_result(job_id)
    repo.delete_idempotency_record(idem_key)
    assert repo.get_tenant(tenant_id) is None, "cleanup failed"
    print("  cleanup                     ok")

    print("all checks passed")


if __name__ == "__main__":
    main()
