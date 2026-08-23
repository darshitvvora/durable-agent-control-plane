"""DynamoDB repository — single-table design, see docs/DECISIONS.md for the key schema.

The only module that knows about pk/sk/gsi1. Everything else works with the
plain domain models in models.py.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings
from app.registry.models import (
    AgentPackage,
    FairnessSetting,
    IdempotencyRecord,
    Job,
    JobResult,
    Tenant,
)

IDEMPOTENCY_TTL_DAYS = 30


@lru_cache
def _table() -> Any:
    settings = get_settings()
    session = (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    return session.resource("dynamodb").Table(settings.dynamodb_table_name)


def _to_decimal(value: Any) -> Any:
    """DynamoDB rejects floats. Recurse — nested dicts/lists carry them too."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def _from_decimal(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


def _decode(item: dict) -> dict:
    return {k: _from_decimal(v) for k, v in item.items()}


# --- Tenant ---


def _tenant_key(tenant_id: str) -> dict:
    return {"pk": f"TENANT#{tenant_id}", "sk": f"TENANT#{tenant_id}"}


def put_tenant(tenant: Tenant) -> None:
    item = _tenant_key(tenant.tenant_id) | {
        k: _to_decimal(v) for k, v in tenant.model_dump().items()
    }
    _table().put_item(Item=item)


def get_tenant(tenant_id: str) -> Tenant | None:
    response = _table().get_item(Key=_tenant_key(tenant_id))
    item = response.get("Item")
    return Tenant(**_decode(item)) if item else None


def list_tenants() -> list[Tenant]:
    # Scan is fine here — tenant count is small and bounded, unlike jobs.
    response = _table().scan(
        FilterExpression="begins_with(pk, :prefix)",
        ExpressionAttributeValues={":prefix": "TENANT#"},
    )
    return [Tenant(**_decode(item)) for item in response.get("Items", [])]


def delete_tenant(tenant_id: str) -> None:
    _table().delete_item(Key=_tenant_key(tenant_id))


def install_agent(tenant_id: str, agent_id: str) -> Tenant:
    """Make a published agent available to a tenant. Idempotent."""
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise ValueError(f"unknown tenant {tenant_id!r} — run `dos tenant add` first")
    if not list_agent_package_versions(agent_id):
        raise ValueError(f"unknown agent {agent_id!r} — publish it first")
    if agent_id not in tenant.installed_agent_ids:
        tenant.installed_agent_ids = [*tenant.installed_agent_ids, agent_id]
        put_tenant(tenant)
    return tenant


def uninstall_agent(tenant_id: str, agent_id: str) -> Tenant:
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise ValueError(f"unknown tenant {tenant_id!r} — run `dos tenant add` first")
    if agent_id in tenant.installed_agent_ids:
        tenant.installed_agent_ids = [a for a in tenant.installed_agent_ids if a != agent_id]
        put_tenant(tenant)
    return tenant


# --- AgentPackage ---


def _agent_package_key(agent_id: str, version: int) -> dict:
    return {"pk": f"AGENT#{agent_id}", "sk": f"VERSION#{version}"}


def put_agent_package(package: AgentPackage) -> None:
    item = _agent_package_key(package.agent_id, package.version) | {
        k: _to_decimal(v) for k, v in package.model_dump().items()
    }
    _table().put_item(Item=item)


def get_agent_package(agent_id: str, version: int) -> AgentPackage | None:
    response = _table().get_item(Key=_agent_package_key(agent_id, version))
    item = response.get("Item")
    return AgentPackage(**_decode(item)) if item else None


def list_agent_package_versions(agent_id: str) -> list[AgentPackage]:
    response = _table().query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": f"AGENT#{agent_id}"},
    )
    return [AgentPackage(**_decode(item)) for item in response.get("Items", [])]


def delete_agent_package(agent_id: str, version: int) -> None:
    _table().delete_item(Key=_agent_package_key(agent_id, version))


def list_agent_packages() -> list[AgentPackage]:
    """Every version of every published agent — same scan pattern as list_tenants()."""
    response = _table().scan(
        FilterExpression="begins_with(pk, :prefix)",
        ExpressionAttributeValues={":prefix": "AGENT#"},
    )
    return [AgentPackage(**_decode(item)) for item in response.get("Items", [])]


# --- Job ---


def _job_key(job_id: str) -> dict:
    return {"pk": f"JOB#{job_id}", "sk": f"JOB#{job_id}"}


