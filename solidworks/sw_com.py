# -*- coding: utf-8 -*-
"""
sw_com -- layer 0: SolidWorks COM plumbing (early binding) + encapsulated gotchas.

Not a tool; it is imported by the verbs (sw_parts/sw_assembly/...) and by the engine.
It encapsulates, in one place, everything that cost time to discover (see the RAG
partition `sw_recipe`):
  - SW objects do not expose per-object typeinfo -> dynamic dispatch gets the
    property/method wrong. Fix: makepy over the typelib + instantiate the generated
    interface class with the raw _oleobj_ (CastTo does NOT work on these objects).
  - each doc is IModelDoc2 AND IAssemblyDoc/IPartDoc at the same time -> cast per
    interface.
  - returned objects come back dynamic -> re-cast before calling typed methods.
"""
import contextlib
import subprocess
import time
import winreg

import pythoncom
import win32com.client as win32

# SldWorks Type Library. The VERSION changes with the SolidWorks version (2017 = 25.0),
# so it is DISCOVERED in the registry, not hardcoded here -- see _typelib_version().
SW_TLB = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"
SW_TLB_MAJOR, SW_TLB_MINOR = 25, 0        # last resort, if the registry does not answer
# SOLIDWORKS Constant type library (the enums). Same versioning as the one above.
SW_CONST_TLB = "{4687F359-55D0-4CD3-B6CF-2EB42C11F989}"


def _typelib_version(guid: str = SW_TLB) -> tuple:
    """Registered SolidWorks typelib version, the HIGHEST one if there are several.

    TRAP: the version subkeys under `HKCR\\TypeLib\\{guid}` are HEXADECIMAL --
    SW 2017 shows up as "19", which is 0x19 = 25. Passing 19 to LoadRegTypeLib asks
    for a typelib that does not exist and returns 'Library not registered'. This
    stayed hidden while a cached makepy module existed; drop the cache and
    everything breaks.
    """
    candidates = []
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"TypeLib\\{guid}") as key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    major, _, minor = name.partition(".")
                    candidates.append((int(major, 16), int(minor or "0", 16)))
                except ValueError:
                    continue
    except OSError:
        pass
    for version in sorted(candidates, reverse=True):
        try:
            pythoncom.LoadRegTypeLib(guid, version[0], version[1], 0)
            return version
        except pythoncom.com_error:
            continue
    return SW_TLB_MAJOR, SW_TLB_MINOR


_ENUMS: dict = {}


def enum_names(enum: str) -> dict:
    """{value: name} of a SolidWorks enum (e.g. 'swFeatureError_e'), read from the
    INSTALLED constants typelib -- the enums grow between versions, so the names come
    from the version that answers, not from a table copied out of the docs. Empty dict
    if the typelib or the enum is not there (callers then report the raw code)."""
    if enum not in _ENUMS:
        names = {}
        try:
            major, minor = _typelib_version(SW_CONST_TLB)
            tl = pythoncom.LoadRegTypeLib(SW_CONST_TLB, major, minor, 0)
            for i in range(tl.GetTypeInfoCount()):
                if tl.GetDocumentation(i)[0] == enum:
                    ti = tl.GetTypeInfo(i)
                    for v in range(ti.GetTypeAttr().cVars):
                        vd = ti.GetVarDesc(v)
                        names[vd.value] = ti.GetNames(vd.memid)[0]
                    break
        except pythoncom.com_error:
            pass
        _ENUMS[enum] = names
    return _ENUMS[enum]

