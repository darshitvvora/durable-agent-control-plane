"""Agent package loading — `agents/<id>/` on disk becomes an AgentPackage row.

`agents/` is an authoring-time surface, not a runtime one: `dos agent publish`
reads the package from disk and writes it to the registry, and from then on the
workflow only ever reads DynamoDB. That is why the Lambda worker does not need
the agents/ folder in its deployment bundle.

Adding an agent means adding a directory here. No workflow code, no UI code
(CLAUDE.md §2).
"""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from app.registry.models import AgentPackage, AgentTier, ApprovalPolicy

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
MANIFEST_NAME = "manifest.yaml"
SOP_NAME = "procedure.sop.md"
TEMPLATE_ID = "_template"

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class ManifestError(Exception):
    """A package on disk is malformed. Raised with an author-facing message."""


class Manifest(BaseModel):
    """The on-disk manifest.yaml contract — this is the boilerplate's public API.

    Kept deliberately small. `parameters` supplies defaults for the placeholders
    a procedure.sop.md uses; runtime context (tenant, job) overrides them.
    """

    id: str
    name: str
    version: int = 1
    tier: str = "1"
    model: str = "bedrock-claude"
    tools: list[str] = []
    mcp_servers: list[str] = []
    parameters: dict[str, str] = {}
    approval_policy: ApprovalPolicy | None = None
    guardrail_arn: str | None = None
    output_model: str | None = None


def placeholders(sop: str) -> set[str]:
    return set(PLACEHOLDER.findall(sop))


def render_sop(sop: str, context: dict[str, str]) -> str:
    """Substitute {{placeholders}}. Pure string work — safe in workflow code.

    An unknown placeholder is left verbatim rather than blanked, so a typo shows
    up in the prompt (and in the session pane) instead of silently deleting an
    instruction.
    """
    return PLACEHOLDER.sub(lambda m: context.get(m.group(1), m.group(0)), sop)


def package_dir(agent_id: str) -> Path:
    return AGENTS_DIR / agent_id.replace("-", "_")


def load_manifest(agent_id: str) -> tuple[Manifest, str]:
    """Read and validate one package from disk. Returns (manifest, raw SOP)."""
    directory = package_dir(agent_id)
    manifest_path = directory / MANIFEST_NAME
    sop_path = directory / SOP_NAME

    if not manifest_path.is_file():
        raise ManifestError(f"{manifest_path} not found")
    if not sop_path.is_file():
        raise ManifestError(f"{sop_path} not found")

    try:
        raw = yaml.safe_load(manifest_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{manifest_path} is not valid YAML: {exc}") from exc

    try:
        manifest = Manifest(**raw)
    except ValidationError as exc:
        raise ManifestError(f"{manifest_path} is invalid:\n{exc}") from exc

    if manifest.id != agent_id:
        raise ManifestError(f"{manifest_path}: id is {manifest.id!r}, expected {agent_id!r}")

    return manifest, sop_path.read_text()


def to_package(manifest: Manifest, sop: str) -> AgentPackage:
    """Convert an on-disk package into the registry row the workflow reads.

    The SOP is stored unrendered; placeholders resolve per job at runtime so one
    published row serves every tenant.
    """
    return AgentPackage(
        agent_id=manifest.id,
        version=manifest.version,
        name=manifest.name,
        tier=AgentTier(manifest.tier),
        model=manifest.model,
        system_prompt=sop,
        parameters=manifest.parameters,
        tools=manifest.tools,
        mcp_servers=manifest.mcp_servers,
        approval_policy=manifest.approval_policy,
        guardrail_arn=manifest.guardrail_arn,
        output_model=manifest.output_model,
    )


def discover() -> list[str]:
    """Agent ids present on disk, excluding the template."""
    if not AGENTS_DIR.is_dir():
        return []
    return sorted(
        directory.name.replace("_", "-")
        for directory in AGENTS_DIR.iterdir()
        if directory.is_dir() and directory.name != TEMPLATE_ID
        and (directory / MANIFEST_NAME).is_file()
    )
