"""`dos` — the agent control plane CLI.

Primary extensibility surface: an author scaffolds, validates, tests, and
publishes an agent without touching workflow or UI code.
"""

import asyncio
import shutil
from typing import NoReturn

import typer

from app.demo import DEFAULT_FLOOD_AGENT_ID
from app.registry import repository as repo
from app.registry.manifest import (
    AGENTS_DIR,
    MANIFEST_NAME,
    SOP_NAME,
    TEMPLATE_ID,
    ManifestError,
    discover,
    load_manifest,
    package_dir,
    to_package,
)
from app.registry.models import AgentPackage, AgentTier, Tenant, TenantTier
from app.registry.validate import validate as validate_package

app = typer.Typer(help="Durable Agent Control Plane CLI", no_args_is_help=True)
agent_app = typer.Typer(help="Author and publish agent packages", no_args_is_help=True)
tenant_app = typer.Typer(help="Manage tenants in the registry", no_args_is_help=True)
demo_app = typer.Typer(help="Demo controls: fairness, flood, metrics", no_args_is_help=True)
app.add_typer(agent_app, name="agent")
app.add_typer(tenant_app, name="tenant")
app.add_typer(demo_app, name="demo")


def _fail(message: str) -> NoReturn:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@agent_app.command("list")
def agent_list() -> None:
    """Agent packages present on disk."""
    ids = discover()
    if not ids:
        typer.echo("no agent packages found")
        return
    for agent_id in ids:
        try:
            manifest, _ = load_manifest(agent_id)
            typer.echo(f"{agent_id:24s} v{manifest.version}  tier {manifest.tier}  {manifest.name}")
        except ManifestError:
            typer.secho(f"{agent_id:24s} INVALID", fg=typer.colors.RED)


@agent_app.command("init")
def agent_init(agent_id: str) -> None:
    """Scaffold a new agent package from the template."""
    destination = package_dir(agent_id)
    if destination.exists():
        _fail(f"{destination} already exists")

    template = AGENTS_DIR / TEMPLATE_ID
    if not template.is_dir():
        _fail(f"template not found at {template}")

    shutil.copytree(template, destination)
    display_name = agent_id.replace("-", " ").title()
    manifest_path = destination / MANIFEST_NAME
    manifest_path.write_text(
        manifest_path.read_text()
        .replace("id: my-agent", f"id: {agent_id}")
        .replace("name: My Agent", f"name: {display_name}")
    )

    # The template's README describes the *template*. Copying it verbatim left
    # every scaffolded package announcing itself as "Agent package template",
    # which is the first file a reviewer opens.
    (destination / "README.md").write_text(
        f"# {display_name}\n\n"
        f"Agent package for `{agent_id}`. The two files beside this one are the "
        f"whole agent — there is no code to deploy.\n\n"
        f"- `manifest.yaml` — tier, model, tools, parameters, approval policy\n"
        f"- `procedure.sop.md` — the system prompt, substituted per job\n\n"
        f"Say here what this agent decides and what it must never do.\n\n"
        f"Authoring guide: [CONTRIBUTING.md](../../CONTRIBUTING.md)\n"
    )

    typer.secho(f"created {destination}", fg=typer.colors.GREEN)
    typer.echo(f"  edit {MANIFEST_NAME} and {SOP_NAME}, then: dos agent validate {agent_id}")


