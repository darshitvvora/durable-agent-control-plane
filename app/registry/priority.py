"""Tenant -> real Temporal Priority. Client-side only, never called from workflow
code, so no determinism concern.

Kept separate from repository.py (which stays pure storage) so it can be reused
by every workflow-starting call site (`dos agent test`, the future API in E4,
`dos demo flood` in E3.2) without duplicating the lookup-or-fail behaviour.
"""

from temporalio.common import Priority

from app.registry import repository as repo


def resolve_priority(tenant_id: str) -> Priority:
    """The tenant's real priority/fairness values, straight from the registry.

    Raises rather than defaulting silently — an unrecognised tenant is the
    caller's mistake, not something to paper over with a made-up priority.

    Fairness is not a Temporal Cloud setting to flip — it engages the moment any
    job attaches a fairness_key. So "fairness off" (E3.2, proof 1) means this
    resolver stops attaching one: the job falls back to the implicit shared
    empty-string key, i.e. plain FIFO within its priority tier. Centralising the
    check here means every caller (dos agent test, dos demo flood, the future
    API) respects the toggle without re-deriving this logic.
    """
    tenant = repo.get_tenant(tenant_id)
    if tenant is None:
        raise ValueError(f"unknown tenant {tenant_id!r} — run `dos tenant add` first")
    if not repo.get_fairness_setting().enabled:
        return Priority(priority_key=tenant.priority_key)
    return Priority(
        priority_key=tenant.priority_key,
        fairness_key=tenant.tenant_id,
        fairness_weight=tenant.fairness_weight,
    )
