"""
mcp_solidworks.py
MCP server for driving SolidWorks through COM automation.

Architecture:
  - Generic tools (sw_call / sw_get_prop / sw_search_api / sw_open_doc /
    sw_save_doc / sw_run_macro) cover the whole COM API.
  - Convenience tools (sw_new_part / sw_sketch_rectangle / sw_extrude) give a
    deterministic "golden path" for the common modelling flow.
  - A handle registry lets COM objects returned by one call be reused in the next.

Every COM operation runs on a single dedicated thread (fixed apartment,
CoInitialize once), preserving apartment affinity and handle reuse.
On failure the tools RAISE (the MCP marks isError=true) -- they never hand back
a traceback as if it were a success.
"""

import os
import sys
import json
import concurrent.futures

import pythoncom
import pywintypes
import win32com.client
import win32com.client.dynamic
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server import knowledge

mcp = FastMCP("CADapter")

# ── Dedicated COM executor (single thread, fixed apartment) ───────────────────
# FastMCP runs tools on worker threads; COM is apartment-threaded and needs a
# CoInitialize per thread. Serializing everything onto one thread solves
# CoInitialize, apartment affinity (required by the handles) and call ordering.

_com_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="sw-com"
)
_com_init_done = False


def _on_com_thread(fn, *args, **kwargs):
    def runner():
        global _com_init_done
        if not _com_init_done:
            pythoncom.CoInitialize()
            _com_init_done = True
        return fn(*args, **kwargs)

    return _com_pool.submit(runner).result()


def _run_com_tool(impl, *args, **kwargs) -> str:
    """Run impl on the COM thread and serialize the returned dict as JSON.
    A com_error becomes a RuntimeError with a readable message; other exceptions
    bubble up as they are. Either way FastMCP marks the result as an error."""
    try:
        result = _on_com_thread(impl, *args, **kwargs)
    except pythoncom.com_error as exc:
        raise RuntimeError(_format_com_error(exc)) from None
    return json.dumps(result, ensure_ascii=False, indent=2)


def _format_com_error(exc: pythoncom.com_error) -> str:
    try:
        hr = exc.args[0]
        msg = exc.args[1]
        info = exc.args[2] if len(exc.args) > 2 else None
        detail = info[2] if info and len(info) > 2 and info[2] else ""
        return f"COM error {hr}: {msg}. {detail}".strip()
    except Exception:
        return f"COM error: {exc}"


# ── Connection and targets ────────────────────────────────────────────────────
# SOLIDWORKS automation uses late binding (dynamic dispatch) here. Early binding
# through makepy is not viable on this path: EnsureDispatch/EnsureModule fail for
# this typelib and pollute the gen_py cache on top of it. Dynamic dispatch
# resolves most methods; the notable exception is SelectByID2, which does NOT
# resolve on `doc` -- which is why selection always goes through `extension`
# (IModelDocExtension).

_app = None  # cached instance (lives on the COM thread)


def _get_app():
    global _app
    if _app is None:
        # Dynamic dispatch EXPLICITLY: win32com.client.GetActiveObject wraps through
        # the gen_py cache, and once the curated layer has created that cache (first
        # sw_verb) the app came back early-bound -- byref VARIANTs then raised
        # 'Type mismatch' in OpenDoc6/RunMacro2 and RevisionNumber turned into a
        # bound method. Every call in this module is written for dynamic dispatch.
        try:
            unk = pythoncom.GetActiveObject(pywintypes.IID("SldWorks.Application"))
            _app = win32com.client.dynamic.Dispatch(
                unk.QueryInterface(pythoncom.IID_IDispatch))
        except Exception:
            raise RuntimeError(
                "SolidWorks is not running. Open SolidWorks before using the tools."
            )
    return _app


def _null_dispatch():
    """Null COM object (Nothing) for optional dispatch parameters such as
    SelectByID2's Callout -- passing Python None causes 'Type mismatch'."""
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)


