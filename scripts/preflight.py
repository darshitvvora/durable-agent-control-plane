"""Preflight — the check a presenter runs minutes before walking on stage
(E8.2 T4). Run with `make preflight`.

Eleven external dependencies (Temporal Cloud, AWS credentials, DynamoDB,
Bedrock, four AgentCore services, S3, Mockoon, the API/UI dev servers) plus
the demo's own runtime state (fairness, kill switch, ramp, payment counter,
seeded memory) all have to be healthy at once for a demo to run cleanly. Any
one of them failing silently costs a debugging detour mid-rehearsal — this
script's job is to turn "something is wrong somewhere" into "this specific
thing is wrong, here is the fix," in one run, before an audience is watching.

**Every check here is READ-ONLY.** Nothing in this module calls a mutating
AWS/Temporal/Mockoon API — only Get*/List*/Describe*/count_workflows/GET. A
nervous presenter runs this seconds before going on; it must be structurally
incapable of changing state.

**Checks are independent.** Each check is wrapped so that an exception inside
it becomes a FAIL for that check alone — one dependency being down must never
stop the presenter from seeing the whole picture in a single run. Where two
checks would otherwise both issue the same underlying RPC (Worker Deployment
routing status, used by both the version-pin check and the ramp check), the
result is fetched once and reused — reporting is still fully independent,
this only avoids a redundant network round trip.

Tenants and agent packages are read from the registry, never hardcoded
(CLAUDE.md §11).
"""

import asyncio
import configparser
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from temporalio.client import Client

from app.activities.memory import recall_tenant_memory
from app.config import Settings, get_settings
from app.demo import payment_count
from app.registry import deployment as deployment_registry
from app.registry import fleet
from app.registry import repository as repo
from app.registry.deployment import RoutingStatus
from app.temporal_client import bedrock_session, connect

# Beat 0 opens on this tenant's memory recall (scripts/seed_demo.py). Not
# read from the registry because it names a specific demo beat, not "any
# tenant" — same reason scripts/seed_demo.py hardcodes it.
BEAT_0_TENANT = "acme"
SEEDED_MEMORY_MINIMUM = 2

# Mockoon's payments bucket is process-local, in-memory state — it does NOT
# survive a Mockoon restart, unlike AgentCore Memory (AWS-durable, survives
# everything). scripts/seed_demo.py's INV-6742 scenario settles and calls
# issue_payment for real, so the count reads 1 immediately after a
# `make demo-prepare` run — but empirically (2026-09-07, see docs/DECISIONS.md)
# a Mockoon restart between seeding and going on stage zeroes it again while
# leaving seeded memory untouched, so 0 (matching this check's original
# wording) is the only value stable across that restart, not 1.
EXPECTED_SEEDED_PAYMENT_COUNT = 0

SSO_WARN_THRESHOLD = timedelta(hours=2)

UI_DEV_SERVER_URL = "http://localhost:5173/"  # Vite's default; no .env setting names it


