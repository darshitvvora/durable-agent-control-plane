"""Static validation of an agent package.

Everything checkable without running the agent is checked here, so an author gets
a clear error at `dos agent validate` time rather than a failed job later.
"""

from app.activities.catalog import TOOL_CATALOG
from app.registry.manifest import Manifest, ManifestError, load_manifest, placeholders
from app.temporal_client import model_registry

RUNTIME_PLACEHOLDERS = {"tenant_id", "agent_id", "agent_version", "job_id"}


def validate(agent_id: str) -> list[str]:
    """Return a list of problems. Empty means the package is publishable."""
    try:
        manifest, sop = load_manifest(agent_id)
    except ManifestError as exc:
        return [str(exc)]

    problems: list[str] = []
    problems += _check_model(manifest)
    problems += _check_tools(manifest)
    problems += _check_placeholders(manifest, sop)
    problems += _check_approval_policy(manifest)

    if not sop.strip():
        problems.append("procedure.sop.md is empty — it becomes the system prompt")

    return problems


def _check_model(manifest: Manifest) -> list[str]:
    known = sorted(model_registry())
    if manifest.model not in known:
        return [f"model {manifest.model!r} is not registered (known: {', '.join(known)})"]
    return []


def _check_tools(manifest: Manifest) -> list[str]:
    known = sorted(TOOL_CATALOG)
    return [
        f"tool {tool!r} is not in the catalog (known: {', '.join(known) or 'none'})"
        for tool in manifest.tools
        if tool not in TOOL_CATALOG
    ]


def _check_placeholders(manifest: Manifest, sop: str) -> list[str]:
    """Every {{placeholder}} must resolve, or it silently ships in the prompt."""
    available = RUNTIME_PLACEHOLDERS | set(manifest.parameters)
    unresolved = sorted(placeholders(sop) - available)
    problems = [
        f"procedure.sop.md uses {{{{{name}}}}} but it is not declared under parameters:"
        for name in unresolved
    ]
    unused = sorted(set(manifest.parameters) - placeholders(sop))
    problems += [f"parameter {name!r} is declared but never used in procedure.sop.md"
                 for name in unused]
    return problems


def _check_approval_policy(manifest: Manifest) -> list[str]:
    policy = manifest.approval_policy
    if policy is None:
        return []
    if policy.tool not in manifest.tools:
        return [
            f"approval_policy targets tool {policy.tool!r}, which this agent does not declare"
        ]
    return []
