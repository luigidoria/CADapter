# The MCP server

`mcp_server/mcp_solidworks.py` exposes SOLIDWORKS to Claude (or any MCP client) over
stdio. Every COM operation runs on **one dedicated thread** (a fixed COM apartment), so
calls are serialized and COM objects survive between tool calls.

It expects SOLIDWORKS to be **already open**. The curated layer (`solidworks/`) can also
launch it.

Rule of thumb: **a curated verb beats `sw_call`.** `sw_call` is the escape hatch, and
you can record lessons in your own recipes. If you have populated the optional RAG,
search your recipes before struggling with the same thing twice.

## Tools

| Tool | What for |
|---|---|
| `sw_verbs(domain)` | the **catalog** of curated verbs, one line each (`part`, `sketch`, `inspect`, `asm`, `dwg`, `sheet`) |
| `sw_verb_help(name)` | one verb's full docstring and parameters, including its **measured gotchas** |
| `sw_verb(name, params)` | run a curated verb by name, e.g. `sw_verb("sheet.new_sheet", '{"plane":"Front","w_mm":100,"h_mm":60}')` |
| `sw_api_signature(method, interface)` | the **complete** signature of a COM method: every argument, in order, with types and by-ref flags. Use it before `sw_call` |
| `sw_search_api(query, partition, top_k)` | semantic search over the recipes. Query in **English** |
| `sw_call(target, method, args, mm_args, nothing_args)` | run **any** COM method |
| `sw_get_prop(target, property_name)` | read any COM property |
| `sw_open_doc(action, path, doc_type)` | open (`open`) or create (`new`) a document |
| `sw_save_doc(path)` | save, or save-as when `path` is given |
| `sw_run_macro(macro_path, sub_name)` | run a VBA macro (`.swp` / `.swb`; a text `.swb` ignores `sub_name`) |
| `sw_reconnect()` | rebuild the COM link (clears the app and every handle) |
| `sw_list_handles()` / `sw_clear_handles()` | inspect / drop stored COM objects |
| `sw_new_part()` | convenience: new part from the default template |
| `sw_sketch_rectangle(plane, x1, y1, x2, y2)` | convenience: sketch + rectangle, in mm |
| `sw_extrude(depth_mm, reverse)` | convenience: extrude the most recent sketch, in mm |
| `sw_start_session()` / `sw_end_session()` | from the logging proxy: record a session to `sessions/*.jsonl` |

Tools **raise** on failure, so the result is marked as an error. They never return a
traceback dressed up as success.

## `target` in `sw_call` / `sw_get_prop`

| target | COM interface |
|---|---|
| `app` | `ISldWorks` |
| `doc` | `IModelDoc2` (active document) |
| `sketch_mgr` | `ISketchManager` |
| `feature_mgr` | `IFeatureManager` |
| `extension` | `IModelDocExtension` |
| `assembly` | `IAssemblyDoc` |
| `drawing` | `IDrawingDoc` |
| `@handle_N` | a stored COM object (see below) |

Targets use **dynamic dispatch**. Two consequences: selection goes through `extension`
(`SelectByID2` does not resolve on `doc`), and a method that takes no arguments resolves
as a property, so read it with `sw_get_prop` (`sw_get_prop("doc", "GetConfigurationNames")`).

### Arguments

- `args`: a JSON array of positional arguments.
  - `null` is an **out-parameter**, passed by reference with the type the signature
    index gives it (`Long`, `Double`, `String`, `Boolean`; a double when the index is not
    built). Its value comes back in `out_params`, e.g.
    `sw_call("app", "OpenDoc6", '["C:/x/a.sldasm", 2, 1, "", null, null]')` returns the
    `Errors` and `Warnings` codes as `out_param_4` / `out_param_5`.
  - `"@handle_N"` is a stored COM object.
- `mm_args`: indices of `args` that are in **millimetres** and must become metres.
- `nothing_args`: indices to pass as COM `Nothing` (a null object, e.g. an optional
  callback).

Result: `{"result": ..., "out_params": {...}}`. A returned COM object becomes
`{"$handle": "handle_N"}`.

## Handles

COM objects (a sketch, a feature, the selection manager) do not fit in JSON, so they are
stored and named `handle_N`. Reuse one in a later call:

```text
# call 1 returned {"result": {"$handle": "handle_3"}}
sw_call(target="@handle_3", method="GetName")              # as the target
sw_call(target="doc", method="Foo", args="[\"@handle_3\"]")  # or as an argument
```

`sw_reconnect()` and `sw_clear_handles()` drop them.

## Units

| Quantity | In tools and verbs | Internally |
|---|---|---|
| coordinates, depths, radii | millimetres | ÷ 1000 → metres |
| angles | degrees | → radians |

Convenience tools and **every** curated verb convert automatically. In `sw_call`, list
the millimetre arguments in `mm_args`.

One measured exception inside the API: `InsertSheetMetalJog` wants its angle in
**degrees**. Passing radians is a silent no-op. `sheet.jog` handles this for you.

## Example: a 50 × 30 × 20 mm block

Through the convenience tools:

```text
sw_new_part()
sw_sketch_rectangle("Front", 0, 0, 50, 30)
sw_extrude(20)
sw_save_doc("C:/.../block.sldprt")
```

Through the raw API, when no verb covers the case. Plane names are **literal** here: on
a Portuguese template the front plane is `"Plano frontal"`, not `"Front Plane"` (the
convenience tools and the curated verbs resolve the real name by themselves):

```text
sw_new_part()
sw_call(target="extension", method="SelectByID2",
        args="[\"Front Plane\", \"PLANE\", 0, 0, 0, false, 0, null, 0]", nothing_args="[7]")
sw_call(target="sketch_mgr", method="InsertSketch", args="[true]")
sw_call(target="sketch_mgr", method="CreateCornerRectangle",
        args="[0, 0, 0, 50, 30, 0]", mm_args="[0,1,2,3,4,5]")
sw_call(target="sketch_mgr", method="InsertSketch", args="[true]")
# ... select the sketch and call FeatureManager.FeatureExtrusion2
```

## Recipes

`sw_search_api(query, partition="recipe")` searches the curated recipes in
`rag/recipe/` after you install the optional dependencies, supply your own Markdown
recipes and build the index. No recipes or database are bundled. See the
[RAG setup guide](../rag/README.md) for the format and indexing commands.

Use the recipes to **discover** flows and gotchas, and `sw_api_signature` for the
**exact** argument list.

## Error recovery

| Problem | Fix |
|---|---|
| COM disconnected / SOLIDWORKS hung | `sw_reconnect()` · `sw_com.restart()` in the curated layer (kill and relaunch, ~23 s) |
| too many windows open / SaveAs failing | `sw_com.close_untitled()` or `sw_com.app().CloseAllDocuments(True)` |
| wrong selection | `sw_call(target="doc", method="ClearSelection2", args="[true]")` |
| "Parameter not optional" / wrong argument count | `sw_api_signature("MethodName")` |
| `sw_call` failed and it is not clear why | If you populated RAG, search your recipes with `sw_search_api(...)` |
| which method to use | `sw_api_signature("prefix")` suggests names by prefix / substring |
| an enum value | the makepy-generated constants module (`SOLIDWORKS Constant type library`) |
| which version is running | `sw_get_prop("app", "RevisionNumber")` |
