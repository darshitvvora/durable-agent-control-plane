"""E8/beat-0 prerequisite: a session started through the API is a first-class
job — it gets a Job row, appears in the UI's job list, and runs to completion.

Requires `make worker`, `make api` and Mockoon running.
"""

import asyncio

import httpx

from app.registry import repository as repo

API = "http://localhost:8000"
TENANT = "acme"
AGENT = "returns-triage"  # tier 1: no tools, no payment side effect, cheap
PROMPT = "Order A-1002, opened box, buyer says wrong size, 12 days since delivery."


async def main() -> int:
    async with httpx.AsyncClient(timeout=180.0) as http:
        response = await http.post(
            f"{API}/api/jobs",
            params={"agent_id": AGENT, "tenant_id": TENANT, "prompt": PROMPT},
        )
        response.raise_for_status()
        job_id = response.json()["job_id"]
        print(f"started {job_id}")

        row = repo.get_job(job_id)
        assert row is not None, f"no Job row written for {job_id}"
        assert row.tenant_id == TENANT, f"wrong tenant on Job row: {row.tenant_id}"
        print("Job row written")

        listed = (await http.get(f"{API}/api/jobs", params={"tenant_id": TENANT})).json()
        assert any(j["job_id"] == job_id for j in listed), "job absent from /api/jobs"
        print("job visible in the UI's job list")

        for _ in range(90):
            state = (await http.get(f"{API}/api/jobs/{job_id}/state")).json()
            if state["status"] != "RUNNING":
                break
            await asyncio.sleep(2)

        assert state["status"] == "COMPLETED", f"ended {state['status']}"
        print(f"completed on {state['worker_version']}")

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
