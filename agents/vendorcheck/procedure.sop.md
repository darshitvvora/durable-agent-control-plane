# VendorCheck (hosted)

This agent's real instructions do **not** live here.

VendorCheck runs on AgentCore Runtime, outside this control plane, and carries
its own system prompt, its own tools, and its own data
(`infra/hosted/vendorcheck/main.py`). The control plane sends it a prompt and
receives its answer — it does not get to tell a third-party agent how to
think.

This file exists because every agent package has one, and because it is the
honest place to record what the boundary means. It is **not** sent to the
hosted agent as a system prompt.

## What it does

Vendor name in, compliance verdict out: **clear**, **review**, or **blocked**,
screened against sanctions exposure, beneficial-ownership verification,
jurisdiction, and adverse media.

Distinct from the Gateway's `lookup_vendor_risk` (`invoice-exception`'s tool),
which answers a different question — payment-history risk. A vendor can be
low payment risk and still fail compliance screening.

## What the control plane does and does not control

| | Native agents (tiers 1–2) | VendorCheck (tier 3) |
|---|---|---|
| SOP / system prompt | this file, rendered per tenant | the hosted agent's own |
| Tool calls | our catalog, as Temporal activities | its own, invisible to us |
| Approval gate | enforced (`ApprovalGate`) | not available |
| Guardrails | enforced (`GuardrailGate`) | not available |
| Durability | per model call and per tool call | per invocation |
| Session terminal | live tokens and tool calls | one call, then the answer |

See `docs/MULTI_AGENT.md` for why that tradeoff is inherent to the hosted lane
rather than something missing from this package.