# swUserPreferenceStringValue_e / swDocTemplateTypes
TPL_PART, TPL_ASSEMBLY, TPL_DRAWING = 8, 9, 10
# swDocumentTypes_e
DOC_PART, DOC_ASSEMBLY, DOC_DRAWING = 1, 2, 3
# swUserPreferenceToggle_e: the 'Modify' box on dimension creation (a modal that
# stalls automation)
SW_TOGGLE_INPUT_DIM_ON_CREATE = 10
# swSaveReminderEnable: the periodic save REMINDER ("you haven't saved in a while").
# It ships enabled with a 20 min interval -- in a long automation session it pops up
# in the middle of the script and waits for the user, without any call of ours asking
# for it.
SW_TOGGLE_SAVE_REMINDER = 322
# swSketchPromptToCloseSketch: asks whether we want to close an OPEN profile. Our
# profile sketches (edge flange, lofted bend) are open ON PURPOSE.
SW_TOGGLE_PROMPT_CLOSE_SKETCH = 96

_MOD = None   # makepy module of the typelib (early binding)
_APP = None   # ISldWorks


# ── connection / module ───────────────────────────────────────────────────────
def connect(launch: bool = True, visible: bool = True, timeout: float = 120.0):
    """Connect to SolidWorks (early binding). Tries to ATTACH to a running instance;
    if there is none and launch=True, LAUNCHES SW via Dispatch (COM starts the
    registered server) and waits for it to become responsive. visible=True makes the
    window visible."""
    global _MOD, _APP
    pythoncom.CoInitialize()
    major, minor = _typelib_version()
    _MOD = win32.gencache.EnsureModule(SW_TLB, 0, major, minor)
    try:
        # ATTACH to the running one -- and cast, just like the launch path right below.
        # GetActiveObject returns a DYNAMIC dispatch, and the difference is not
        # cosmetic: a method with a byref out-param (OpenDoc6(..., Errors, Warnings))
        # requires all 6 arguments on the dynamic path and accepts 4 under early
        # binding. Without this cast, `part.open_part` failed with "Type mismatch"
        # ONLY when SolidWorks was already open -- i.e. depending on who launched SW,
        # the same verb worked or did not (measured 2026-08-18, while photographing
        # the goldens).
        _APP = cast(win32.GetActiveObject("SldWorks.Application"), "ISldWorks")
    except pythoncom.com_error as exc:
        if not launch:
            raise RuntimeError(
                "SolidWorks is not running and launch=False."
            ) from exc
        # Dispatch LAUNCHES SW and RETURNS a live handle -- USE that return value (a SW
        # launched by COM does not register in the ROT, so GetActiveObject would fail).
        raw = win32.Dispatch("SldWorks.Application")
        _APP = cast(raw, "ISldWorks")  # early binding via the generated module
        # wait for the handle to answer (SW may still be initializing add-ins)
        deadline = time.time() + timeout
        while True:
            try:
                _APP.RevisionNumber()
                break
            except pythoncom.com_error:
                if time.time() > deadline:
                    raise RuntimeError("SolidWorks did not answer after launch (timeout).")
                time.sleep(1.0)
    if visible:
        _APP.Visible = True
    # DETERMINISTIC HANDS: never open modal dialogs that stall automation.
    # swInputDimValOnCreate(10): on creating a dimension, SW opens the 'Modify' box to
    # type the value -> stalls waiting for human input. Turn it off (the value comes
    # from AddDimension/the API).
    # swSaveReminderEnable(322): the save reminder shows up by itself in the middle of
    # automation (default: every 20 min) and waits for an answer -- MEASURED as enabled
    # on this machine. swSketchPromptToCloseSketch(96): asks whether we close an open
    # profile, which we use on purpose.
    for pref in (SW_TOGGLE_INPUT_DIM_ON_CREATE, SW_TOGGLE_SAVE_REMINDER,
                 SW_TOGGLE_PROMPT_CLOSE_SKETCH):
        try:
            _APP.SetUserPreferenceToggle(pref, False)
        except pythoncom.com_error:
            pass
    return _APP


def app():
    """ISldWorks (connects on the first call)."""
    if _APP is None:
        connect()
    return _APP


