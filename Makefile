.PHONY: worker api ui mockoon verify verify-agent verify-payment verify-tool-call verify-interrupt \
	verify-tenant-priority \
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

# npx avoids a global install; the desktop app can open the same collection file
mockoon:
	npx --yes @mockoon/cli@9.8.0 start --data mocks/payment-service.json --port 3001

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

# needs `make worker` running
verify-tenant-priority:
	uv run python -m scripts.verify_tenant_priority

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