class Status(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str
    remediation: str | None = None

    def render(self) -> str:
        line = f"[{self.status.value:4}] {self.name} — {self.detail}"
        if self.status != Status.PASS and self.remediation:
            line += f"\n       fix: {self.remediation}"
        return line


async def _guarded(name: str, run: Any) -> CheckResult:
    """Run one check; any exception it raises becomes a FAIL for it alone.

    This is the mechanism behind "checks are independent" — a check that
    forgets to catch its own AWS/Temporal/HTTP error still can't take the
    rest of the run down with it.
    """
    try:
        return await run
    except Exception as e:  # noqa: BLE001 - a check's own bug must not abort the run
        return CheckResult(
            name,
            Status.FAIL,
            f"unexpected error: {e!r}",
            "this check crashed rather than reporting cleanly — investigate the "
            "traceback above/rerun with `uv run python -m scripts.preflight` "
            "directly to see it",
        )


def _credentials_fail(name: str, e: BotoCoreError) -> CheckResult:
    """A boto3 call died on the credential chain itself (expired/missing SSO
    token, no profile, etc.) rather than on anything specific to this
    check — `BotoCoreError` is the shared base for those, distinct from
    `ClientError` (a request the service itself rejected). Point back at
    the "AWS credentials" check instead of repeating its diagnosis in every
    AWS-backed check: with one shared credential chain, an expired token
    manifests here identically no matter which check trips over it first.
    """
    return CheckResult(
        name,
        Status.FAIL,
        f"AWS credential error: {e}",
        "this is downstream of the 'AWS credentials' check above — fix that "
        "first (most likely `aws sso login`, then restart the worker)",
    )


# --- 1. .env ---------------------------------------------------------------

# Settings fields that are typed Optional in app/config.py but are load
# bearing for THIS demo (an AgentCore-first path, per CLAUDE.md §1) — a
# fork without AgentCore provisioned yet is legitimately allowed to leave
# these unset (every activity that reads them no-ops cleanly), so they are
# not pydantic-`required`, but a presenter about to run the demo needs every
# one of them filled in. bedrock_api_key is excluded: grep confirms nothing
# in app/, cli/, or scripts/ reads it today, so flagging it empty would be a
# false alarm, not an actionable fix.
DEMO_CRITICAL_OPTIONAL_FIELDS = (
    "bedrock_guardrail_id",
    "bedrock_guardrail_version",
    "agentcore_gateway_url",
    "agentcore_memory_id",
    "agentcore_runtime_endpoint",
    "s3_bucket_name",
)


async def check_env() -> CheckResult:
    try:
        settings = Settings()
    except Exception as e:  # noqa: BLE001 - pydantic ValidationError, reported verbatim
        return CheckResult(
            ".env",
            Status.FAIL,
            f"required settings missing or invalid: {e}",
            "fill in the missing keys in .env — copy the header comments in .env "
            "for the full list of keys this app reads",
        )
    missing = [f for f in DEMO_CRITICAL_OPTIONAL_FIELDS if not getattr(settings, f)]
    if missing:
        return CheckResult(
            ".env",
            Status.FAIL,
            f"empty but required for this demo: {', '.join(k.upper() for k in missing)}",
            "set these in .env — every AgentCore-first demo beat depends on all of "
            "them being configured, even though app/config.py allows them to be "
            "unset for a fork that hasn't provisioned AgentCore yet",
        )
    return CheckResult(".env", Status.PASS, "every required and demo-critical setting is set")


# --- 2. AWS credentials + SSO token lifetime -------------------------------


def _sso_session_name(profile_name: str) -> str | None:
    """The `sso_session` this profile points at, from ~/.aws/config. None if
    the profile doesn't exist or isn't SSO-based."""
    config = configparser.ConfigParser()
    config.read(Path.home() / ".aws" / "config")
    section = f"profile {profile_name}"
    if section not in config:
        return None
    return config[section].get("sso_session")


def _sso_token_expiry(session_name: str) -> datetime | None:
    """`expiresAt` from the SSO token cache for this session, or None if no
    cache file exists for it yet (never logged in) — confirmed on this
    machine by hashing the real session name and matching the actual cache
    filename under ~/.aws/sso/cache/."""
    # noqa: S324 - filename lookup, not a security hash
    digest = hashlib.sha1(session_name.encode("utf-8")).hexdigest()
    cache_file = Path.home() / ".aws" / "sso" / "cache" / f"{digest}.json"
    if not cache_file.exists():
        return None
    data = json.loads(cache_file.read_text())
    expires_at = data.get("expiresAt")
    if not expires_at:
        return None
    return datetime.fromisoformat(expires_at)


async def check_aws_credentials(settings: Settings) -> CheckResult:
    """The single most important check here: an expired SSO token kills
    Bedrock, all four AgentCore services, DynamoDB and the sandbox at once,
    because every boto3 client in this app shares one credential chain. It
    expired three times in one day of development."""
    session = bedrock_session()

    def _identity() -> dict:
        return session.client("sts").get_caller_identity()

    restart_note = (
        "run `aws sso login`"
        + (f" --profile {settings.aws_profile}" if settings.aws_profile else "")
        + ", then RESTART THE WORKER (`make worker`) — this app's boto3 sessions "
        "are `lru_cache`d, so a worker that was already running keeps using the "
        "dead credentials even after you re-login. This single token backs "
        "Bedrock, all four AgentCore services, DynamoDB and the sandbox, so "
        "letting it expire kills every beat of the demo simultaneously."
    )

    try:
        identity = await asyncio.to_thread(_identity)
    except Exception as e:  # noqa: BLE001 - ClientError/NoCredentialsError, reported the same way
        return CheckResult(
            "AWS credentials", Status.FAIL, f"get_caller_identity failed: {e}", restart_note
        )

    detail = f"account={identity.get('Account')} arn={identity.get('Arn')}"

    if not settings.aws_profile:
        # No profile — e.g. Lambda's execution role. Nothing SSO-shaped to check.
        return CheckResult(
            "AWS credentials",
            Status.PASS,
            detail + " (AWS_PROFILE unset — not an SSO profile, skipping "
            "token-lifetime check)",
        )

    session_name = _sso_session_name(settings.aws_profile)
    if session_name is None:
        return CheckResult(
            "AWS credentials",
            Status.WARN,
            detail + f"; profile {settings.aws_profile!r} in ~/.aws/config has no "
            "sso_session — cannot check remaining token lifetime",
            "if this profile is SSO-based, confirm it has an `sso_session = ...` line",
        )

    expires_at = await asyncio.to_thread(_sso_token_expiry, session_name)
    if expires_at is None:
        return CheckResult(
            "AWS credentials",
            Status.WARN,
            detail + f"; no SSO token cache found for session {session_name!r} — "
            "cannot check remaining lifetime",
            "run `aws sso login`"
            + (f" --profile {settings.aws_profile}" if settings.aws_profile else ""),
        )

    remaining = expires_at - datetime.now(UTC)
    remaining_str = str(remaining).split(".")[0]
    if remaining <= timedelta(0):
        return CheckResult(
            "AWS credentials",
            Status.FAIL,
            detail + f"; SSO token EXPIRED at {expires_at.isoformat()}",
            restart_note,
        )
    if remaining < SSO_WARN_THRESHOLD:
        return CheckResult(
            "AWS credentials",
            Status.WARN,
            detail + f"; SSO token expires in {remaining_str} "
            f"({expires_at.isoformat()}) — below the {SSO_WARN_THRESHOLD} safety margin",
            restart_note,
        )
    return CheckResult(
        "AWS credentials",
        Status.PASS,
        detail + f"; SSO token valid for {remaining_str} more ({expires_at.isoformat()})",
    )


# --- 3. DynamoDB ------------------------------------------------------------


async def check_dynamodb(settings: Settings) -> CheckResult:
    session = bedrock_session()

    def _table_status() -> str:
        table = session.client("dynamodb").describe_table(TableName=settings.dynamodb_table_name)
        return table["Table"]["TableStatus"]

    try:
        table_status = await asyncio.to_thread(_table_status)
        tenants = await asyncio.to_thread(repo.list_tenants)
        packages = await asyncio.to_thread(repo.list_agent_packages)
    except BotoCoreError as e:
        return _credentials_fail("DynamoDB", e)
    except ClientError as e:
        return CheckResult(
            "DynamoDB",
            Status.FAIL,
            f"describe_table({settings.dynamodb_table_name!r}) failed: {e}",
            "check DYNAMODB_TABLE_NAME in .env, or check docs/AWS_SETUP.md",
        )
    if table_status != "ACTIVE":
        return CheckResult(
            "DynamoDB",
            Status.FAIL,
            f"table {settings.dynamodb_table_name!r} status is {table_status}, not ACTIVE",
            "wait for the table to finish provisioning, or check docs/AWS_SETUP.md",
        )

    published_ids = sorted({p.agent_id for p in packages})

    if not tenants:
        return CheckResult(
            "DynamoDB",
            Status.FAIL,
            "table ACTIVE but no tenants registered",
            "run `make tenant-add` (or `dos tenant add`) for at least the demo's tenants",
        )
    if not packages:
        return CheckResult(
            "DynamoDB",
            Status.FAIL,
            "table ACTIVE, tenants present, but no agent packages published",
            "run `make agent-publish ID=<id>` for each demo agent",
        )
    return CheckResult(
        "DynamoDB",
        Status.PASS,
        f"table ACTIVE; {len(tenants)} tenant(s); agents published: {', '.join(published_ids)}",
    )


# --- 4/5/6/12(ramp) — Temporal Cloud -----------------------------------------


async def _connect_temporal() -> tuple[Client | None, Exception | None]:
    try:
        return await connect(), None
    except Exception as e:  # noqa: BLE001 - reported to every dependent check below
        return None, e


def _temporal_unreachable(name: str, error: Exception) -> CheckResult:
    return CheckResult(
        name,
        Status.FAIL,
        f"Temporal Cloud unreachable: {error!r}",
        "check TEMPORAL_ADDRESS/TEMPORAL_NAMESPACE/TEMPORAL_CLOUD_API_KEY in .env, "
        "and that the Temporal Cloud API key itself hasn't expired (independent "
        "of AWS SSO)",
    )


async def check_temporal_reachable(client: Client | None, error: Exception | None) -> CheckResult:
    if client is None:
        assert error is not None
        return _temporal_unreachable("Temporal Cloud", error)

    probe = await fleet.tenant_running(client, "preflight-probe-nonexistent-tenant")
    if probe is None:
        return CheckResult(
            "Temporal Cloud",
            Status.FAIL,
            "reachable, but the TenantId custom search attribute is not "
            "registered on this namespace",
            "add it — see docs/AWS_SETUP.md's 'TenantId custom search attribute' entry",
        )
    return CheckResult(
        "Temporal Cloud", Status.PASS, "reachable; TenantId search attribute registered"
    )


async def check_worker_version(
    client: Client | None,
    error: Exception | None,
    settings: Settings,
    routing: RoutingStatus | None,
) -> CheckResult:
    if client is None:
        assert error is not None
        return _temporal_unreachable("Worker deployment version", error)
    assert routing is not None
    expected = f"{deployment_registry.DEPLOYMENT_NAME}.{settings.build_id}"
    if routing.current_version != expected:
        return CheckResult(
            "Worker deployment version",
            Status.FAIL,
            f"current version is {routing.current_version!r}, expected {expected!r} "
            f"(BUILD_ID={settings.build_id})",
            "run `temporal worker deployment set-current-version --deployment-name "
            f"{deployment_registry.DEPLOYMENT_NAME} --build-id {settings.build_id}` — "
            "until this matches, new workflow starts get NO task and sit at "
            "WorkflowTaskScheduled forever with no error anywhere (CLAUDE.md §4)",
        )
    return CheckResult(
        "Worker deployment version", Status.PASS, f"current version is {routing.current_version}"
    )


async def check_poller_count(
    client: Client | None, error: Exception | None, settings: Settings
) -> CheckResult:
    if client is None:
        assert error is not None
        return _temporal_unreachable("Task queue pollers", error)
    count = await fleet.worker_count(client)
    if count == 0:
        return CheckResult(
            "Task queue pollers",
            Status.FAIL,
            f"no worker polling task queue {settings.task_queue!r}",
            "start `make worker`",
        )
    if count > 1:
        return CheckResult(
            "Task queue pollers",
            Status.WARN,
            f"{count} pollers seen on {settings.task_queue!r} — describe_task_queue "
            "reports recently-seen pollers, so a just-restarted worker can "
            "legitimately show a stale extra for a few minutes",
            "if this doesn't drop to 1 within ~5 minutes, check for a leftover "
            "worker process (E7.1)",
        )
    return CheckResult("Task queue pollers", Status.PASS, f"1 poller on {settings.task_queue!r}")


# --- 7. Mockoon --------------------------------------------------------------


async def check_mockoon(settings: Settings) -> CheckResult:
    routes = {
        "payments": "payments",
        "disputes/:id/evidence": "disputes/preflight-check/evidence",
        "dispute-responses": "dispute-responses",
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        failures = []
        for family, path in routes.items():
            try:
                response = await client.get(f"{settings.mockoon_base_url}/{path}")
                if response.status_code >= 400:
                    failures.append(f"{family} -> HTTP {response.status_code}")
            except httpx.HTTPError as e:
                return CheckResult(
                    "Mockoon",
                    Status.FAIL,
                    f"unreachable at {settings.mockoon_base_url}: {e}",
                    "start it: `mockoon-cli start --data mocks/payment-service.json "
                    "--port 3001` (or `make mockoon`)",
                )
    if failures:
        return CheckResult(
            "Mockoon",
            Status.FAIL,
            f"route family failing: {'; '.join(failures)}",
            "check mocks/payment-service.json for that route, and that the "
            "collection running is the current one",
        )
    return CheckResult(
        "Mockoon", Status.PASS, "payments, disputes/:id/evidence, dispute-responses all answering"
    )


# --- 8. Bedrock models + guardrail ------------------------------------------


async def check_bedrock_models(settings: Settings) -> CheckResult:
    session = bedrock_session()
    client = session.client("bedrock")

    def _resolve(model_id: str) -> str:
        return client.get_inference_profile(inferenceProfileIdentifier=model_id)["status"]

    try:
        claude_status = await asyncio.to_thread(_resolve, settings.bedrock_claude_model_id)
        nova_status = await asyncio.to_thread(_resolve, settings.bedrock_nova_model_id)
    except BotoCoreError as e:
        return _credentials_fail("Bedrock models", e)
    except ClientError as e:
        return CheckResult(
            "Bedrock models",
            Status.FAIL,
            f"could not resolve a configured model id: {e}",
            "check BEDROCK_CLAUDE_MODEL_ID/BEDROCK_NOVA_MODEL_ID in .env and that "
            "this account has Bedrock model access for them in this region",
        )
    if claude_status != "ACTIVE" or nova_status != "ACTIVE":
        return CheckResult(
            "Bedrock models",
            Status.WARN,
            f"claude={claude_status} nova={nova_status} — resolvable but not both ACTIVE",
        )
    return CheckResult(
        "Bedrock models", Status.PASS, "claude and nova inference profiles both ACTIVE"
    )


async def check_bedrock_guardrail(settings: Settings) -> CheckResult:
    if not settings.bedrock_guardrail_id or not settings.bedrock_guardrail_version:
        return CheckResult(
            "Bedrock guardrail",
            Status.WARN,
            "BEDROCK_GUARDRAIL_ID/VERSION unset — app/activities/guardrail.py "
            "no-ops cleanly, but the demo's guardrail-verdict beat has nothing to show",
        )
    session = bedrock_session()

    def _status() -> str:
        return session.client("bedrock").get_guardrail(
            guardrailIdentifier=settings.bedrock_guardrail_id,
            guardrailVersion=settings.bedrock_guardrail_version,
        )["status"]

    try:
        status = await asyncio.to_thread(_status)
    except BotoCoreError as e:
        return _credentials_fail("Bedrock guardrail", e)
    except ClientError as e:
        return CheckResult(
            "Bedrock guardrail",
            Status.FAIL,
            f"get_guardrail failed: {e}",
            "check BEDROCK_GUARDRAIL_ID/BEDROCK_GUARDRAIL_VERSION in .env",
        )
    if status != "READY":
        return CheckResult("Bedrock guardrail", Status.FAIL, f"status is {status}, not READY")
    return CheckResult("Bedrock guardrail", Status.PASS, "READY")


# --- 9. AgentCore Memory / Gateway / Runtime --------------------------------


async def check_agentcore_memory(settings: Settings) -> CheckResult:
    if not settings.agentcore_memory_id:
        return CheckResult(
            "AgentCore Memory",
            Status.WARN,
            "AGENTCORE_MEMORY_ID unset — memory recall/seed features no-op",
        )
    session = bedrock_session()

    def _status() -> str:
        client = session.client("bedrock-agentcore-control")
        return client.get_memory(memoryId=settings.agentcore_memory_id)["memory"]["status"]

    try:
        status = await asyncio.to_thread(_status)
    except BotoCoreError as e:
        return _credentials_fail("AgentCore Memory", e)
    except ClientError as e:
        return CheckResult(
            "AgentCore Memory",
            Status.FAIL,
            f"get_memory failed: {e}",
            "check AGENTCORE_MEMORY_ID in .env — it must be the exact value "
            "GetMemory expects (same value app/activities/memory.py uses)",
        )
    if status != "ACTIVE":
        return CheckResult("AgentCore Memory", Status.FAIL, f"status is {status}, not ACTIVE")
    return CheckResult("AgentCore Memory", Status.PASS, "ACTIVE")


async def check_agentcore_gateway(settings: Settings) -> CheckResult:
    """GetGateway wants a `gatewayIdentifier`, but .env holds a URL — parsing
    an id out of the URL would be guessing at a format AWS never promised
    (CLAUDE.md §11). Instead: ListGateways (read-only), GetGateway on each
    result (also read-only, since ListGateways' own items don't carry the
    URL) to find the row whose `gatewayUrl` matches, and read `status` off
    that row. Two distinct failure modes on purpose: no match at all means
    the config is stale or points at another account/region; a match with
    a non-READY status means the resource itself is unhealthy."""
    if not settings.agentcore_gateway_url:
        return CheckResult(
            "AgentCore Gateway",
            Status.WARN,
            "AGENTCORE_GATEWAY_URL unset — Gateway-backed tools are unavailable",
        )
    session = bedrock_session()
    client = session.client("bedrock-agentcore-control")

    def _find() -> dict | None:
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"nextToken": next_token} if next_token else {}
            page = client.list_gateways(**kwargs)
            for item in page.get("items", []):
                detail = client.get_gateway(gatewayIdentifier=item["gatewayId"])
                if detail.get("gatewayUrl") == settings.agentcore_gateway_url:
                    return detail
            next_token = page.get("nextToken")
            if not next_token:
                return None

    try:
        match = await asyncio.to_thread(_find)
    except BotoCoreError as e:
        return _credentials_fail("AgentCore Gateway", e)
    except ClientError as e:
        return CheckResult(
            "AgentCore Gateway", Status.FAIL, f"list_gateways/get_gateway failed: {e}"
        )
    if match is None:
        return CheckResult(
            "AgentCore Gateway",
            Status.FAIL,
            f"no gateway in this account/region has URL {settings.agentcore_gateway_url!r}",
            "AGENTCORE_GATEWAY_URL is stale or points at another account/region — "
            "check .env against `aws bedrock-agentcore-control list-gateways`",
        )
    if match["status"] != "READY":
        return CheckResult(
            "AgentCore Gateway",
            Status.FAIL,
            f"gateway {match['gatewayId']} status is {match['status']}, not READY",
        )
    return CheckResult("AgentCore Gateway", Status.PASS, f"gateway {match['gatewayId']} READY")


async def check_agentcore_runtime(settings: Settings) -> CheckResult:
    """Same ruling as the Gateway check, but ListAgentRuntimes' own items
    already carry both `agentRuntimeArn` and `status` — no per-row
    GetAgentRuntime call needed to compare against the configured ARN."""
    if not settings.agentcore_runtime_endpoint:
        return CheckResult(
            "AgentCore Runtime",
            Status.WARN,
            "AGENTCORE_RUNTIME_ENDPOINT unset — the hosted-agent lane is unavailable",
        )
    session = bedrock_session()
    client = session.client("bedrock-agentcore-control")

    def _find() -> dict | None:
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"nextToken": next_token} if next_token else {}
            page = client.list_agent_runtimes(**kwargs)
            for item in page.get("agentRuntimes", []):
                if item.get("agentRuntimeArn") == settings.agentcore_runtime_endpoint:
                    return item
            next_token = page.get("nextToken")
            if not next_token:
                return None

    try:
        match = await asyncio.to_thread(_find)
    except BotoCoreError as e:
        return _credentials_fail("AgentCore Runtime", e)
    except ClientError as e:
        return CheckResult(
            "AgentCore Runtime", Status.FAIL, f"list_agent_runtimes failed: {e}"
        )
    if match is None:
        return CheckResult(
            "AgentCore Runtime",
            Status.FAIL,
            f"no agent runtime in this account/region has ARN "
            f"{settings.agentcore_runtime_endpoint!r}",
            "AGENTCORE_RUNTIME_ENDPOINT is stale or points at another "
            "account/region — check .env against "
            "`aws bedrock-agentcore-control list-agent-runtimes`",
        )
    if match["status"] != "READY":
        return CheckResult(
            "AgentCore Runtime",
            Status.FAIL,
            f"runtime {match['agentRuntimeId']} status is {match['status']}, not READY",
        )
    return CheckResult(
        "AgentCore Runtime", Status.PASS, f"runtime {match['agentRuntimeId']} READY"
    )


