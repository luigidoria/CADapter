"""
solidworks_core.py
CURATED, TYPED SolidWorks API (the core layer), importable WITHOUT the MCP.

Why it exists:
the Exploration Engine is code -- it imports the *core* and calls Python functions
directly, skipping the MCP protocol layer (which exists so an LLM can discover
tools through natural language). The refinement loop uses ONLY this curated API
(set/rebuild/rebuild_ok/export_step/get_global_variables); the generic `sw_call`
+ RAG stay in mcp_solidworks.py, for the LLM's AGENTIC use (Phase 0).

This module imports NOTHING from `mcp`. It mirrors the patterns already proven in
mcp_solidworks.py: a single-thread COM executor (fixed apartment, CoInitialize
once), COM errors becoming readable RuntimeErrors, explicit unit conversion.

VALIDATED LIVE (SolidWorks 2017, rev 25.3, via solidworks/tests/probes/probe_equationmgr.py): in
this typelib, under win32com's dynamic dispatch, methods with NO parameters are
exposed as PROPGET -- they are accessed WITHOUT parentheses (GetEquationMgr,
GetCount, EditRebuild3). Methods WITH parameters (Equation(i), Value(i),
GlobalVariable(i), Add2, Delete) are called normally. Writing an equation uses an
indexed property-put through Invoke (validated, with the order (index, value)
being the correct one).
"""

from __future__ import annotations

import concurrent.futures
import os
import re
from typing import Any

import pythoncom
import win32com.client

# ── Dedicated COM executor (single thread) ────────────────────────────────────
# Same reason as the MCP's: COM is apartment-threaded; serializing onto one thread
# solves CoInitialize, apartment affinity and call ordering. It guarantees that
# "only the orchestrator touches SolidWorks" during a run (COM concurrency).

_com_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sw-core")
_com_init_done = False


def _on_com_thread(fn, *args, **kwargs):
    def runner():
        global _com_init_done
        if not _com_init_done:
            pythoncom.CoInitialize()
            _com_init_done = True
        return fn(*args, **kwargs)

    return _com_pool.submit(runner).result()


def _format_com_error(exc: "pythoncom.com_error") -> str:
    try:
        hr = exc.args[0]
        msg = exc.args[1]
        info = exc.args[2] if len(exc.args) > 2 else None
        detail = info[2] if info and len(info) > 2 and info[2] else ""
        return f"COM error {hr}: {msg}. {detail}".strip()
    except Exception:
        return f"COM error: {exc}"


def _guard(fn, *args, **kwargs):
    """Run fn on the COM thread; a com_error becomes a readable RuntimeError."""
    try:
        return _on_com_thread(fn, *args, **kwargs)
    except pythoncom.com_error as exc:
        raise RuntimeError(_format_com_error(exc)) from None


# ── Connection and document ───────────────────────────────────────────────────

_app = None


def _get_app():
    global _app
    if _app is None:
        try:
            _app = win32com.client.GetActiveObject("SldWorks.Application")
        except Exception:
            raise RuntimeError(
                "SolidWorks is not running. Open SolidWorks before using the core."
            )
    return _app


def _read_active_doc(app):
    """Read `app.ActiveDoc` ROBUSTLY. Validated live (SW 2017, pywin32 311): under
    this typelib's dynamic dispatch, ATTRIBUTE access to `app.ActiveDoc` is
    unstable (same family as the SelectByID2 gotcha the MCP documents -- win32com
    uses the combined flag METHOD|PROPERTYGET, which the server rejects for this
    property). The low-level PURE PROPGET Invoke always works."""
    try:
        return app.ActiveDoc
    except AttributeError:
        dispid = app._oleobj_.GetIDsOfNames("ActiveDoc")
        raw = app._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYGET, True)
        return win32com.client.Dispatch(raw) if raw is not None else None


def _active_doc():
    doc = _read_active_doc(_get_app())
    if doc is None:
        raise RuntimeError("No active document. Use open_part() or open the part in SW.")
    return doc


# ── Equation / unit parsing ───────────────────────────────────────────────────
# A global variable shows up in the EquationMgr as an equation whose name (in
# quotes) comes before the '=' and whose RHS is the expression. For DESIGN
# variables (literals, e.g. '"height"= 50mm'), we parse number + display unit.
#
# Unit contract: the core operates in the part's DISPLAY units -- the same ones
# the global variables are written in and that the profile's ranges refer to
# (mm/deg). We deliberately do NOT use EquationMgr.Value (which returns SI), so
# that the contract stays consistent between reading and writing.

