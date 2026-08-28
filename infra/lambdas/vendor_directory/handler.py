"""AgentCore Gateway Lambda target — exposes vendor risk lookup as an MCP tool
(E7.1 T1). Self-contained: no dependency on Mockoon being network-reachable,
since Gateway (an AWS service) cannot reach a laptop's localhost.

Gateway invokes this directly with a flat event of the tool's input properties
and metadata on `context.client_context.custom` — see
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html.
Deliberately fake, deterministic data, same spirit as mocks/payment-service.json.
"""

VENDOR_RISK = {
    "globex retail": {"risk_tier": "low", "notes": "12 clean payment cycles, no disputes on file."},
    "initech supply": {"risk_tier": "medium", "notes": "2 late deliveries in the last quarter."},
    "acme logistics": {"risk_tier": "low", "notes": "Preferred vendor, net-30 terms."},
}

DEFAULT_RESULT = {"risk_tier": "unknown", "notes": "No history on file for this vendor."}


def lambda_handler(event: dict, context) -> dict:
    tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    if "lookup_vendor_risk" not in tool_name:
        return {"error": f"unknown tool: {tool_name}"}

    vendor = str(event.get("vendor_name", "")).strip().lower()
    return VENDOR_RISK.get(vendor, DEFAULT_RESULT)