# --- 10. S3 ------------------------------------------------------------------


async def check_s3(settings: Settings) -> CheckResult:
    if not settings.s3_bucket_name:
        return CheckResult(
            "S3 bucket", Status.WARN, "S3_BUCKET_NAME unset — External Storage stays disabled"
        )
    session = bedrock_session()

    def _head() -> None:
        session.client("s3").head_bucket(Bucket=settings.s3_bucket_name)

    try:
        await asyncio.to_thread(_head)
    except BotoCoreError as e:
        return _credentials_fail("S3 bucket", e)
    except ClientError as e:
        return CheckResult(
            "S3 bucket",
            Status.FAIL,
            f"head_bucket({settings.s3_bucket_name!r}) failed: {e}",
            "check S3_BUCKET_NAME in .env and that this account has access to it",
        )
    return CheckResult("S3 bucket", Status.PASS, f"{settings.s3_bucket_name} reachable")


# --- 11. API + UI dev servers ------------------------------------------------


async def check_api(settings: Settings) -> CheckResult:
    url = f"http://localhost:{settings.api_port}/api/metrics/status"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
        response.raise_for_status()
        response.json()
    except (httpx.HTTPError, ValueError) as e:
        return CheckResult("API", Status.FAIL, f"{url} failed: {e}", "start `make api`")
    return CheckResult("API", Status.PASS, f"{url} responding")


