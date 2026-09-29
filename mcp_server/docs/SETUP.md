# Base setup

Use Windows, Python 3.12+ and an installed, licensed SOLIDWORKS. Run these commands
from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe mcp_server\build_api_signatures_typelib.py
```

The signature builder uses the locally registered typelib and writes ignored
`mcp_server/cache/api_signatures.json`. SOLIDWORKS need not be running for that
step. Rebuild separately on SW2017 and SW2026. Lookup supports the old
`rag_index/api_signatures.json` until a new cache exists.

Open SOLIDWORKS, then start your MCP client from this checkout. `.mcp.json` registers
`solidworks` through `mcp_server/sw_session_logger.py`; `.codex/config.toml` provides
the disabled-by-default Codex registration. Set the interpreter to the local
Windows venv if configuring an external client. To omit session logging, launch
`mcp_server/mcp_solidworks.py` directly.

Configuration is optional. See `mcp_server/.env.example`; use
`mcp_server/.env` only for overrides. Existing root `.env` and `SOLIDMCP_ENV_FILE`
remain supported. Session logs are ignored local data and may contain user content.

Recipe search is optional. Missing recipes or Chroma must not prevent normal CAD
tools from starting.
Install [RAG](../../rag/README.md) separately to enable recipe search.

If import fails, verify the selected Windows interpreter and installed requirements.
If signatures are missing, run the builder on that machine and restart the server.
If a CAD call fails, capture the exact tool error and run the smallest matching
domain test; do not remove COM casts or change argument order speculatively.
