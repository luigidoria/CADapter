# Architecture

The base MCP server delegates curated operations to `solidworks/` and exposes generic
COM calls for API exploration. COM calls stay serialized on their initialized thread;
preserve early-binding/recast rules and API-specific conversions.

Signature lookup uses a cache generated from the local SOLIDWORKS typelib. It remains
independent of optional semantic recipe search. Existing environment names and MCP
tool names are compatibility contracts even though the display name is CADapter.

`engine/` owns local execution, allowlist and path checks; `verb_catalog/` owns
schemas and pure-Python compilation. Optional integrations use the same engine.
`rag/` provides optional search and index generation from V1. Users supply their
own recipes and build a local index; knowledge content and databases are not
distributed. Removing it disables semantic search while leaving normal CAD
operations available.