def _active_doc():
    doc = _get_app().ActiveDoc
    if doc is None:
        raise RuntimeError(
            "No active document. Use sw_open_doc or sw_new_part first."
        )
    return doc


def _get_target(target: str):
    if target.startswith("@"):
        return _resolve_handle_ref(target)
    if target == "app":
        return _get_app()
    doc = _active_doc()
    subobjs = {
        "doc":         lambda: doc,
        "sketch_mgr":  lambda: doc.SketchManager,
        "feature_mgr": lambda: doc.FeatureManager,
        "extension":   lambda: doc.Extension,
        "assembly":    lambda: doc,   # best-effort: some IAssemblyDoc /
        "drawing":     lambda: doc,   # IDrawingDoc methods may not resolve dynamically
    }
    if target not in subobjs:
        raise ValueError(
            f"Target '{target}' is invalid. Options: app, doc, sketch_mgr, "
            f"feature_mgr, extension, assembly, drawing, or @handle_N."
        )
    return subobjs[target]()


def _mm(value: float) -> float:
    return value / 1000.0


# ── COM object handle registry ────────────────────────────────────────────────
# COM objects do not survive json.dumps; we store them by id for later reuse.

_handles: dict = {}
_handle_counter = 0


def _is_com(val) -> bool:
    return hasattr(val, "_oleobj_")


def _store_handle(obj) -> str:
    global _handle_counter
    _handle_counter += 1
    hid = f"handle_{_handle_counter}"
    _handles[hid] = obj
    return hid


def _jsonify(val):
    """Turn a COM return into something serializable; objects become {'$handle': id}."""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (list, tuple)):
        return [_jsonify(v) for v in val]
    if isinstance(val, dict):
        # curated verbs return dicts (mass, list_features, bend_info, cut_list):
        # without this branch they reached the model as a Python repr string
        return {str(k): _jsonify(v) for k, v in val.items()}
    if _is_com(val):
        return {"$handle": _store_handle(val)}
    return str(val)


def _resolve_handle_ref(val):
    """Resolve '@handle_N' to the stored object; other values pass straight through."""
    if isinstance(val, str) and val.startswith("@handle_"):
        hid = val[1:]
        if hid not in _handles:
            raise RuntimeError(f"Handle '{hid}' does not exist. Use sw_list_handles().")
        return _handles[hid]
    return val


# ── Structured signature index (lazy init) ───────────────────────────────────

_SIG_INDEX: dict | None = None
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIG_INDEX_PATH = str(knowledge.signature_index_path())


def _get_sig_index() -> dict:
    global _SIG_INDEX
    if _SIG_INDEX is None:
        if not os.path.exists(_SIG_INDEX_PATH):
            raise RuntimeError(
                "Signature index not found. Build it from this machine's own "
                "SolidWorks typelib:\n"
                "    python mcp_server/build_api_signatures_typelib.py\n"
                "It is not shipped with the repository: the index is derived from "
                "the installed SolidWorks version, and the older HTML-based builder "
                "needs Dassault documentation that cannot be redistributed."
            )
        with open(_SIG_INDEX_PATH, encoding="utf-8") as f:
            _SIG_INDEX = json.load(f)
    return _SIG_INDEX