async def check_ui() -> CheckResult:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(UI_DEV_SERVER_URL)
        response.raise_for_status()
    except httpx.HTTPError as e:
        return CheckResult(
            "UI dev server",
            Status.FAIL,
            f"{UI_DEV_SERVER_URL} failed: {e}",
            "start `cd ui && npm run dev`",
        )
    return CheckResult("UI dev server", Status.PASS, f"{UI_DEV_SERVER_URL} responding")


# --- 12. Demo state ----------------------------------------------------------


async def check_demo_fairness() -> CheckResult:
    try:
        enabled = await asyncio.to_thread(lambda: repo.get_fairness_setting().enabled)
    except BotoCoreError as e:
        return _credentials_fail("Demo state: fairness", e)
    if not enabled:
        return CheckResult(
            "Demo state: fairness",
            Status.FAIL,
            "fairness is OFF",
            "run `make demo-fairness-on` (or `make demo-reset`)",
        )
    return CheckResult("Demo state: fairness", Status.PASS, "ON")


async def check_demo_kill_switch() -> CheckResult:
    try:
        armed = await asyncio.to_thread(lambda: repo.get_kill_switch().armed)
    except BotoCoreError as e:
        return _credentials_fail("Demo state: kill switch", e)
    if armed:
        return CheckResult(
            "Demo state: kill switch",
            Status.FAIL,
            "ARMED from a previous run",
            "run `make demo-reset` to disarm it before going on stage — an armed "
            "switch crashes the worker on the very next consequential tool call",
        )
    return CheckResult("Demo state: kill switch", Status.PASS, "disarmed")


