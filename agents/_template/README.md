# Agent package template

Do not edit this folder — copy it. `dos agent init <id>` (or `make agent-init
ID=<id>`) copies it to `agents/<id>/`, fills in the id and name, and replaces
this README with a stub for your agent.

An agent package is:

- `manifest.yaml` — tier, model, tools, MCP servers, parameters, approval
  policy, optional structured output model. Every field is annotated in the
  copy beside this file.
- `procedure.sop.md` — the system prompt, in plain English with RFC 2119
  keywords. `{{placeholders}}` are substituted per job.
- `tools.py` — **tier 2 only**, and optional even then: most tier-2 agents put
  their activity in `app/activities/` and register it in `TOOL_CATALOG`, which
  is what all three reference agents do.

Tier 1 needs no Python at all. See `agents/returns_triage/` for a complete
worked tier-1 example, and [CONTRIBUTING.md](../../CONTRIBUTING.md) for the
authoring guide.