@mcp.tool()
def sw_api_signature(method: str, interface: str = "") -> str:
    """
    Returns the COMPLETE signature (every parameter, in order) of a SolidWorks COM
    method -- untruncated. Use it BEFORE sw_call when you need the exact number and
    order of arguments.
    method: the exact name (e.g. "FeatureCut4"). Case-insensitive.
    interface: optional -- filters when the method exists on more than one interface.
    With no exact match it suggests nearby names (prefix/contains).
    """
    index = _get_sig_index()
    key = method.strip().lower()

    # Exact match
    if key in index:
        variants = index[key]
        if interface:
            iface_lower = interface.lower()
            filtered = [v for v in variants if v["interface"].lower() == iface_lower]
            if filtered:
                variants = filtered
        return json.dumps(variants, ensure_ascii=False, indent=2)

    # Fallback: prefix or contains
    candidates = [
        k for k in index
        if k.startswith(key) or key in k
    ]
    if not candidates:
        return json.dumps(
            {"error": f"Method '{method}' not found.", "suggestions": []},
            ensure_ascii=False, indent=2,
        )

    suggestions = []
    for c in sorted(candidates)[:8]:
        for v in index[c]:
            suggestions.append({
                "method": v["method"],
                "interface": v["interface"],
                # The typelib builder writes only `params`; old doc-derived caches also
                # carry `arg_count`. Both mean the same number.
                "arg_count": v.get("arg_count", len(v.get("params") or [])),
            })

    return json.dumps(
        {"error": f"Method '{method}' not found (exact match).", "suggestions": suggestions},
        ensure_ascii=False, indent=2,
    )


# Recipe search is optional; the tool identity remains stable for existing clients.
@mcp.tool()
def sw_search_api(query: str, partition: str = "recipe", top_k: int = 3) -> str:
    """Search the optional local recipe index. Query in English.

    Use sw_api_signature for exact COM argument lists. Missing RAG installation or
    index is an explicit tool error; it does not disable CAD operations.
    """
    return knowledge.search(query, partition, top_k)


@mcp.tool()
def sw_call(
    target: str,
    method: str,
    args: str = "[]",
    mm_args: str = "[]",
    nothing_args: str = "[]",
) -> str:
    """
    Run a SolidWorks COM method. target: app/doc/sketch_mgr/feature_mgr/
    extension/assembly/drawing, or "@handle_N" for a stored object.
    args: JSON array (null=byref out-param, typed from the signature index when it
    is built -- Long, Double, String, Boolean -- else a double; "@handle_N"=stored
    object).
    mm_args: indices of args given in mm, to convert to meters.
    nothing_args: indices to pass as COM Nothing (a null object).
    Returns {"result": ..., "out_params": {...}}. Returned COM objects become
    {"$handle": "handle_N"} and can be reused in later calls.
    """
    return _run_com_tool(_sw_call_impl, target, method, args, mm_args, nothing_args)


# Dynamic dispatch passes a byref out-param with the exact VARTYPE it is given, and
# SolidWorks refuses a mismatch ('Type mismatch' on OpenDoc6's `long` Errors when it
# arrived as a double). The signature index knows each parameter's type.
_TARGET_IFACE = {"app": "SldWorks", "doc": "ModelDoc2", "extension": "ModelDocExtension",
                 "sketch_mgr": "SketchManager", "feature_mgr": "FeatureManager",
                 "assembly": "AssemblyDoc", "drawing": "DrawingDoc"}
_BYREF_VT = {"long": (pythoncom.VT_I4, 0), "integer": (pythoncom.VT_I2, 0),
             "double": (pythoncom.VT_R8, 0.0), "boolean": (pythoncom.VT_BOOL, False),
             "string": (pythoncom.VT_BSTR, "")}


def _out_param_type(target, method, idx, nargs):
    """(VARTYPE, initial value) for out-param `idx`; a double when the index is not
    built or the method's overloads disagree on the type."""
    fallback = (pythoncom.VT_R8, 0.0)
    try:
        entries = _get_sig_index().get(method.lower(), [])
    except RuntimeError:
        return fallback
    iface = _TARGET_IFACE.get(target)
    entries = [e for e in entries if e["interface"] == iface] or entries
    types = {e["params"][idx]["type"].lower() for e in entries
             if len(e["params"]) == nargs and e["params"][idx]["byref"]}
    return _BYREF_VT.get(types.pop(), fallback) if len(types) == 1 else fallback