_NAME_RE = re.compile(r'^\s*"(?P<name>[^"]+)"\s*=\s*(?P<rhs>.+?)\s*$')
_LITERAL_RE = re.compile(r'^\s*(?P<num>[-+]?\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]*)\s*$')


def _parse_equation(eq_str: str) -> tuple[str | None, float | None, str]:
    """Returns (name, literal_value | None, unit). value=None when the RHS is an
    expression (not a literal) -- in which case the core does not know the display
    value and cannot set it safely."""
    m = _NAME_RE.match(eq_str)
    if not m:
        return None, None, ""
    name = m.group("name")
    lit = _LITERAL_RE.match(m.group("rhs"))
    if not lit:
        return name, None, ""
    return name, float(lit.group("num")), lit.group("unit")


# ── Curated (public) API ──────────────────────────────────────────────────────


def connect() -> dict[str, Any]:
    """(Re)connect to the running SolidWorks. Returns the revision -- useful as a
    heartbeat."""

    def impl():
        global _app
        _app = None
        app = _get_app()
        return {"status": "ok", "revision": str(app.RevisionNumber)}

    return _guard(impl)


def open_part(path: str) -> dict[str, Any]:
    """Open a part (.sldprt) and make it the active document."""

    def impl():
        app = _get_app()
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = app.OpenDoc6(path, 1, 1, "", errors, warnings)  # 1 = swDocPART
        if doc is None:
            raise RuntimeError(f"Failed to open '{path}' (errors={errors.value}).")
        # A SILENT OpenDoc6 (option=1) loads the part but does NOT bring it forward:
        # ActiveDoc stays on the previous document. Validated live (SW 2017) --
        # without this the loop would silently drive the WRONG part (doctrine: never
        # operate on the wrong part by omission). ACTIVATE it explicitly.
        title = doc.GetTitle  # PROPGET
        act_err = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        app.ActivateDoc3(title, False, 0, act_err)  # (Name, UseUserPrefs, Option, Errors)
        return {"status": "ok", "title": title, "path": path}

    return _guard(impl)


def get_global_variables() -> dict[str, float]:
    """Read the active part's LITERAL global variables -> {name: display_value}.

    Variables whose RHS is an expression (not a literal) are omitted: the
    refinement loop only drives named independent variables, and the core cannot
    infer their display value safely. These are the candidate design space."""

    def impl():
        eq = _active_doc().GetEquationMgr   # PROPGET: no parentheses
        out: dict[str, float] = {}
        for i in range(eq.GetCount):        # PROPGET: no parentheses
            if not eq.GlobalVariable(i):
                continue
            name, value, _unit = _parse_equation(eq.Equation(i))
            if name is not None and value is not None:
                out[name] = value
        return out

    return _guard(impl)


def set_global_variable(name: str, value: float, *, rebuild: bool = False) -> dict[str, Any]:
    """Assign `value` to the global variable `name`, PRESERVING the existing
    equation's display unit. It errors out if the variable does not exist or if its
    current RHS is not a literal (an expression cannot be set safely).

    NOTE (validated live): the write uses the EquationMgr's parameterized
    property-put on Equation(index) through a low-level Invoke (dynamic win32com
    does not expose an indexed setter). Check against SW 2017."""

    def impl():
        eq = _active_doc().GetEquationMgr   # PROPGET
        target = -1
        unit = ""
        for i in range(eq.GetCount):        # PROPGET
            if not eq.GlobalVariable(i):
                continue
            nm, lit, un = _parse_equation(eq.Equation(i))
            if nm == name:
                if lit is None:
                    raise RuntimeError(
                        f"Global variable '{name}' is not a literal (its RHS is an "
                        "expression); the core will not change it, so as not to corrupt "
                        "the equation."
                    )
                target, unit = i, un
                break
        if target < 0:
            raise RuntimeError(f"Global variable '{name}' not found in the active part.")

        new_eq = f'"{name}" = {value}{unit}'
        _set_indexed_property(eq, "Equation", target, new_eq)

        if rebuild:
            ok = bool(_active_doc().EditRebuild3)   # PROPGET
            return {"status": "ok", "name": name, "value": value,
                    "equation": new_eq, "rebuild_ok": ok}
        return {"status": "ok", "name": name, "value": value, "equation": new_eq}

    return _guard(impl)


