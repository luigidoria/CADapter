# CADapter

CADapter exposes SOLIDWORKS parts, assemblies, drawings and inspection through MCP
and a curated Python API. Windows, Python 3.12+ and a licensed SOLIDWORKS installation
are required. SOLIDWORKS 2017 and 2026 are compatibility targets; consult the release
validation record for the tested scope.

From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe mcp_server/build_api_signatures_typelib.py
```

Open SOLIDWORKS and configure an MCP client using `.mcp.json`. See
[setup](mcp_server/docs/SETUP.md), [MCP tools](mcp_server/README.md), and the
[curated API](solidworks/README.md). Public units are millimetres and degrees.
Generate signature caches separately for each installed SOLIDWORKS version.

The [local engine](engine/README.md) compiles and executes bundles using the shared
[verb catalog](verb_catalog/README.md).
Optional [recipe search](rag/README.md) infrastructure is included from V1. Install
its dependencies, supply your own Markdown recipes and build the index locally.
No knowledge content or database is bundled; CAD tools do not depend on search.