def _sw_call_impl(target, method, args, mm_args, nothing_args):
    obj = _get_target(target)
    arg_list = json.loads(args)
    mm_indices = set(json.loads(mm_args))
    nothing_indices = set(json.loads(nothing_args))

    coerced = []
    for idx, val in enumerate(arg_list):
        val = _resolve_handle_ref(val)
        if val is None and idx in nothing_indices:
            coerced.append(win32com.client.VARIANT(pythoncom.VT_DISPATCH, None))
        elif val is None:
            vt, init = _out_param_type(target, method, idx, len(arg_list))
            coerced.append(win32com.client.VARIANT(pythoncom.VT_BYREF | vt, init))
        elif idx in mm_indices and isinstance(val, (int, float)) and not isinstance(val, bool):
            coerced.append(_mm(float(val)))
        else:
            coerced.append(val)

    result = getattr(obj, method)(*coerced)

    out_vals = {}
    for idx, val in enumerate(coerced):
        if isinstance(val, win32com.client.VARIANT):
            out_vals[f"out_param_{idx}"] = _jsonify(val.value)

    return {"result": _jsonify(result), "out_params": out_vals}


# ── CURATED verbs (solidworks/) exposed by name ───────────────────────────────
# Up to here the tools covered the RAW COM API (sw_call) and three conveniences. The
# semantic verbs of the six `solidworks/` modules -- which are where the measured
# gotchas are already resolved internally -- were only reachable by writing a Python
# script. These three tools close that gap for every domain at once:
# catalog, help and dispatcher.
#
# A usage rule worth more than the list: CURATED VERB > sw_call. sw_call is the escape
# hatch. If `sw_verbs` shows a verb for what you want, use it -- it already resolves the
# selection, the marks and the method versions that cost whole measurement rounds.

_catalog = None
_curated_connected = False


def _get_catalog():
    global _catalog
    if _catalog is None:
        sys.path.insert(0, _ROOT)
        from solidworks import catalog as _k
        _catalog = _k
    return _catalog


def _connect_curated():
    """`C.connect()` once per process, ON THE COM THREAD.

    The curated layer uses early binding and has its own connection (it ATTACHES to a
    running SW or LAUNCHES one). It does not replace `_get_app()` of the generic tools:
    the two coexist in the same process, and that is why a handle created by a curated
    verb works as a `target` in a following sw_call -- both are COM objects with
    `_oleobj_`.
    """
    global _curated_connected
    if not _curated_connected:
        sys.path.insert(0, _ROOT)
        from solidworks import sw_com as C
        C.connect()
        _curated_connected = True


def _resolve_deep(val):
    """Resolve '@handle_N' INSIDE lists and dicts too.

    A sheet metal verb takes geometry in a list (`sheet.corner_trim(edges=[...])`,
    `sheet.gusset(faces=[...])`, `inspect.select`): resolving only at the top level
    would leave exactly those out.
    """
    if isinstance(val, list):
        return [_resolve_deep(v) for v in val]
    if isinstance(val, dict):
        return {k: _resolve_deep(v) for k, v in val.items()}
    return _resolve_handle_ref(val)


@mcp.tool()
def sw_verbs(domain: str = "") -> str:
    """
    CATALOG of the curated verbs (what to use BEFORE falling back to sw_call).
    domain: "" (all) | part | sketch | inspect | asm | dwg | sheet.
    Returns {verb: one-line summary}. Detail and gotchas: sw_verb_help.
    """
    k = _get_catalog()
    try:
        return json.dumps({"domains": {d: len(k.list_verbs(d)) for d in k.DOMAINS},
                           "verbs": k.catalog(domain)},
                          ensure_ascii=False, indent=2)
    except k.VerbError as exc:
        raise RuntimeError(str(exc)) from None