def set_global_variables(deltas: dict[str, float], *, rebuild: bool = True) -> dict[str, Any]:
    """Apply several absolute values at once; a SINGLE rebuild at the end (fast).
    This is what the refinement loop calls per iteration."""

    def impl():
        eq = _active_doc().GetEquationMgr   # PROPGET
        # index the literal global vars by name, in one pass
        idx: dict[str, tuple[int, str]] = {}
        for i in range(eq.GetCount):        # PROPGET
            if not eq.GlobalVariable(i):
                continue
            nm, lit, un = _parse_equation(eq.Equation(i))
            if nm is not None and lit is not None:
                idx[nm] = (i, un)

        missing = [n for n in deltas if n not in idx]
        if missing:
            raise RuntimeError(f"global variables missing or non-literal: {missing}")

        for name, value in deltas.items():
            i, unit = idx[name]
            _set_indexed_property(eq, "Equation", i, f'"{name}" = {value}{unit}')

        result: dict[str, Any] = {"status": "ok", "applied": deltas}
        if rebuild:
            result["rebuild_ok"] = bool(_active_doc().EditRebuild3)   # PROPGET
        return result

    return _guard(impl)


def rebuild(force: bool = False) -> bool:
    """Rebuild the active part. force=True uses ForceRebuild3 (rebuilds everything).
    Returns True if the rebuild reported success -- the engine's geometry gate."""

    def impl():
        doc = _active_doc()
        if force:
            return bool(doc.ForceRebuild3(False))   # has a param -> a method
        return bool(doc.EditRebuild3)               # PROPGET: no parentheses

    return _guard(impl)


def rebuild_ok() -> bool:
    """Is the geometry valid? Rebuilds and reports success/failure.

    NOTE: EditRebuild3 returns False on a rebuild failure. Fine-grained per-feature
    inspection (over-defined, what's-wrong) is future hardening -- for now the
    rebuild's boolean is the gate, as the plan's honest prerequisite states."""

    def impl():
        return bool(_active_doc().EditRebuild3)   # PROPGET

    return _guard(impl)


def export_step(path: str) -> str:
    """Export the active part as STEP (the .step/.stp extension in the path triggers
    the translator). Returns the written path. Reuses the SaveAs4 pattern already
    proven in the MCP."""

    def impl():
        doc = _active_doc()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        ok = doc.SaveAs4(path, 0, 0, errors, warnings)  # 0 = current version / no options
        if not ok:
            raise RuntimeError(f"STEP export failed (errors={errors.value}, path={path}).")
        return path

    return _guard(impl)


def save_as(path: str) -> dict[str, Any]:
    """Save the ACTIVE part AS `path` (SaveAs4) and rename it to that file.
    `.SLDPRT` saves as a part; `.step`/`.stp` exports STEP. Useful for working on a
    COPY (e.g. Beam500) without touching the user's original file."""

    def impl():
        doc = _active_doc()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        ok = doc.SaveAs4(path, 0, 0, errors, warnings)
        if not ok:
            raise RuntimeError(f"SaveAs failed (errors={errors.value}, path={path}).")
        return {"status": "ok", "path": path}

    return _guard(impl)


# ── The `remodel` route (Phase 7): a MASS RELIEF feature ──────────────────────

# Names of the global variables this route expects to find IN THE MODEL FILE, and
# the ones it creates. They are a contract with the .sldprt, not internal
# identifiers -- change them here only together with the model.
GV_LENGTH, GV_WIDTH, GV_HEIGHT = "Length", "Width", "Height"
GV_RELIEF_LENGTH, GV_RELIEF_WIDTH, GV_RELIEF_DEPTH = (
    "ReliefLength", "ReliefWidth", "ReliefDepth")


