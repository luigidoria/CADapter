# Base setup

Use Windows, Python 3.12+ and an installed, licensed SOLIDWORKS. Run these commands
from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe mcp_server\build_api_signatures_typelib.py
```

The signature builder uses the locally registered type library (TYPELIB) and writes ignored
`mcp_server/cache/api_signatures.json`. SOLIDWORKS need not be running for that
step. It reads the highest registered SOLIDWORKS TYPELIB version and writes one
cache; with several versions registered, the cache describes the newest one. Rebuild
it after installing or upgrading SOLIDWORKS. Lookup supports the old
`rag_index/api_signatures.json` until a new cache exists.

Open SOLIDWORKS, then start your MCP client from this checkout. `.mcp.json` registers
`CADapter` running `mcp_server/mcp_solidworks.py`. Set the interpreter to the local
Windows venv if configuring an external client.

Configuration is optional. See `mcp_server/.env.example`; use
`mcp_server/.env` only for overrides. Existing root `.env` and the legacy
`SOLIDMCP_ENV_FILE` variable (the pre-CADapter name) remain supported.

Recipe search is optional. Missing recipes or Chroma must not prevent normal CAD
tools from starting.
Install [RAG](../../rag/README.md) separately to enable recipe search.

If import fails, verify the selected Windows interpreter and installed requirements.
If signatures are missing, run the builder on that machine and restart the server.
If a CAD call fails, capture the exact tool error; do not remove COM casts or
change argument order speculatively.

Next: [MCP tools](../README.md), [curated CAD API](../../solidworks/README.md), and
[architecture](../../docs/ARCHITECTURE.md).
