# Agent package template

Populated in E2.1. Copy this folder to `agents/<id>/` and provide:

- `manifest.yaml` — id, tier, model, mcp_servers, guardrail, interventions, output_model
- `procedure.sop.md` — reasoning instructions in plain markdown, RFC 2119 keywords
- `tools.py` — tier 2+ only, custom activity tools

See CLAUDE.md §6 and the "Adding an agent" tiers.