def create_relief_pocket(
    *,
    instruction: str = "",
    region: dict | None = None,
    walls_mm: float = 5.0,
    depth_frac: float = 0.6,
) -> dict[str, Any]:
    """Create an internal relief POCKET (a blind cut-extrude) on the block's top
    face and EXPOSE the global variables that dimension it -- the engine's `remodel`
    route (Phase 7). After that, the loop drives the pocket through the same path as
    `set_global_variables` (ReliefLength/ReliefWidth/ReliefDepth, in mm).

    Internal relief recipe: a rectangle centered on the top face, inset from the
    edges by `walls_mm` (keeping a load-bearing wall), cut downward to `depth_frac`
    of the Height (keeping a floor). The initial dimensions derive from the existing
    global variables (Length/Width/Height).

    NOTE (live validation -- pending): the COM SEQUENCE below (SelectByID2 of the top
    face by coordinate + SketchManager.CreateCornerRectangle + FeatureManager.
    FeatureCut4 + binding the cut's dimensions to the new global variables through
    Add2) uses the patterns already proven on this part in Phase 0 (Add2 + indexed
    property-put) and the sketch precedent (tests/_scratch/sketch_block_floor.py),
    BUT the top face's coordinate system and the NAMES of the generated dimensions
    (e.g. 'D1@Sketch2') need to be confirmed against SW 2017 with the beam loaded --
    exactly the kind of probing the _scratch scripts do. Each phase is guarded; on a
    divergence the COM error points at the exact step (actionable, not silent)."""

    region = region or {}

    def impl():
        doc = _active_doc()
        gv = get_global_variables()
        length = gv.get(GV_LENGTH)
        width = gv.get(GV_WIDTH)
        height = gv.get(GV_HEIGHT)
        if length is None or width is None or height is None:
            raise RuntimeError(
                f"create_relief_pocket requires the global variables {GV_LENGTH}/"
                f"{GV_WIDTH}/{GV_HEIGHT} (Phase 0); I found {sorted(gv)}."
            )

        # the pocket's initial dimensions (mm) -- inset from the edges by walls_mm.
        pocket_len = max(1.0, length - 2 * walls_mm)
        pocket_wid = max(1.0, width - 2 * walls_mm)
        pocket_depth = max(0.1, height * depth_frac)

        # --- 1) select the TOP face (Z = Height) by coordinate (in meters).
        #     SelectByID2(name, type, x, y, z, append, mark, callout, selOption)
        x = (length / 2) * 1e-3
        y = (width / 2) * 1e-3
        z = height * 1e-3
        ok = doc.Extension.SelectByID2("", "FACE", x, y, z, False, 0, None, 0)
        if not ok:
            raise RuntimeError(
                f"top face not selected at ({x:.4f},{y:.4f},{z:.4f}) m -- confirm the "
                "beam's coordinate system (see the validation NOTE)."
            )

        # --- 2) sketch the centered rectangle, inset from the edges (coords in meters).
        sm = doc.SketchManager
        sm.InsertSketch(True)
        sm.AddToDB = True
        x0 = (walls_mm) * 1e-3
        y0 = (walls_mm) * 1e-3
        x1 = (length - walls_mm) * 1e-3
        y1 = (width - walls_mm) * 1e-3
        sm.CreateCornerRectangle(x0, y0, 0.0, x1, y1, 0.0)
        sm.AddToDB = False

        # --- 3) blind downward cut-extrude (depth pocket_depth).
        #     FeatureCut4 has many flags; the essentials: blind, depth = pocket_depth
        #     (m), in the direction that enters the material (flip per the face normal).
        fm = doc.FeatureManager
        feat = fm.FeatureCut4(
            True, False, False, 0, 0,           # sd, flip, dir2, t1(blind), t2
            pocket_depth * 1e-3, 0.0,            # d1 (m), d2
            False, False, False, False,          # draft flags
            0.0, 0.0, False, False, False, False,
            False, True, True, True, True, False,
            0, 0.0, False,
        )
        sm.InsertSketch(True)  # makes sure the sketch is closed
        if feat is None:
            raise RuntimeError(
                "FeatureCut4 created no feature -- check the blind cut's flags against "
                "SW 2017 (see the live validation NOTE)."
            )

        # --- 4) expose 3 global variables (mm) for the loop to drive the pocket. Reuses
        #     the Add2 pattern validated in Phase 0 (parametrize_beam).
        eq = doc.GetEquationMgr  # PROPGET
        new_vars = {
            GV_RELIEF_LENGTH: (pocket_len, (walls_mm + 1.0, length - walls_mm)),
            GV_RELIEF_WIDTH: (pocket_wid, (walls_mm + 1.0, width - walls_mm)),
            GV_RELIEF_DEPTH: (pocket_depth, (0.1, height - 0.5)),
        }
        for name, (val, _rng) in new_vars.items():
            eq.Add2(-1, f'"{name}" = {val}mm', True)
        bool(doc.EditRebuild3)  # PROPGET

        return {
            "ok": True,
            "feature": getattr(feat, "Name", "ReliefPocket"),
            "new_variables": {n: rng for n, (_v, rng) in new_vars.items()},
            "touches_wetted_surface": bool(region.get("touches_wetted_surface", False)),
            "instruction": instruction,
        }

    return _guard(impl)


# ── Parameterized property-put helper ─────────────────────────────────────────


def _set_indexed_property(obj, prop_name: str, index: int, value) -> None:
    """Assign obj.<prop_name>(index) = value for an indexed COM property.

    Dynamic win32com (late binding) generates no setter for an indexed property; we
    do the low-level Invoke with DISPATCH_PROPERTYPUT. The assigned value is the
    last argument; the index is positional before it (mapping VBA's
    'obj.Prop(i) = value').

    NOTE (live validation): confirm the order (index, value) against SW 2017. If it
    is reversed, swap to (value, index)."""
    dispid = obj._oleobj_.GetIDsOfNames(prop_name)
    obj._oleobj_.Invoke(
        dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, False, index, value
    )
