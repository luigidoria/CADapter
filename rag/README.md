# Optional recipe search (RAG)

V1 includes search, the recipe index builder, dependencies and configuration.
No recipe content or prebuilt database is included. Each user supplies their own
knowledge and builds an index locally. The base MCP server works without Chroma
or an index; recipe search then reports a setup error. TYPELIB signatures belong
to `mcp_server/`, independently of RAG.


## Populate and build

From the repository root, using the environment that runs the MCP server:

```powershell
.venv\Scripts\python.exe -m pip install -r rag/requirements.txt
New-Item -ItemType Directory -Force rag/recipe
```

Create your own UTF-8 `.md` files directly in `rag/recipe/` (one recipe per file;
subdirectories are not indexed). Use this structure:

```markdown
# Descriptive title with relevant API method names
keywords: English search terms and method names

Your actionable guidance, prerequisites, observed failure mode and workaround.
```

Use English titles, keywords, body text and search queries for the default embedding
model. Once you have added at least one recipe, run:

```powershell
.venv\Scripts\python.exe rag/build_rag_recipe.py
```

The embedding backend may download its model on first use. The builder creates the
local Chroma database and the `sw_recipe` collection. Restart MCP after indexing,
then query `sw_search_api` with `partition="recipe"` and terms from your content.
Running the builder again updates recipes by filename; it does not remove entries
for deleted or renamed files. For a clean rebuild, stop MCP and move the existing
index aside before building again.

## Local data and configuration

`recipe/` and `index/` are ignored by Git and excluded from public releases. Keep
your own backups; neither your Markdown knowledge nor your generated database is
part of the distribution. Extracted vendor documentation and derived indexes must
not be distributed either.

The default index directory is `rag/index`. For a different location, copy
`rag/.env.example` to `rag/.env` and set `CADAPTER_RAG_INDEX` to an absolute path.
Create its parent directory first and use the same setting for the builder and MCP.
Keep external index directories outside version control as well.

Boundary tests are in `mcp_server/tests/test_knowledge.py`; they do not require Chroma.
Live search requires the optional dependencies, your recipes and a built index.