def put_job(job: Job) -> None:
    item = (
        _job_key(job.job_id)
        | {k: _to_decimal(v) for k, v in job.model_dump().items()}
        | {"gsi1_pk": job.tenant_id, "gsi1_sk": job.created_at}
    )
    _table().put_item(Item=item)


def _job_from_item(item: dict) -> Job:
    return Job(**_decode({k: v for k, v in item.items() if k not in ("gsi1_pk", "gsi1_sk")}))


def get_job(job_id: str) -> Job | None:
    response = _table().get_item(Key=_job_key(job_id))
    item = response.get("Item")
    return _job_from_item(item) if item else None


def list_jobs_for_tenant(tenant_id: str, limit: int = 100) -> list[Job]:
    response = _table().query(
        IndexName="gsi1",
        KeyConditionExpression="gsi1_pk = :tenant_id",
        ExpressionAttributeValues={":tenant_id": tenant_id},
        ScanIndexForward=False,
        Limit=limit,
    )
    return [_job_from_item(item) for item in response.get("Items", [])]


def delete_job(job_id: str) -> None:
    _table().delete_item(Key=_job_key(job_id))


def mark_job_started(job_id: str, started_at: str) -> None:
    """Stamp when a job actually began executing, for the E3.2 wait-time metric.

    A no-op if no Job row exists for this id — not every workflow-starting call
    site tracks one (`dos agent test`, the verify scripts), and this must never
    create a stray partial row for them.
    """
    try:
        _table().update_item(
            Key=_job_key(job_id),
            UpdateExpression="SET started_at = :started_at, #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":started_at": started_at, ":status": "running"},
            ConditionExpression="attribute_exists(pk)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return
        raise


# --- JobResult ---


def _job_result_key(job_id: str) -> dict:
    return {"pk": f"JOB#{job_id}", "sk": "RESULT"}


def put_job_result(result: JobResult) -> None:
    item = _job_result_key(result.job_id) | {
        k: _to_decimal(v) for k, v in result.model_dump().items()
    }
    _table().put_item(Item=item)


def get_job_result(job_id: str) -> JobResult | None:
    response = _table().get_item(Key=_job_result_key(job_id))
    item = response.get("Item")
    return JobResult(**_decode(item)) if item else None


def delete_job_result(job_id: str) -> None:
    _table().delete_item(Key=_job_result_key(job_id))


# --- IdempotencyRecord — proof 3's mechanism ---


def _idempotency_key(key: str) -> dict:
    return {"pk": f"IDEMPOTENCY#{key}", "sk": f"IDEMPOTENCY#{key}"}


def claim_idempotency_key(key: str, created_at: str) -> bool:
    """Atomically claim a key before performing a side effect. False if already claimed."""
    expires_at = datetime.fromisoformat(created_at) + timedelta(days=IDEMPOTENCY_TTL_DAYS)
    ttl = int(expires_at.timestamp())
    item = _idempotency_key(key) | {
        "key": key,
        "status": "pending",
        "result": None,
        "created_at": created_at,
        "ttl": ttl,
    }
    try:
        _table().put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def complete_idempotency_record(key: str, result: dict) -> None:
    _table().update_item(
        Key=_idempotency_key(key),
        UpdateExpression="SET #status = :status, #result = :result",
        ExpressionAttributeNames={"#status": "status", "#result": "result"},
        ExpressionAttributeValues={":status": "completed", ":result": _to_decimal(result)},
    )


def get_idempotency_record(key: str) -> IdempotencyRecord | None:
    response = _table().get_item(Key=_idempotency_key(key))
    item = response.get("Item")
    return IdempotencyRecord(**_decode(item)) if item else None


def delete_idempotency_record(key: str) -> None:
    _table().delete_item(Key=_idempotency_key(key))


# --- FairnessSetting — the demo's fairness on/off toggle ---

_FAIRNESS_KEY = {"pk": "SETTINGS#fairness", "sk": "SETTINGS#fairness"}


def get_fairness_setting() -> FairnessSetting:
    response = _table().get_item(Key=_FAIRNESS_KEY)
    item = response.get("Item")
    return FairnessSetting(**_decode(item)) if item else FairnessSetting()


def put_fairness_setting(setting: FairnessSetting) -> None:
    item = _FAIRNESS_KEY | {k: _to_decimal(v) for k, v in setting.model_dump().items()}
    _table().put_item(Item=item)