async def check_demo_ramp(
    client: Client | None, error: Exception | None, routing: RoutingStatus | None
) -> CheckResult:
    if client is None:
        assert error is not None
        return _temporal_unreachable("Demo state: ramp", error)
    assert routing is not None
    if routing.ramping_version or routing.ramping_percentage:
        return CheckResult(
            "Demo state: ramp",
            Status.FAIL,
            f"active ramp to {routing.ramping_version!r} at {routing.ramping_percentage}%",
            "run `make demo-reset` (or `dos demo ramp --version <build> --percent "
            "100`) to clear it before starting proof 2 from a clean state",
        )
    return CheckResult("Demo state: ramp", Status.PASS, "no active ramp")


async def check_demo_payment_counter(settings: Settings) -> CheckResult:
    try:
        count = await payment_count()
    except httpx.HTTPError as e:
        return CheckResult(
            "Demo state: payment counter",
            Status.FAIL,
            f"could not read Mockoon's payments bucket: {e}",
            "check Mockoon is running (see the Mockoon check above)",
        )
    if count != EXPECTED_SEEDED_PAYMENT_COUNT:
        return CheckResult(
            "Demo state: payment counter",
            Status.FAIL,
            f"{count}, expected {EXPECTED_SEEDED_PAYMENT_COUNT}",
            "run `make demo-reset` (via `make demo-prepare`) to clear Mockoon's "
            "payments bucket before going on stage",
        )
    return CheckResult(
        "Demo state: payment counter", Status.PASS, f"{count} (clean — no residual payments)"
    )