@agent_app.command("validate")
def agent_validate(agent_id: str) -> None:
    """Check a package resolves: model, tools, placeholders, approval policy."""
    problems = validate_package(agent_id)
    if problems:
        typer.secho(f"{agent_id} is not valid:", fg=typer.colors.RED, err=True)
        for problem in problems:
            typer.secho(f"  - {problem}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho(f"{agent_id} is valid", fg=typer.colors.GREEN)


@agent_app.command("publish")
def agent_publish(agent_id: str) -> None:
    """Write the package to the registry. No redeploy, no worker restart."""
    problems = validate_package(agent_id)
    if problems:
        _fail(f"{agent_id} is not valid — run `dos agent validate {agent_id}` first")

    manifest, sop = load_manifest(agent_id)
    package = to_package(manifest, sop)
    repo.put_agent_package(package)
    typer.secho(
        f"published {package.agent_id} v{package.version} to the registry", fg=typer.colors.GREEN
    )


@agent_app.command("register-hosted")
def agent_register_hosted(
    agent_id: str,
    name: str = typer.Option(..., "--name"),
    runtime_arn: str = typer.Option(..., "--runtime-arn", help="AgentCore Runtime ARN"),
    version: int = typer.Option(1, "--version"),
    description: str = typer.Option(
        "", "--description", help="what this agent does, shown in the agent store"
    ),
) -> None:
    """Register a hosted (tier 3) agent by ARN alone — no package on disk (E7.3 T2).

    The whole point of the hosted lane: installing a third-party agent needs
    neither repo access nor a deploy, just an ARN someone hands you. Contrast
    `publish`, which reads agents/<id>/ from disk.
    """
    if not runtime_arn.startswith("arn:aws:bedrock-agentcore:"):
        _fail(
            f"{runtime_arn!r} is not an AgentCore Runtime ARN "
            "(expected arn:aws:bedrock-agentcore:<region>:<account>:runtime/...)"
        )

    package = AgentPackage(
        agent_id=agent_id,
        version=version,
        name=name,
        tier=AgentTier.HOSTED,
        model="bedrock-claude",  # ignored for tier 3; the hosted agent picks its own
        # No SOP: a hosted agent carries its own system prompt. This text is
        # never sent to it — it is what the agent store shows a tenant.
        system_prompt=description or f"{name} — hosted on AgentCore Runtime, ARN {runtime_arn}",
        runtime_arn=runtime_arn,
    )
    repo.put_agent_package(package)
    typer.secho(
        f"registered hosted agent {agent_id} v{version} -> {runtime_arn}", fg=typer.colors.GREEN
    )


@tenant_app.command("add")
def tenant_add(
    tenant_id: str,
    name: str = typer.Option(..., "--name"),
    tier: TenantTier = typer.Option(..., "--tier"),  # noqa: B008 -- typer's exemption misses enum types
    priority_key: int = typer.Option(3, "--priority-key", help="1-5, lower = higher priority"),
    fairness_weight: float = typer.Option(1.0, "--fairness-weight"),
    memory_namespace: str = typer.Option("", "--memory-namespace", help="defaults to tenant_id"),
    s3_prefix: str = typer.Option("", "--s3-prefix", help="defaults to tenants/<tenant_id>/"),
) -> None:
    """Add or update a tenant in the registry."""
    tenant = Tenant(
        tenant_id=tenant_id,
        name=name,
        tier=tier,
        priority_key=priority_key,
        fairness_weight=fairness_weight,
        memory_namespace=memory_namespace or tenant_id,
        s3_prefix=s3_prefix or f"tenants/{tenant_id}/",
    )
    repo.put_tenant(tenant)
    typer.secho(
        f"added tenant {tenant_id} ({tier}, priority_key={priority_key}, "
        f"fairness_weight={fairness_weight})",
        fg=typer.colors.GREEN,
    )


@tenant_app.command("install")
def tenant_install(tenant_id: str, agent_id: str) -> None:
    """Make a published agent available to a tenant."""
    try:
        repo.install_agent(tenant_id, agent_id)
    except ValueError as e:
        _fail(str(e))
    typer.secho(f"installed {agent_id} for {tenant_id}", fg=typer.colors.GREEN)


@tenant_app.command("uninstall")
def tenant_uninstall(tenant_id: str, agent_id: str) -> None:
    """Remove an agent from a tenant."""
    try:
        repo.uninstall_agent(tenant_id, agent_id)
    except ValueError as e:
        _fail(str(e))
    typer.secho(f"uninstalled {agent_id} from {tenant_id}", fg=typer.colors.GREEN)


@tenant_app.command("list")
def tenant_list() -> None:
    """Tenants present in the registry."""
    tenants = repo.list_tenants()
    if not tenants:
        typer.echo("no tenants found")
        return
    for tenant in tenants:
        typer.echo(
            f"{tenant.tenant_id:12s} {tenant.tier:10s} priority={tenant.priority_key} "
            f"weight={tenant.fairness_weight:.1f}  {tenant.name}"
        )


@agent_app.command("test")
def agent_test(
    agent_id: str,
    prompt: str = typer.Option(..., "--prompt", help="The job input to send"),
    tenant: str = typer.Option("acme", "--tenant", help="Tenant to run as"),
) -> None:
    """Publish, then run one real job through Temporal Cloud and print the result."""
    asyncio.run(_run_test(agent_id, prompt, tenant))


async def _run_test(agent_id: str, prompt: str, tenant: str) -> None:
    from app.config import get_settings
    from app.registry.priority import resolve_priority
    from app.temporal_client import connect
    from app.workflows.agent_job import AgentJobWorkflow

    problems = validate_package(agent_id)
    if problems:
        _fail(f"{agent_id} is not valid — run `dos agent validate {agent_id}` first")

    try:
        resolve_priority(tenant)
    except ValueError as e:
        _fail(str(e))

    manifest, sop = load_manifest(agent_id)
    repo.put_agent_package(to_package(manifest, sop))

    settings = get_settings()

    from app.sessions import start_session

    job_id = await start_session(manifest.id, tenant, prompt)
    typer.echo(f"running {job_id} on {settings.temporal_namespace}...")
    client = await connect()
    handle = client.get_workflow_handle_for(AgentJobWorkflow.run, job_id)
    outcome = await handle.result()
    typer.echo(f"  stop_reason = {outcome.stop_reason}")
    typer.echo(f"  output      = {outcome.output.strip()}")


@demo_app.command("fairness")
def demo_fairness(
    on: bool = typer.Option(..., "--on/--off", help="Enable or disable Task Queue Fairness"),
) -> None:
    """Toggle fairness for every Priority resolve_priority() builds from now on."""
    from app.registry.models import FairnessSetting

    repo.put_fairness_setting(FairnessSetting(enabled=on))
    typer.secho(f"fairness {'enabled' if on else 'disabled'}", fg=typer.colors.GREEN)


@demo_app.command("flood")
def demo_flood(
    tenant: str = typer.Option(..., "--tenant"),
    count: int = typer.Option(..., "--count"),
    agent: str = typer.Option(
        DEFAULT_FLOOD_AGENT_ID, "--agent", help="Published agent to flood with"
    ),
) -> None:
    """Submit `count` jobs for `tenant` concurrently, to build real queue backlog."""
    from app.demo import submit_flood

    typer.echo(f"flooding {count} {agent} jobs for {tenant}...")
    try:
        asyncio.run(submit_flood(tenant, count, agent))
    except ValueError as e:
        _fail(str(e))
    typer.secho(f"submitted {count} jobs for {tenant}", fg=typer.colors.GREEN)


@demo_app.command("metrics")
def demo_metrics() -> None:
    """Current per-tenant p95 wait, computed from real Job rows."""
    from app.registry.metrics import tenant_wait_p95

    tenants = repo.list_tenants()
    if not tenants:
        typer.echo("no tenants found")
        return
    for t in tenants:
        p95 = tenant_wait_p95(t.tenant_id)
        value = f"{p95:.2f}s" if p95 is not None else "no data"
        typer.echo(f"{t.tenant_id:12s} p95 wait = {value}")


@demo_app.command("ramp")
def demo_ramp(
    version: str = typer.Option(None, "--version", help="Build ID to ramp toward"),
    percent: float = typer.Option(None, "--percent", help="0-100; >=100 makes it current"),
    clear: bool = typer.Option(False, "--clear", help="Remove any active ramp"),
) -> None:
    """Route a percentage of NEW sessions to another build (proof 2). In-flight
    PINNED sessions on other versions keep running untouched."""
    from app.demo import clear_ramp, ramp_status, set_ramp

    if clear:
        asyncio.run(clear_ramp())
        typer.secho("ramp cleared", fg=typer.colors.GREEN)
        return

    if version is None or percent is None:
        status = asyncio.run(ramp_status())
        typer.echo(f"current:  {status.current_version or '(none)'}")
        typer.echo(
            f"ramping:  {status.ramping_version or '(none)'} "
            f"@ {status.ramping_percentage:.0f}%"
        )
        return

    try:
        asyncio.run(set_ramp(version, percent))
    except ValueError as e:
        _fail(str(e))
    typer.secho(f"ramping {percent:.0f}% of new sessions to {version}", fg=typer.colors.GREEN)


@demo_app.command("kill-worker")
def demo_kill_worker(
    at_tool_boundary: bool = typer.Option(
        True, "--at-tool-boundary/--no-at-tool-boundary",
        help="Only supported mode: crash right after the next consequential tool call succeeds",
    ),
) -> None:
    """Arm the worker to crash right after its next payment/dispute call
    succeeds, but before Temporal records completion (proof 3). Does not kill
    anything itself — restart the worker process yourself once it exits."""
    from app.demo import arm_kill_switch

    if not at_tool_boundary:
        _fail("kill-worker only supports --at-tool-boundary — there is no timer-based mode")
    asyncio.run(arm_kill_switch())
    typer.secho(
        "armed — the worker will exit right after its next payment/dispute call succeeds",
        fg=typer.colors.YELLOW,
    )


@demo_app.command("payment-count")
def demo_payment_count() -> None:
    """How many payments actually reached the mocked provider (proof 3)."""
    from app.demo import payment_count

    typer.echo(f"payments recorded by the mock provider = {asyncio.run(payment_count())}")


if __name__ == "__main__":
    app()
