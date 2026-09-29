# CADapter: guide for Claude Code

This repository exposes SOLIDWORKS to LLMs over COM. The MCP server
(`mcp_server/mcp_solidworks.py`) loads from `.mcp.json` when you open Claude Code in this
folder; SOLIDWORKS must already be running.

## Working with SOLIDWORKS through the tools

- **A curated verb beats `sw_call`.** Start with `sw_verbs(domain)` to see the catalog,
  read `sw_verb_help(name)` before calling a verb you have not used (its docstring holds
  the measured gotchas), then `sw_verb(name, params)`.
- `sw_call` is the escape hatch for anything the verbs do not cover. Before using it, get
  the exact argument list with `sw_api_signature("MethodName")`. If you have populated
  the optional RAG, search your recipes with
  `sw_search_api("...", partition="recipe")` (queries in English).
  Recipe content and a prebuilt database are not bundled.
- Units: millimetres and degrees in every tool and verb. In `sw_call`, list the
  millimetre argument indices in `mm_args`.
- **A return value is not evidence.** Many SOLIDWORKS methods report success without
  building anything. After building, run `inspect.validate` (rebuild errors, mate
  errors, interferences, missing references) and check the geometry against the intent
  with `inspect.measure` (distances, angles, diameters), mass and bounding box.
- **On a file you did not build, read before you change.** `inspect.model_summary` first
  (features, dimension names, mates, views, health), then `inspect.sketch(name)` for a
  sketch's geometry and relations, `asm.mates(comp)` + `asm.free_dof(comp)` for why a part
  can still move, `dwg.list_views` for a drawing's views.
- Hygiene: ~20 open documents make SOLIDWORKS crawl, and SaveAs fails on a file that is
  open. `sw_com.close_untitled()` closes every never-saved document WITHOUT saving; use
  it only in a session with no unsaved work you need. Never call `sw_com.restart()` as
  routine troubleshooting: it force-kills every SOLIDWORKS process.

References: [mcp_server/README.md](mcp_server/README.md) (tools, handles, units),
[solidworks/README.md](solidworks/README.md) (every verb, and the gotchas),
[solidworks/docs/SHEET_METAL.md](solidworks/docs/SHEET_METAL.md).

## Working on the code

- **All code is in English**, comments included. The exceptions are data, not code:
  the Portuguese plane names of a pt-BR SOLIDWORKS template (`"Plano frontal"`), and the
  pt-BR halves of the tables that match user text (boolean synonyms such as `"nao"` /
  `"sim"`). Translating those breaks the product silently.
- Record new COM lessons in authored `rag/recipe/` guidance and rebuild the recipe index.
- A new curated verb must never identify "the feature I just created" with
  `FeatureByPositionReverse(0)`: on a sheet-metal part that is always `Flat-Pattern1`.

Before calling a change done, run the deterministic checks (seconds, no CAD):

```powershell
$env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python.exe -u solidworks\tests\unit\check_catalog.py
```

and, if the change touches a verb, the smoke test of its domain with SOLIDWORKS open
(`solidworks\tests\smoke\smoke_<domain>.py <block>`). Everything else is in [docs/TESTING.md](docs/TESTING.md).
