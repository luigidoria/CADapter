# Base setup

Use Windows, Python 3.12+ and an installed, licensed SOLIDWORKS. CADapter V1 was
validated with SOLIDWORKS 2017 and SOLIDWORKS 2026. Run these commands from the
repository root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe mcp_server\build_api_signatures_typelib.py
```

The signature builder uses the locally registered type library (TYPELIB) and writes ignored
`mcp_server/cache/api_signatures.json`. SOLIDWORKS need not be running for that
step. It reads the highest registered SOLIDWORKS TYPELIB version and writes one
cache; with several versions registered, the cache describes the newest one. Rebuild
it after installing or upgrading SOLIDWORKS.

## Connect an MCP client

Open SOLIDWORKS before the first CAD call. The client starts the server as a stdio
process: the venv interpreter running `mcp_server/mcp_solidworks.py`. The server finds
its modules, configuration and cache from its own location, so with absolute paths it
can be started from any working directory. The examples assume the checkout is at
`C:\CADapter`; replace it with yours and quote paths that contain spaces.

**Claude Code, inside this checkout.** `.mcp.json` registers `CADapter` with paths
relative to the repository root, so it works when Claude Code starts here.

**Claude Code, in any folder (user scope).** Register the server once for your user:

```powershell
claude mcp add --scope user CADapter -- C:\CADapter\.venv\Scripts\python.exe C:\CADapter\mcp_server\mcp_solidworks.py
claude mcp list                                  # CADapter ... Connected
claude mcp remove --scope user CADapter          # to undo
```

**Other MCP clients.** Most accept the same `mcpServers` form in their own
configuration file. Use absolute paths; JSON needs doubled backslashes:

```json
{
  "mcpServers": {
    "CADapter": {
      "type": "stdio",
      "command": "C:\\CADapter\\.venv\\Scripts\\python.exe",
      "args": ["C:\\CADapter\\mcp_server\\mcp_solidworks.py"]
    }
  }
}
```

V1 was tested end-to-end with Claude Code; other MCP clients have not yet been formally
validated.

## Configuration and troubleshooting

Configuration is optional. See `mcp_server/.env.example`; use
`mcp_server/.env` only for overrides. An existing root `.env` remains supported.

Recipe search (RAG) is optional. The core CAD tools do not need it and start without
recipes or Chroma. The repository ships no recipe content or prebuilt index.
See the [RAG documentation](../../rag/README.md) to configure it and index your own
content.

If import fails, verify the selected Windows interpreter and installed requirements.
If signatures are missing, run the builder on that machine and restart the server.
If a CAD call fails, keep the exact tool error message when reporting the problem.

Next: [MCP tools](../README.md), [curated CAD API](../../solidworks/README.md), and
[architecture](../../docs/ARCHITECTURE.md).
