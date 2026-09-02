"""Demo reset — return the job index to a clean state between deliveries (E8.1 T4).

Scope is deliberately narrow: **Job rows and their result rows, nothing else.**
Tenants, agent packages, installs and idempotency records are fixtures, not
run state — wiping them would mean re-seeding the registry before every
rehearsal.

Why this exists at all: `tenant_wait_p95` is windowed by *sample count* (the
tenant's most recent 200 jobs), not by time, so the metric is sticky. A
fairness-OFF round's long waits stay in the window and flatten the contrast on
the next fairness-ON round, which is exactly the comparison proof 1 turns on.
Clearing job rows between rounds is what makes the two measurements
comparable.

Tenants come from the registry, never a hardcoded list (CLAUDE.md §11).

    uv run python -m scripts.reset              # dry run — prints the plan
    uv run python -m scripts.reset --yes        # actually delete
    uv run python -m scripts.reset --tenant initech --yes
"""

import argparse

from app.registry import repository as repo

PAGE = 100


def _job_ids(tenant_id: str) -> list[str]:
    """Every job id for a tenant, paged past `list_jobs_for_tenant`'s limit."""
    seen: list[str] = []
    known: set[str] = set()
    while True:
        page = repo.list_jobs_for_tenant(tenant_id, limit=PAGE)
        fresh = [j.job_id for j in page if j.job_id not in known]
        if not fresh:
            return seen
        seen.extend(fresh)
        known.update(fresh)
        if len(page) < PAGE:
            return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="Only this tenant (default: every registered tenant)")
    parser.add_argument("--yes", action="store_true", help="Actually delete; otherwise dry run")
    args = parser.parse_args()

    tenants = [args.tenant] if args.tenant else [t.tenant_id for t in repo.list_tenants()]

    plan = {t: _job_ids(t) for t in tenants}
    total = sum(len(v) for v in plan.values())

    for tenant_id, job_ids in plan.items():
        print(f"  {tenant_id:9} {len(job_ids):4} job rows")
    print(f"  {'TOTAL':9} {total:4}")

    if total == 0:
        print("\nnothing to delete — job index is already clean")
        return 0

    if not args.yes:
        print("\ndry run — re-run with --yes to delete")
        print("(tenants, agent packages, installs and idempotency records are never touched)")
        return 0

    deleted = 0
    for job_ids in plan.values():
        for job_id in job_ids:
            repo.delete_job(job_id)
            repo.delete_job_result(job_id)
            deleted += 1

    print(f"\ndeleted {deleted} job rows (+ their result rows)")

    remaining = sum(len(_job_ids(t)) for t in tenants)
    print(f"remaining job rows for these tenants: {remaining}")
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
