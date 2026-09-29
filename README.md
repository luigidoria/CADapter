# CADapter

CADapter is a SOLIDWORKS automation layer for AI systems. It provides curated,
measured operations for creating, editing, inspecting, and validating parts,
assemblies, sheet-metal models, and technical drawings through MCP and Python.
CADapter is not an AI model. An AI system or another automation client decides what
to build; CADapter translates that intent into native SOLIDWORKS operations and
returns structured CAD results.

AI systems can build and edit parts, assemble components with mechanical mates,
create technical drawings, measure geometry, and check model health and interference.

The base requires Windows, Python 3.12 or newer, and a licensed SOLIDWORKS
installation. CADapter V1 was validated on SOLIDWORKS 2017 (25.3.0) and SOLIDWORKS
2026 (34.3.2). Both completed all 543 required compatibility steps. The detailed
results and investigated version-dependent differences are in the
[validation record](docs/TESTING.md#frozen-v1-validation).

## Showcases

The engineering workflow connects:

**AI instruction -> CADapter -> native SOLIDWORKS result**

### Inline-2 Engine

**Complete CAD Workflow**

[Watch the demonstration](showcases/inline-2-engine/demo.mp4)

A two-cylinder engine case study covering multiple CAD stages, from individual
components to a mechanism and its technical drawings. The focus is workflow breadth,
not only geometry generation.

**part creation -> assembly -> motion -> technical drawings**

[View the full showcase](showcases/inline-2-engine/README.md) · [Prompt](showcases/inline-2-engine/prompt.md)

### Vintage Beam Engine

**Requirements-Driven Design**

[Watch the demonstration](showcases/vintage-beam-engine/demo.mp4)

An engineering brief defines system constraints; the AI system determines dimensions,
proportions and linkages using CADapter. Full rotation and freedom from unintended
interference are acceptance criteria, not assumed outcomes.

**requirements + constraints -> detailed design -> parts -> assembly -> validated motion**

[View the full showcase](showcases/vintage-beam-engine/README.md) · [Prompt](showcases/vintage-beam-engine/prompt.md)

For reproducible engineering checks available now, see the
[part creation and inspection workflow](solidworks/tests/smoke/smoke_parts.py), the
[usable technical drawing benchmark](solidworks/tests/benchmark/benchmark_drawings.py),
and the [constrained clevis workflow](solidworks/tests/smoke/smoke_clevis.py).

## Current capabilities

| Domain | Current scope |
|---|---|
| Parts and sketches | parametric geometry, features, patterns, datums, materials, properties, configurations, export |
| Inspection and measurement | topology, dimensions, mass, model summaries, validation |
| Assemblies | components, mechanical mates, motion limits, interference checks, exploded views, export |
| Drawings | views, dimensions, GD&T, tables, layout, quality checks, PDF export |
| Sheet metal | base features, bends, flanges, fabrication data, flattening, DXF export |

The public API exposes 193 registered curated entries across six domains: parts,
sketches, inspection, assemblies, drawings, and sheet metal.
Public distances use millimetres and angles use degrees.

Use curated verbs first. They contain the language handling, unit conversions, COM
recasts, and measured workarounds required by supported workflows. `sw_call` remains a
generic escape hatch for methods outside the curated surface; it is not a promise that
every SOLIDWORKS COM operation works through automation.

See the [curated API guide](solidworks/README.md) for the implemented operations and
their measured limits.

## Architecture

```text
AI system or automation client
             |
             v
     MCP server (stdio)
             |
             v
  curated CAD verb catalog
             |
             v
serialized Windows COM thread -> SOLIDWORKS
```

`mcp_server/` exposes discovery, help, curated execution, signature lookup, and a
limited generic COM path. `solidworks/` owns the CAD behavior and is the single V1
verb implementation. Optional `rag/` support searches user-authored local recipes;
normal CAD operations do not depend on it. API signatures are generated locally from
the registered SOLIDWORKS TYPELIB.

See [architecture](docs/ARCHITECTURE.md) for COM affinity, handles, optional modules,
and optional recipe search.

## Getting started

Run from the repository root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe mcp_server/build_api_signatures_typelib.py
```

Open SOLIDWORKS, then configure an MCP client with `.mcp.json`. See the
[setup guide](mcp_server/docs/SETUP.md), [MCP tool reference](mcp_server/README.md),
and [curated API guide](solidworks/README.md).

## Documentation

- [Architecture and boundaries](docs/ARCHITECTURE.md)
- [Base setup](mcp_server/docs/SETUP.md)
- [MCP tools and generic calls](mcp_server/README.md)
- [Curated CAD API](solidworks/README.md)
- [Sheet-metal details](solidworks/docs/SHEET_METAL.md)
- [Optional recipe search](rag/README.md)
- [Testing levels and safety](tests/README.md)
- [Frozen V1 validation](docs/TESTING.md#frozen-v1-validation)

## Scope and limitations

- Live CAD execution requires Windows and SOLIDWORKS. Linux can run structural checks,
  but not COM validation.
- V1 was measured on SOLIDWORKS 2017 and 2026. Releases between them were not part of
  the frozen cross-version run.
- Hole Wizard, several advanced pattern/body operations, and some drawing tables are
  outside the curated surface because their automation paths were not reliable. See
  the [curated API limits](solidworks/README.md#limits).
- Optional recipe search includes no SOLIDWORKS documentation, recipe content,
  or prebuilt database. Users build local indexes from content they are
  entitled to use.

## License

CADapter is licensed under [Apache-2.0](LICENSE).

## Validation provenance

The frozen V1 measurements apply to code commit
`2490209328ffc48212dc7900d65d3ead6a173085`. The later baseline refresh and
documentation changes do not imply another SOLIDWORKS run. See the
[validation record](docs/TESTING.md#frozen-v1-validation) for the complete results.