async def check_demo_seeded_memory() -> CheckResult:
    try:
        notes = await recall_tenant_memory(BEAT_0_TENANT)
    except BotoCoreError as e:
        return _credentials_fail("Demo state: seeded memory", e)
    if len(notes) < SEEDED_MEMORY_MINIMUM:
        return CheckResult(
            "Demo state: seeded memory",
            Status.FAIL,
            f"tenant {BEAT_0_TENANT!r} has {len(notes)} recallable event(s), "
            f"need >= {SEEDED_MEMORY_MINIMUM}",
            "run `make demo-reset` then `make demo-seed` (or `make demo-prepare` "
            "for both) — beat 0's opening recall needs real prior "
            "invoice-exception sessions",
        )
    return CheckResult(
        "Demo state: seeded memory",
        Status.PASS,
        f"tenant {BEAT_0_TENANT!r} has {len(notes)} recallable event(s)",
    )


# --- runner ------------------------------------------------------------------


async def main() -> int:
    settings = get_settings()

    temporal_client, temporal_error = await _connect_temporal()
    routing: RoutingStatus | None = None
    if temporal_client is not None:
        try:
            routing = await deployment_registry.routing_status(temporal_client)
        except Exception as e:  # noqa: BLE001 - surfaced per-check below, not raised here
            temporal_error = e
            temporal_client = None

    results = await asyncio.gather(
        _guarded(".env", check_env()),
        _guarded("AWS credentials", check_aws_credentials(settings)),
        _guarded("DynamoDB", check_dynamodb(settings)),
        _guarded("Temporal Cloud", check_temporal_reachable(temporal_client, temporal_error)),
        _guarded(
            "Worker deployment version",
            check_worker_version(temporal_client, temporal_error, settings, routing),
        ),
        _guarded(
            "Task queue pollers", check_poller_count(temporal_client, temporal_error, settings)
        ),
        _guarded("Mockoon", check_mockoon(settings)),
        _guarded("Bedrock models", check_bedrock_models(settings)),
        _guarded("Bedrock guardrail", check_bedrock_guardrail(settings)),
        _guarded("AgentCore Memory", check_agentcore_memory(settings)),
        _guarded("AgentCore Gateway", check_agentcore_gateway(settings)),
        _guarded("AgentCore Runtime", check_agentcore_runtime(settings)),
        _guarded("S3 bucket", check_s3(settings)),
        _guarded("API", check_api(settings)),
        _guarded("UI dev server", check_ui()),
        _guarded("Demo state: fairness", check_demo_fairness()),
        _guarded("Demo state: kill switch", check_demo_kill_switch()),
        _guarded("Demo state: ramp", check_demo_ramp(temporal_client, temporal_error, routing)),
        _guarded("Demo state: payment counter", check_demo_payment_counter(settings)),
        _guarded("Demo state: seeded memory", check_demo_seeded_memory()),
    )

    for result in results:
        print(result.render())

    fail_count = sum(1 for r in results if r.status == Status.FAIL)
    warn_count = sum(1 for r in results if r.status == Status.WARN)
    ok_count = len(results) - fail_count - warn_count
    print(f"\n{len(results)} checks: {ok_count} PASS, {warn_count} WARN, {fail_count} FAIL")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
