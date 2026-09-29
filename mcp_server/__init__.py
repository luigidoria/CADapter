"""mcp_server/ -- SolidWorks exposed as an MCP server for Claude.

  mcp_solidworks      the server: generic COM tools (sw_call, sw_get_prop, ...),
                      signature lookup, recipe search, convenience tools
  sw_session_logger   stdio proxy that records every tool call to sessions/*.jsonl

Registered in `.mcp.json`; run from the repo root.
"""
