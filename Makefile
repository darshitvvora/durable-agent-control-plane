.PHONY: worker api ui e2e mockoon verify verify-agent verify-payment verify-tool-call verify-interrupt \
	verify-tenant-priority verify-streaming verify-agents verify-versioning verify-pinning verify-kill-resume \
	verify-guardrail verify-returns-triage \
	verify-sandbox-isolation verify-external-storage verify-hosted \
	replay lint typecheck format ci \
	dos agent-list agent-init agent-validate agent-test agent-publish \
	tenant-add tenant-list \
	demo-flood demo-fairness-on demo-fairness-off demo-metrics demo-ramp demo-kill-worker

# --- dev loop — Temporal Cloud only, no local server (CLAUDE.md §2) ---

worker:
	uv run python -m app.worker

api:
	uv run uvicorn app.api.main:app --reload

ui:
	cd ui && npm run dev

# needs `make worker`, `make api`, `make mockoon`, and `make ui` all running —
# drives the real UI against the real stack, no mocks (docs/DECISIONS.md, E5.1)
e2e:
	cd ui && npm run e2e

# mockoon-cli is installed globally (`npm install -g @mockoon/cli`); the
# desktop app can open the same collection file as an alternative
mockoon:
	mockoon-cli start --data mocks/payment-service.json --port 3001

# --- verification — runs against real AWS and real Temporal Cloud ---

verify:
	uv run python -m scripts.verify

# needs `make worker` running in another shell
verify-agent:
	uv run python -m scripts.verify_agent_job

# needs Mockoon running; no worker required
verify-payment:
	uv run python -m scripts.verify_payment

# needs `make worker` and Mockoon running
verify-tool-call:
	uv run python -m scripts.verify_tool_call

# needs `make worker` and Mockoon running
verify-interrupt:
	uv run python -m scripts.verify_interrupt

# needs `make worker`, `make api`, and Mockoon running
verify-guardrail:
	uv run python -m scripts.verify_guardrail

# needs `make worker` running
verify-tenant-priority:
	uv run python -m scripts.verify_tenant_priority

# needs `make worker`, `make api`, and Mockoon running
verify-streaming:
	uv run python -m scripts.verify_streaming

# needs `make worker` running — the worked tier-1 example (E10.1 T3)
verify-returns-triage:
	uv run python -m scripts.verify_returns_triage

# needs `make worker` and Mockoon running
verify-agents:
	uv run python -m scripts.verify_reference_agents

# needs `make worker` and Mockoon running
verify-versioning:
	uv run python -m scripts.verify_versioning

# needs `make worker` and Mockoon running — spawns its OWN second worker process
verify-pinning:
	uv run python -m scripts.verify_pinning

# needs Mockoon running — spawns its OWN worker process(es), do not run `make worker` alongside it
verify-kill-resume:
	uv run python -m scripts.verify_kill_resume

verify-sandbox-isolation:
	uv run python -m scripts.verify_sandbox_isolation

verify-external-storage:
	uv run python -m scripts.verify_external_storage

verify-hosted:
	uv run python -m scripts.verify_hosted

# non-determinism guard — replays real histories from Temporal Cloud (script lands with E1.1)
replay:
	uv run python -m scripts.replay

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy app cli scripts

ci: lint typecheck

# --- agent packages ---

# The editable install's `dos` entrypoint. Worked only after the repo directory was
# renamed to drop its trailing space (see docs/DECISIONS.md, 2026-08-21).
DOS = uv run dos

dos:
	$(DOS) $(ARGS)

agent-list:
	$(DOS) agent list

agent-init:
	$(DOS) agent init $(ID)

agent-validate:
	$(DOS) agent validate $(ID)

agent-test:
	$(DOS) agent test $(ID) --prompt "$(PROMPT)"

agent-publish:
	$(DOS) agent publish $(ID)

tenant-add:
	$(DOS) tenant add $(ID) --name "$(NAME)" --tier $(TIER) --priority-key $(PRIORITY_KEY) --fairness-weight $(FAIRNESS_WEIGHT)

tenant-list:
	$(DOS) tenant list

# --- demo controls ---

demo-flood:
	uv run dos demo flood --tenant $(TENANT) --count $(COUNT)

demo-fairness-on:
	uv run dos demo fairness --on

demo-fairness-off:
	uv run dos demo fairness --off

demo-metrics:
	uv run dos demo metrics

demo-ramp:
	uv run dos demo ramp --version $(VERSION) --percent $(PERCENT)

demo-kill-worker:
	uv run dos demo kill-worker --at-tool-boundary
