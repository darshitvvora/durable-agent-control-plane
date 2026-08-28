"""MCP server catalog — maps a manifest's `mcp_servers` names to
`TemporalMCPClient` handles, the same resolve-or-fail shape as TOOL_CATALOG
(`app/activities/catalog.py`).

"vendor-directory" is a shared *name*, not the Gateway's URL — mirroring
`model_registry()` (`app/temporal_client.py`), where "bedrock-claude" is a name
resolved to a real model only on the worker side. Here, the worker side is
`strands_plugin()`'s `mcp_clients={"vendor-directory": lambda: MCPClient(...)}`,
which is where the actual Gateway URL lives (E7.1 T1). Keeping the manifest
and this catalog free of the literal URL is what keeps a published package
portable across AWS accounts and forks.
"""

from datetime import timedelta
from typing import Any

from temporalio.common import RetryPolicy
from temporalio.contrib.strands import TemporalMCPClient

MCP_ACTIVITY_OPTIONS: dict[str, Any] = {
    "start_to_close_timeout": timedelta(seconds=15),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}

MCP_SERVER_CATALOG: dict[str, TemporalMCPClient] = {
    "vendor-directory": TemporalMCPClient(
        "vendor-directory",
        # Re-listed every turn (T1's acceptance criteria), not cached — this is
        # a demo of the Gateway wiring itself, and cache_tools=True would hide
        # a Gateway-side tool change until the next session.
        cache_tools=False,
        **MCP_ACTIVITY_OPTIONS,
    )
}