@mcp.tool()
def sw_verb_help(name: str) -> str:
    """
    FULL docstring and parameters of a curated verb (e.g. "sheet.jog").
    This is where the MEASURED gotchas are -- that the jog's angle goes in degrees,
    that the K-factor only changes through the flange. Reading it before calling
    saves repeating the measurement.
    """
    k = _get_catalog()
    try:
        return json.dumps(k.help_for(name), ensure_ascii=False, indent=2, default=str)
    except k.VerbError as exc:
        raise RuntimeError(str(exc)) from None


@mcp.tool()
def sw_verb(name: str, params: str = "{}") -> str:
    """
    Run a curated verb by NAME. E.g. name="sheet.new_sheet",
    params='{"plane":"Front","w_mm":100,"h_mm":60,"thickness_mm":2}'.
    params: a JSON object; "@handle_N" (inside a list too) becomes the stored COM
    object. Units: mm and degrees, as in the verbs. Returned COM objects become
    {"$handle": "handle_N"} and work as arguments in the next call.
    """
    return _run_com_tool(_sw_verb_impl, name, params)


def _sw_verb_impl(name, params):
    k = _get_catalog()
    try:
        args = json.loads(params or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"params is not valid JSON: {exc}") from None
    if not isinstance(args, dict):
        raise RuntimeError("params must be a JSON object (e.g. '{\"mm\": 2}').")
    # the verb name is checked BEFORE connecting: a refusal of FORM must not cost a
    # trip to SolidWorks (the lesson had already been paid for on the agent side --
    # pre-CAD rejection is free)
    try:
        k.verb(name)
    except k.VerbError as exc:
        raise RuntimeError(str(exc)) from None
    _connect_curated()
    try:
        result = k.call(name, args, resolver=_resolve_deep)
    except k.VerbError as exc:
        raise RuntimeError(str(exc)) from None
    return {"verb": name, "result": _jsonify(result)}


@mcp.tool()
def sw_get_prop(target: str, property_name: str) -> str:
    """
    Read a COM property of a SolidWorks interface. target: the same as sw_call
    (including "@handle_N"). Returns {"property": ..., "value": ...}.
    """
    return _run_com_tool(_sw_get_prop_impl, target, property_name)


def _sw_get_prop_impl(target, property_name):
    obj = _get_target(target)
    val = getattr(obj, property_name)
    return {"property": property_name, "value": _jsonify(val)}


@mcp.tool()
def sw_open_doc(action: str, path: str = "", doc_type: str = "part") -> str:
    """
    Open or create a document in SolidWorks.
    action="open" requires the full path; action="new" uses doc_type:
    part/assembly/drawing. For "new", the default template configured in SW is
    resolved automatically.
    """
    return _run_com_tool(_sw_open_doc_impl, action, path, doc_type)


_DOC_TYPE = {"part": 1, "assembly": 2, "drawing": 3}
# swUserPreferenceStringValue_e: Part=8, Assembly=9, Drawing=10
_TEMPLATE_PREF = {"part": 8, "assembly": 9, "drawing": 10}


def _new_doc(app, doc_type):
    if doc_type not in _DOC_TYPE:
        raise ValueError(f"invalid doc_type: '{doc_type}'. Use: {list(_DOC_TYPE)}")
    template = app.GetUserPreferenceStringValue(_TEMPLATE_PREF[doc_type])
    if not template:
        raise RuntimeError(
            f"No default '{doc_type}' template configured in SolidWorks "
            f"(Tools > Options > Default Templates)."
        )
    doc = app.NewDocument(template, 0, 0, 0)
    if doc is None:
        raise RuntimeError(f"NewDocument failed (template='{template}').")
    return doc


def _sw_open_doc_impl(action, path, doc_type):
    app = _get_app()
    if action == "open":
        if not path:
            raise ValueError("path is required for action='open'")
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = app.OpenDoc6(path, _DOC_TYPE.get(doc_type, 1), 1, "", errors, warnings)
        if doc is None:
            raise RuntimeError(f"Failed to open '{path}' (errors={errors.value}).")
        return {"status": "ok", "title": doc.GetTitle}
    if action == "new":
        doc = _new_doc(app, doc_type)
        return {"status": "ok", "title": doc.GetTitle}
    raise ValueError(f"invalid action: '{action}'. Use 'open' or 'new'.")


