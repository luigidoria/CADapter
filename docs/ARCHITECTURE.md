# Architecture

CADapter separates CAD behavior from the AI system or automation client that requests
it. The base V1 path is:

```text
MCP client
    |
    v
mcp_server/mcp_solidworks.py
    |
    v
solidworks/catalog.py -> curated domain module
    |
    v
serialized COM apartment -> SOLIDWORKS
```

CADapter is the automation and interface layer in this flow. It does not contain or
require an AI model.

## Base modules

| Module | Responsibility |
|---|---|
| `solidworks/` | The single curated implementation of part, sketch, inspection, assembly, drawing, and sheet-metal operations |
| `mcp_server/` | MCP transport, tool discovery, generic COM dispatch, handles, and local TYPELIB signature lookup |
| `config_env.py` | Configuration precedence shared by the base and optional modules |
| `docs/` and module READMEs | Public setup, API, architecture, and validation guidance |

The base requires no local-model frontend.

## Curated operations and the generic path

The curated catalog reflects the public functions defined by six domain modules. It
currently exposes 193 domain-qualified function entries, including inspection and
helpers. This is an API count, not a test count; see the
[catalog definition and counts](../solidworks/README.md). Those functions own unit conversion, localized plane-name
resolution, early-bound COM interfaces, returned-object recasts, and workarounds for
version-specific API behavior.

`sw_verbs`, `sw_verb_help`, and `sw_verb` expose that catalog over MCP. `sw_call` and
`sw_get_prop` provide a lower-level escape hatch on the MCP server's supported targets.
The generic path is useful for API exploration, but it does not replace the measured
behavior of curated verbs and does not guarantee that every COM method marshals through
Python automation.

The implemented domain surface is documented in the
[curated API guide](../solidworks/README.md). Exact method signatures for generic calls
come from the registered SOLIDWORKS TYPELIB, not from semantic search.

## COM execution model

SOLIDWORKS COM objects are apartment-threaded. The MCP server initializes one dedicated
worker thread and serializes COM access on it. Objects returned by COM often need an
explicit early-bound interface recast before their methods are reliable.

JSON callers cannot carry COM objects directly. The MCP server therefore stores
returned objects in a session-local handle table and returns names such as
`@handle_3`. Handles are valid only while their source document and session remain
alive. Optional frontends can implement the same boundary with their own handle prefix.

Public distances are millimetres and public angles are degrees. Conversion happens at
the curated boundary or, for generic calls, only at argument positions explicitly
identified by the caller.

## Local API information and optional search

`mcp_server/build_api_signatures_typelib.py` reads the highest registered SOLIDWORKS
TYPELIB and writes an ignored local cache under `mcp_server/cache/`. The cache belongs
to the installation that generated it and must be rebuilt after installing or upgrading
SOLIDWORKS.

`rag/` is optional. It can index user-authored Markdown recipes into a local Chroma
database. Recipe content, proprietary SOLIDWORKS documentation, and generated
databases are not distributed.

The base server imports recipe search lazily. Missing RAG dependencies or indexes affect
`sw_search_api`, not normal CAD operations or TYPELIB signature lookup.


## Validation and distribution

The public test ladder covers deterministic contracts, installation smoke, domain
regression, and the same-commit SW2017/SW2026 comparison. See
[testing](TESTING.md) for the frozen result and [test levels](../tests/README.md) for
safe execution.

The public V1 distribution contains the base MCP layer, optional recipe-search
infrastructure, and selected tests. Recipe content and generated indexes are supplied
locally by the user.

## Related documentation

- [Base setup](../mcp_server/docs/SETUP.md)
- [MCP tools](../mcp_server/README.md)
- [Curated CAD API](../solidworks/README.md)
- [Optional recipe search](../rag/README.md)
- [Testing and validation](TESTING.md)