def _clear_journal():
    """Remove the SW journal (avoids the journal-recovery prompt on relaunch).

    The folder carries the VERSION in its name ("SOLIDWORKS 2017", "SOLIDWORKS 2026"),
    so the wildcard covers any version -- just like `_typelib_version()`, nothing here
    pins the year. What actually filters is the file name (`swxJRNL.*`); the other
    subfolders of %APPDATA%\\SOLIDWORKS have no journal and simply do not match.
    On a machine with TWO versions installed this cleans both, which is the right
    thing: there is no way to know which one COM will launch.
    """
    import glob
    import os
    for f in glob.glob(os.path.expandvars(r"%APPDATA%\SOLIDWORKS\*\swxJRNL.*")):
        try:
            os.remove(f)
        except OSError:
            pass


def restart(visible: bool = True):
    """Recover from a WEDGED SW (a wedge where not even CloseDoc works): kill the
    SLDWORKS.exe process, clear the journal and relaunch. DESTRUCTIVE -- UNSAVED work
    is lost. Prefer `sw.ExitApp()` while SW still answers (clean shutdown, no dialog)."""
    global _APP
    subprocess.run(["taskkill", "/F", "/IM", "SLDWORKS.exe"],
                   capture_output=True, text=True)
    time.sleep(4.0)
    _clear_journal()
    _APP = None
    return connect(launch=True, visible=visible)


def module():
    if _MOD is None:
        connect()
    return _MOD


# ── early-binding cast (the heart of it) ──────────────────────────────────────
def cast(obj, iface: str):
    """Instantiate the generated interface class with the object's raw dispatch.
    E.g. cast(doc, 'IModelDoc2'), cast(comp, 'IComponent2'), cast(face, 'IFace2').
    win32com's CastTo does NOT work on these objects (it calls EnsureDispatch and
    fails)."""
    return getattr(module(), iface)(obj._oleobj_)


def NULL():
    """Null dispatch (Nothing) via VARIANT -- required on the DYNAMIC path (the MCP's
    sw_call). CAREFUL: under EARLY BINDING (this layer), optional dispatch params
    (SelectByID2's Callout, Select4's Data) want PURE `None`, not this VARIANT."""
    return win32.VARIANT(pythoncom.VT_DISPATCH, None)


def mm(v: float) -> float:
    """mm -> meters (SW's internal unit)."""
    return v / 1000.0


def deg(v: float) -> float:
    """degrees -> radians."""
    return v * 0.017453292519943295


# ── active document ───────────────────────────────────────────────────────────
def active_raw():
    d = app().ActiveDoc
    if d is None:
        # The message SAYS WHAT TO DO on purpose. Measured 2026-08-15: with the old
        # text ("No active document in SolidWorks.") the local model repeated the same
        # `part.block` up to the 40-step ceiling and wedged the CAD; whereas an error
        # pointing at the way out ("feature dimensions always exist") was corrected on
        # the very next attempt.
        raise RuntimeError(
            "No active document in SolidWorks: create or open one BEFORE this "
            "operation (part: part.new_part / part.open_part; assembly: "
            "asm.new_assembly; drawing: dwg.new_drawing).")
    return d


def active(iface: str = "IModelDoc2"):
    """Active document cast to the requested interface (default IModelDoc2).
    The SAME doc can be cast to IAssemblyDoc / IPartDoc / IDrawingDoc."""
    return cast(active_raw(), iface)


SAVE_SILENT = 1   # swSaveAsOptions_Silent


def save_as(md, path: str) -> str:
    """SILENT SaveAs (opens no dialog). Returns the path; raises on failure.

    TWO gotchas encapsulated here:
      - `IModelDoc2.SaveAs` (the obvious path) can open the save box -- the "model
        changed since the last rebuild" warning -- and stall automation. The extension
        version with Options=swSaveAsOptions_Silent(1) saves without asking.
      - `IModelDocExtension.SaveAs` has Errors/Warnings BYREF, so early binding returns
        the TUPLE (ok, errors, warnings) -- and a tuple is always "truthy". Testing the
        raw return would report success even on a save that failed.
    """
    res = cast(md.Extension, "IModelDocExtension").SaveAs(path, 0, SAVE_SILENT, None, 0, 0)
    ok, errors = (res[0], res[1]) if isinstance(res, tuple) else (res, 0)
    if not ok:
        raise RuntimeError(f"SaveAs failed ({path}): swFileSaveError={errors}")
    return path


