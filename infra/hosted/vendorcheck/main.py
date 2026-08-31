"""VendorCheck — the hosted (tier 3) reference agent, E7.3 / E2.3 T4.

This file does NOT run in this repo's worker. It is deployed to AgentCore
Runtime and runs there, in its own container, on its own schedule, with its
own agent loop — the "third-party lane". The control plane reaches it through
one `InvokeAgentRuntime` call (`app/activities/hosted.py`) and sees only the
final answer.

That separation is the point, so this agent is deliberately self-contained:
its own model client, its own tool, its own canned data. It does *not* use
this project's tool catalog, approval gate, guardrail hook, or AgentCore
Gateway — a hosted agent belongs to whoever published it, and the control
plane does not get to reach inside. See `docs/MULTI_AGENT.md` and
`docs/DECISIONS.md` for the durability tradeoff that follows from that.

Its concern is also deliberately different from the Gateway's
`lookup_vendor_risk` (payment-history risk, `infra/lambdas/vendor_directory/`):
this one screens for *compliance* exposure. Two different questions about a
vendor, from two different providers, so the demo shows a genuinely separate
capability rather than a second copy of one we already have.

Deploy with the AgentCore CLI — see `docs/AWS_SETUP.md` (2026-08-29).
Fake, deterministic data, same spirit as `mocks/payment-service.json`.
"""

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

MODEL_ID = "us.anthropic.claude-sonnet-5"

# Compliance screening records. Keys are lowercased vendor names.
WATCHLIST: dict[str, dict] = {
    "globex retail": {
        "sanctions_hit": False,
        "adverse_media": 0,
        "jurisdiction": "US-DE",
        "beneficial_owner_verified": True,
        "notes": "Public filings current. No adverse findings.",
    },
    "initech supply": {
        "sanctions_hit": False,
        "adverse_media": 3,
        "jurisdiction": "CY",
        "beneficial_owner_verified": False,
        "notes": "Ultimate beneficial owner not established; three adverse media "
                 "reports in 18 months relating to undisclosed intermediaries.",
    },
    "acme logistics": {
        "sanctions_hit": False,
        "adverse_media": 1,
        "jurisdiction": "US-CA",
        "beneficial_owner_verified": True,
        "notes": "One historical customs penalty, resolved 2023.",
    },
    "meridian holdings": {
        "sanctions_hit": True,
        "adverse_media": 7,
        "jurisdiction": "RU",
        "beneficial_owner_verified": False,
        "notes": "Entity matches a designated-parties list. Do not transact.",
    },
}

UNKNOWN = {
    "sanctions_hit": False,
    "adverse_media": 0,
    "jurisdiction": "unknown",
    "beneficial_owner_verified": False,
    "notes": "No screening record on file for this vendor.",
}

SYSTEM_PROMPT = """You are VendorCheck, an independent third-party vendor
compliance screening service. You answer exactly one question: is it
compliant to transact with this vendor?

## How you decide

1. You MUST call `screen_vendor` before answering. Never answer from memory.
2. A `sanctions_hit` of true is decisive: the verdict is **blocked**, always,
   regardless of every other signal.
3. An unverified beneficial owner, or a high-risk jurisdiction, or two or more
   adverse media reports, means the verdict is **review** — a human compliance
   officer must sign off before transacting.
4. If nothing above applies and there is a screening record on file, the
   verdict is **clear**.
5. If there is no record on file, the verdict is **review**, not clear. An
   absence of findings is not the same as a clean screen — say so.

## What you must never do

- You MUST NOT invent a sanctions finding, a jurisdiction, or a media report.
- You MUST NOT return **clear** for a vendor you have no record for.

## Output

State the verdict (clear / review / blocked) on the first line, then one
sentence naming the single finding that drove it. Be brief — this is consumed
by another system, not read as a report.
"""


@tool
def screen_vendor(vendor_name: str) -> dict:
    """Look up a vendor's compliance screening record by name."""
    return WATCHLIST.get(vendor_name.strip().lower(), UNKNOWN)


app = BedrockAgentCoreApp()
agent = Agent(
    model=BedrockModel(model_id=MODEL_ID),
    system_prompt=SYSTEM_PROMPT,
    tools=[screen_vendor],
)


@app.entrypoint
def invoke(payload: dict) -> dict:
    """AgentCore Runtime's `/invocations` entrypoint: `{"prompt": ...}` in,
    JSON out (runtime-http-protocol-contract). The synchronous `agent(...)`
    call is correct *here* — this is a plain container, not a Temporal
    workflow sandbox, so the thread it spawns is fine."""
    prompt = (payload or {}).get("prompt", "")
    if not prompt:
        return {"response": "no prompt supplied", "status": "error"}
    return {"response": str(agent(prompt)), "status": "success"}


if __name__ == "__main__":
    app.run()