@mcp.tool()
def sw_save_doc(path: str = "") -> str:
    """
    Save the active document. Empty path=Save, path given=SaveAs.
    """
    return _run_com_tool(_sw_save_doc_impl, path)


def _sw_save_doc_impl(path):
    doc = _active_doc()
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        ok = doc.SaveAs4(path, 0, 0, errors, warnings)
        if not ok:
            raise RuntimeError(f"SaveAs4 failed (errors={errors.value}).")
        return {"status": "ok", "path": path}
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    ok = doc.Save3(1, errors, warnings)
    if not ok:
        raise RuntimeError(f"Save3 failed (errors={errors.value}).")
    return {"status": "ok", "path": doc.GetPathName}


@mcp.tool()
def sw_run_macro(macro_path: str, sub_name: str = "main") -> str:
    """
    Run a SolidWorks VBA macro (.swp or .swb).
    sub_name: the name of the VBA Sub to call (default: "main"). A text .swb macro
    runs as a script and ignores sub_name. "ok" means SolidWorks ran the macro, not
    that the macro did what it meant to: check the model afterwards.
    """
    return _run_com_tool(_sw_run_macro_impl, macro_path, sub_name)


def _sw_run_macro_impl(macro_path, sub_name):
    # Checked HERE, not left to SolidWorks: with a missing file RunMacro2 opens a modal
    # error box and never returns (measured on SW 2026), which would wedge the COM thread.
    if not os.path.isfile(macro_path):
        raise FileNotFoundError(f"Macro not found: '{macro_path}'.")
    if os.path.splitext(macro_path)[1].lower() not in (".swp", ".swb"):
        raise ValueError(f"Not a SolidWorks macro (.swp/.swb): '{macro_path}'.")
    app = _get_app()
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    ok = app.RunMacro2(macro_path, "", sub_name, 0, errors)
    if not ok or errors.value != 0:
        raise RuntimeError(f"RunMacro2 failed (swRunMacroError={errors.value}).")
    return {"status": "ok", "macro": macro_path, "sub": sub_name}


# ── Connection / handles ──────────────────────────────────────────────────────

@mcp.tool()
def sw_reconnect() -> str:
    """
    Rebuild the COM link with SolidWorks (clears the cached app and the handles).
    Use it after SW wedges or disconnects.
    """
    return _run_com_tool(_sw_reconnect_impl)


def _sw_reconnect_impl():
    global _app
    _app = None
    _handles.clear()
    app = _get_app()
    return {"status": "ok", "revision": str(app.RevisionNumber)}


@mcp.tool()
def sw_list_handles() -> str:
    """List the COM object handles currently stored."""
    return json.dumps({"handles": list(_handles.keys())}, ensure_ascii=False)


@mcp.tool()
def sw_clear_handles() -> str:
    """Discard every stored COM object handle."""
    _handles.clear()
    return json.dumps({"status": "ok"}, ensure_ascii=False)


# ── Convenience tools (golden path) ───────────────────────────────────────────

@mcp.tool()
def sw_new_part() -> str:
    """Create a new Part using SolidWorks' default template."""
    return _run_com_tool(_sw_new_part_impl)


def _sw_new_part_impl():
    doc = _new_doc(_get_app(), "part")
    return {"status": "ok", "title": doc.GetTitle}


# Logical aliases -> index of the reference plane in the tree's default order
# (Front=0, Top=1, Right=2). The RESOLUTION itself is language-independent: it reads
# the REAL RefPlane name from the part.
_PLANE_ALIASES = {"front": 0, "top": 1, "right": 2}