def close_active_sketch(md=None):
    """Leave sketch edit mode, if there is an ACTIVE sketch in the doc. Returns True
    if it closed one. (The verbs already close their own sketches; this is a safety
    net.)"""
    md = md or active("IModelDoc2")
    sm = cast(md.SketchManager, "ISketchManager")
    if sm.ActiveSketch is not None:
        sm.InsertSketch(True)
        return True
    return False


def close_untitled():
    """Close UNSAVED docs (Part*/Assem*/Draw* with no path) -- hygiene so we do not
    pile up dozens of windows (which WEDGES SW). Leaves any open sketch first.
    Does NOT touch saved files. Returns how many docs it closed."""
    sw = app()
    closed, seen = 0, set()
    for _ in range(300):
        d = sw.ActiveDoc
        if d is None:
            break
        md = cast(d, "IModelDoc2")
        try:
            close_active_sketch(md)   # do not let an open sketch block the close
        except pythoncom.com_error:
            pass
        title = md.GetTitle()
        if md.GetPathName():   # already saved -> leave it; stop so we do not loop
            if title in seen:
                break
            seen.add(title)
            continue
        sw.CloseDoc(title)
        closed += 1
    return closed


# ── feature tree ──────────────────────────────────────────────────────────────
_PLANE_ALIASES = {"front": 0, "top": 1, "right": 2}


def resolve_plane(md, plane: str) -> str:
    """Resolve 'Front'/'Top'/'Right' to the REAL RefPlane name (independent of the
    template language). Any other value passes straight through (literal name of a
    custom plane)."""
    key = plane.strip().lower()
    if key not in _PLANE_ALIASES:
        return plane.strip()
    names, raw = [], md.FirstFeature()
    while raw is not None and len(names) < 3:
        feat = cast(raw, "IFeature")
        if feat.GetTypeName2() == "RefPlane":
            names.append(feat.Name)
        raw = feat.GetNextFeature()
    idx = _PLANE_ALIASES[key]
    if idx >= len(names):
        raise RuntimeError(
            f"Default plane '{plane}' not found (part has {len(names)} RefPlanes)."
        )
    return names[idx]


@contextlib.contextmanager
def sketch_direct(sm):
    """Create sketch entities STRAIGHT into the database (ISketchManager.AddToDB), with
    no sketch inference, for the duration of the block.

    MEASURED (2026-09-25; the SW 2017 battery found it, reproduced on 2026): with
    inference on, what a CreateLine produces depends on the ZOOM of the window -- a 2 mm
    segment of an L profile snapped both ends onto one point and came back None when the
    view was zoomed out (x12 on 2026; at the 2017 run's default window), and the extrude
    after it failed. With AddToDB the same six lines are created at every zoom tried
    (x0, x12, x25, x40) and extrude to the same volume. The cost: SolidWorks adds no
    automatic relations (the relations a verb asks for are unaffected). The previous
    value is restored on the way out."""
    previous = sm.AddToDB
    sm.AddToDB = True
    try:
        yield sm
    finally:
        sm.AddToDB = previous


def select_plane(md, plane: str) -> str:
    """Select a plane (by alias or name) in the doc. Returns the resolved real name."""
    real = resolve_plane(md, plane)
    ext = cast(md.Extension, "IModelDocExtension")
    ok = ext.SelectByID2(real, "PLANE", 0, 0, 0, False, 0, None, 0)
    if not ok:
        raise RuntimeError(f"Could not select plane '{plane}' ('{real}').")
    return real
