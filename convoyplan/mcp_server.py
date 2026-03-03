"""CONVOYPLAN MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from convoyplan.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-convoyplan[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-convoyplan[mcp]'")
        return 1
    app = FastMCP("convoyplan")

    @app.tool()
    def convoyplan_scan(target: str) -> str:
        """Defense logistics route/sustainment planner computing fuel, resupply windows, and chokepoint risk from a YAML plan.. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