def _resolve_plane_name(doc, plane: str) -> str:
    """Resolve a logical plane ("Front"/"Top"/"Right") to the REAL name of the
    reference plane in the active part, by walking the feature tree and taking the
    first 3 of type RefPlane (default order: Front, Top, Right).

    Independent of the template language: on a pt-BR template the planes are called
    "Plano frontal"/"Plano superior"/"Plano direito" and are still resolved
    correctly. Any value outside the aliases is treated as a literal plane name
    (passes straight through), which allows custom reference planes to be selected.

    Attribute access (no parentheses) is deliberate: the binding is dynamic dispatch
    and these zero-arg methods resolve as property-gets."""
    key = plane.strip().lower()
    if key not in _PLANE_ALIASES:
        return plane.strip()

    planes: list[str] = []
    feat = doc.FirstFeature
    while feat is not None and len(planes) < 3:
        if feat.GetTypeName2 == "RefPlane":
            planes.append(feat.Name)
        feat = feat.GetNextFeature

    idx = _PLANE_ALIASES[key]
    if idx >= len(planes):
        raise RuntimeError(
            f"Default plane '{plane}' not found: the active part has only "
            f"{len(planes)} reference plane(s) ({planes})."
        )
    return planes[idx]


@mcp.tool()
def sw_sketch_rectangle(
    plane: str, x1: float, y1: float, x2: float, y2: float
) -> str:
    """
    Create a sketch on the given plane and draw a rectangle (corners in mm).
    plane: "Front"/"Top"/"Right" (or the plane's full name).
    Returns the name of the sketch created (for later use). Closes the sketch at the end.
    """
    return _run_com_tool(_sw_sketch_rectangle_impl, plane, x1, y1, x2, y2)


def _sw_sketch_rectangle_impl(plane, x1, y1, x2, y2):
    doc = _active_doc()
    ext = doc.Extension
    real_plane = _resolve_plane_name(doc, plane)
    sel = ext.SelectByID2(
        real_plane, "PLANE", 0.0, 0.0, 0.0, False, 0, _null_dispatch(), 0
    )
    if not sel:
        raise RuntimeError(
            f"Could not select plane '{plane}' (resolved to '{real_plane}')."
        )
    sm = doc.SketchManager
    sm.InsertSketch(True)
    sm.CreateCornerRectangle(_mm(x1), _mm(y1), 0.0, _mm(x2), _mm(y2), 0.0)
    doc.ClearSelection2(True)
    sm.InsertSketch(True)  # closes the sketch
    sketch_feat = doc.FeatureByPositionReverse(0)  # most recent feature = the sketch
    return {"status": "ok", "sketch": sketch_feat.Name}


@mcp.tool()
def sw_extrude(depth_mm: float, reverse: bool = False) -> str:
    """
    Extrude (boss, blind) the most recent sketch. depth_mm in mm.
    reverse=True flips the extrusion direction.
    """
    return _run_com_tool(_sw_extrude_impl, depth_mm, reverse)


def _sw_extrude_impl(depth_mm, reverse):
    doc = _active_doc()
    sketch_feat = doc.FeatureByPositionReverse(0)
    doc.ClearSelection2(True)
    ext = doc.Extension
    if not ext.SelectByID2(
        sketch_feat.Name, "SKETCH", 0.0, 0.0, 0.0, False, 0, _null_dispatch(), 0
    ):
        raise RuntimeError(f"Could not select sketch '{sketch_feat.Name}'.")
    fm = doc.FeatureManager
    feat = fm.FeatureExtrusion2(
        True, False, bool(reverse), 0, 0, _mm(depth_mm), 0.01,
        False, False, False, False,
        0.01745329, 0.01745329,
        False, False, False, False,
        True, True, True, 0, 0, False,
    )
    doc.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("FeatureExtrusion2 failed (returned None).")
    return {"status": "ok", "feature": feat.Name}


if __name__ == "__main__":
    knowledge.warmup()
    mcp.run()
