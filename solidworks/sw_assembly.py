# -*- coding: utf-8 -*-
"""
sw_assembly -- layer 1: ASSEMBLY verbs (asm.*). Ported from the validated
smoke_assembly (13/13). See the assembly-* recipes.

Flow: model+save the parts (sw_parts) -> new_assembly -> add_component -> mate ->
rebuild -> save/export (parasolid/step for Mechanical). The `mate` verb consolidates the
7 types behind `kind` (homogeneous family -> 1 verb).
"""
import math
import os

from . import sw_com as C

# swMateType_e (homogeneous family of 2 refs -> kind).
MATE_KINDS = {"coincident": 0, "concentric": 1, "perpendicular": 2, "parallel": 3,
              "tangent": 4, "distance": 5, "angle": 6}
# NOTE: the WIDTH mate (=11) is NOT programmable on SW 2017 -- AddMate5 rejects it
# (ErrorStatus=0) and CreateMateData/IWidthMateFeatureData do not exist in this typelib.
# To center a part use NAMED mid-planes + mate_planes (the datum->mate bridge). See recipe
# assembly-width-mate.
# swMateAlign_e
ALIGN = {"aligned": 0, "anti": 1, "closest": 2}
# swAddMateError_e  (CAREFUL: NoError == 1, not 0!)
MATE_NOERROR, MATE_OVERDEFINED = 1, 5


def _asm():
    return C.active("IAssemblyDoc")


def _md():
    return C.active("IModelDoc2")


# ── document ──────────────────────────────────────────────────────────────────
def new_assembly() -> str:
    """Create a new assembly (default template). Returns the title."""
    sw = C.app()
    sw.NewDocument(sw.GetUserPreferenceStringValue(C.TPL_ASSEMBLY), 0, 0, 0)
    return _md().GetTitle()


def _preload(path: str):
    """Make sure the part/subassembly is LOADED in SW before inserting it.

    MEASURED GOTCHA: `AddComponent5` (and AddComponent4/AddComponent too) only inserts a
    document that is already open in SolidWorks' memory -- straight from disk it returns
    `None`, silently, even with the file intact and the right assembly active. The smokes
    had never seen this because they build the parts in the same session (so they stay
    open); any real use that assembles from existing files hits it. Measured 2026-08-30
    (SW 2017 rev 25.3): without preload 0/5 insertions; with preload 5/5, including the
    VERY file that had failed.
    """
    sw = C.app()
    if sw.GetOpenDocumentByName(path) is not None:
        return
    if not os.path.exists(path):
        raise FileNotFoundError(f"add_component: file does not exist: {path}")
    back_to = C.active("IModelDoc2").GetTitle()   # OpenDoc6 ACTIVATES what it opened
    doc_type = C.DOC_ASSEMBLY if path.lower().endswith(".sldasm") else C.DOC_PART
    res = sw.OpenDoc6(path, doc_type, 0, "", 0, 0)
    doc = res[0] if isinstance(res, tuple) else res
    if doc is None:
        errors = res[1] if isinstance(res, tuple) else "?"
        raise RuntimeError(f"add_component: could not open {path} (errors={errors})")
    # Give the focus back to the assembly. Only `ActivateDoc` (v1) will do:
    # ActivateDoc2/3 have a byref out-param that early binding with this typelib rejects
    # with 'Type mismatch'.
    sw.ActivateDoc(back_to)


def add_component(path: str, xyz=(0.0, 0.0, 0.0), *, fixed: bool = False):
    """Insert a component (a part saved on disk) at position xyz (mm). Returns
    IComponent2. The 1st component in an empty assembly is auto-fixed."""
    x, y, z = xyz
    asm = _asm()
    _preload(path)          # without this AddComponent5 returns None silently (see _preload)
    raw = asm.AddComponent5(path, 0, "", False, "", C.mm(x), C.mm(y), C.mm(z))
    if raw is None:
        raise RuntimeError(f"add_component failed: {path}")
    _md().EditRebuild3()
    comp = C.cast(raw, "IComponent2")
    if fixed:
        fix(comp)
    return comp


def component_count(top_only: bool = False) -> int:
    """How many components the assembly has (`top_only`: first level only)."""
    return _asm().GetComponentCount(bool(top_only))


def components():
    """The assembly's components (IComponent2), at every level."""
    return [C.cast(c, "IComponent2") for c in (_asm().GetComponents(False) or [])]


# ── fix / float ───────────────────────────────────────────────────────────────
def fix(comp) -> bool:
    """Fix the component. Returns IsFixed."""
    md = _md(); md.ClearSelection2(True)
    comp.Select4(False, None, False)
    _asm().FixComponent()
    md.ClearSelection2(True)
    return bool(comp.IsFixed())


def float_(comp) -> bool:
    """Release (float) the component. Returns True if it became free."""
    md = _md(); md.ClearSelection2(True)
    comp.Select4(False, None, False)
    _asm().UnfixComponent()
    md.ClearSelection2(True)
    return not comp.IsFixed()


# ── component operations (remove / suppress / pattern / mirror) ───────────────
def _select_comp(comp, mark=None, append=False):
    """Select a COMPONENT, optionally with a MARK (patterns use the mark to tell
    seed=4 / direction=1 apart). IComponent2.Select4(Append, Data, SuppressSelectDialog)."""
    md = _md()
    if mark is None:
        return comp.Select4(append, None, False)
    selmgr = C.cast(md.SelectionManager, "ISelectionMgr")
    sd = selmgr.CreateSelectData(); sd.Mark = mark
    return comp.Select4(append, sd, False)


def remove_component(comp) -> int:
    """Remove a component from the assembly (and its mates). Returns the new
    (top-level) component count."""
    md = _md(); md.ClearSelection2(True)
    _select_comp(comp)
    ext = C.cast(md.Extension, "IModelDocExtension")
    ext.DeleteSelection2(0)  # swDelete_Absorbed=0 (removes the selection and dependents)
    md.EditRebuild3()
    return component_count(top_only=True)


def suppress(comp, state: bool = True) -> bool:
    """Suppress (state=True) or UNsuppress (state=False) a component -- takes it out of /
    puts it back into the assembly without deleting it (useful for variants / to lighten
    the rebuild). Returns the state that was applied."""
    md = _md(); md.ClearSelection2(True)
    _select_comp(comp)
    ok = md.EditSuppress2() if state else md.EditUnsuppress2()
    md.ClearSelection2(True)
    if not ok:
        raise RuntimeError(f"suppress({state}) failed.")
    return state


# NOTE (LINEAR/CIRCULAR COMPONENT pattern -> ESCAPE HATCH): `FeatureLinearPattern5`/
# `FeatureCircularPattern5` (the SAME methods that work for PART features) return Nothing
# when the seed is a COMPONENT, even with the CORRECT selection (direction=mark 1 type
# EDGE, component=mark 4 type COMPONENTS -- confirmed via GetSelectedObjectType3). There
# is no dedicated component-pattern method in this typelib (only DissolveComponentPattern
# and InsertDerivedPattern). For component arrays, use sw_call or model the pattern in the
# PART (part.pattern_*) before assembling. Tested thoroughly 2026-07-23.


def replace_component(comp, path: str, *, config: str = "", all_instances: bool = True,
                      reattach_mates: bool = True) -> bool:
    """Replace the selected component(s) with another part (`path`), reusing the mates
    where possible. Useful for swapping a variant (e.g. an M8 bolt for an M10). `config` =
    the target configuration. Returns True. (The replacement part needs compatible mate
    references to reattach.)"""
    md = _md(); md.ClearSelection2(True)
    _select_comp(comp)
    ok = _asm().ReplaceComponents2(path, config, bool(all_instances), False,
                                   bool(reattach_mates))
    md.EditRebuild3(); md.ClearSelection2(True)
    if not ok:
        raise RuntimeError(f"replace_component failed: {path}")
    return True


def mirror_component(mirror_plane: str, comps) -> int:
    """Mirror COMPONENTS about an assembly plane ('Front'/'Top'/'Right' or a name).
    Creates mirrored INSTANCES (a positioned opposite-hand copy). `comps` = list of
    IComponent2. Returns the new (top-level) component count.
    GOTCHA (discovered live): MirrorComponents2 only creates anything if
    `ComponentOrientations` is an array of the SAME size as `comps` (passing None -> a
    silent no-op, returns None)."""
    md = _md(); md.ClearSelection2(True)
    real = C.resolve_plane(md, mirror_plane)
    ext = C.cast(md.Extension, "IModelDocExtension")
    if not ext.SelectByID2(real, "PLANE", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError(f"mirror_component: could not select plane '{mirror_plane}'.")
    pf = C.cast(C.cast(md.SelectionManager, "ISelectionMgr").GetSelectedObject6(1, -1), "IFeature")
    md.ClearSelection2(True)
    n0 = component_count(top_only=True)
    comp_arr = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_DISPATCH,
                               [C.cast(c, "IComponent2")._oleobj_ for c in comps])
    orient = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_I4, [0] * len(comps))
    _asm().MirrorComponents2(pf._oleobj_, comp_arr, orient, False, None,
                             False, None, 0, "", "", 0, False, False)
    md.EditRebuild3()
    n1 = component_count(top_only=True)
    if n1 <= n0:
        raise RuntimeError(f"mirror_component created no instance ({n0}->{n1}).")
    return n1


# ── component inspection (finding faces/axes for mates) ───────────────────────
def comp_faces(comp):
    """All the faces of a COMPONENT's body (IFace2), for mating by face."""
    b2 = C.cast(comp.GetBody(), "IBody2")
    return [C.cast(f, "IFace2") for f in b2.GetFaces()]


def find_face(comp, kind="plane", axis=1, want_max=True, radius_mm=None):
    """Find a face of a COMPONENT by geometry (as a mate reference):
    kind='plane' -> planar face perpendicular to `axis`, picked by position;
    kind='cylinder' -> cylindrical face; if `radius_mm` is given, only matches one of
    roughly equal radius (tol 0.2mm) -- ESSENTIAL so the HOLE is not confused with a
    fillet/counterbore/slot that adds other cylinders (the 1st cylindrical face is not
    always the one you want). (PlaneParams is the plane's normal, not the outward one ->
    tell them apart by position.)"""
    cand = []
    for f2 in comp_faces(comp):
        s = C.cast(f2.GetSurface(), "ISurface")
        if kind == "cylinder":
            if s.IsCylinder():
                if radius_mm is None or abs(s.CylinderParams[6] * 1000.0 - radius_mm) < 0.2:
                    return f2
        elif s.IsPlane():
            n = s.PlaneParams
            if abs(n[axis]) > 0.9 and all(abs(n[j]) < 0.1 for j in range(3) if j != axis):
                p = f2.GetClosestPointOn(0, 0, 0)
                cand.append((p[axis], f2))
    if not cand:
        return None
    cand.sort(key=lambda t: t[0])
    return cand[-1][1] if want_max else cand[0][1]


def faces_perp(comp, axis=0):
    """PLANAR faces of the component perpendicular to `axis` (0=X,1=Y,2=Z), ordered by
    position along the axis (lowest->highest). Tells the INNER ones (middle indices) from
    the OUTER ones (the extremes) -- e.g. the inner walls of a clevis for a width mate, or
    the two faces of a flange. (PlaneParams is the plane's normal, not the outward one --
    which is why we order by POSITION, see recipe assembly-select-face.)"""
    out = []
    for f2 in comp_faces(comp):
        s = C.cast(f2.GetSurface(), "ISurface")
        if s.IsPlane():
            n = s.PlaneParams
            if abs(n[axis]) > 0.9 and all(abs(n[j]) < 0.1 for j in range(3) if j != axis):
                out.append((f2.GetClosestPointOn(0, 0, 0)[axis], f2))
    out.sort(key=lambda t: t[0])
    return [f for _, f in out]


def _select(ent, append):
    # early binding: Data=pure None
    return C.cast(ent, "IEntity").Select4(append, None)


def _last_mate_name(md) -> str:
    """Name of the JUST-created mate. GOTCHA: FeatureByPositionReverse(0) returns the
    'Mates' FOLDER (MateGroup), not the mate -> walk the sub-features and take the last."""
    f = C.cast(md.FeatureByPositionReverse(0), "IFeature")
    if not f.GetTypeName2().startswith("MateGroup"):
        return f.Name
    sub, last = f.GetFirstSubFeature(), None
    while sub is not None:
        last = C.cast(sub, "IFeature")
        sub = last.GetNextSubFeature()
    return last.Name if last is not None else f.Name


# ── mate (HOMOGENEOUS family: 7 types through kind) ───────────────────────────
def mate(ref_a, ref_b, kind="coincident", *, align="closest",
         distance_mm: float = 0.0, angle_deg: float = 0.0) -> str:
    """Create a mate between two references (component faces/axes).
    kind: coincident|concentric|perpendicular|parallel|tangent|distance|angle.
    align: closest|aligned|anti. distance in mm, angle in degrees. Returns the mate name."""
    if kind not in MATE_KINDS:
        raise ValueError(f"kind '{kind}' is invalid. Options: {sorted(MATE_KINDS)}")
    md = _md(); md.ClearSelection2(True)
    if not (_select(ref_a, False) and _select(ref_b, True)):
        md.ClearSelection2(True)
        raise RuntimeError(f"mate {kind}: reference selection failed.")
    d, a = C.mm(distance_mm), C.deg(angle_deg)
    m, err = _asm().AddMate5(MATE_KINDS[kind], ALIGN[align], False, d, d, d,
                             1, 1, a, a, a, False, False, 0)
    md.ClearSelection2(True)
    if m is None or err not in (MATE_NOERROR, MATE_OVERDEFINED):
        raise RuntimeError(f"mate {kind} failed (ErrorStatus={err}; NoError==1).")
    return _last_mate_name(md)


def limit_mate(ref_a, ref_b, kind="angle", *, min_deg=0.0, max_deg=90.0, nominal_deg=None,
               min_mm=0.0, max_mm=0.0, nominal_mm=None, align="closest", flip=False) -> str:
    """LIMIT mate: constrains a moving part's TRAVEL to a RANGE (it does not lock the DOF,
    it LIMITS it). Stops the part from rotating/sliding until it INTERPENETRATES the
    neighbouring geometry (see memory assembly-constraints-realism). Uses AddMate5's
    AngleAbs*/DistanceAbs* params.
      kind='angle'    -> angle between ref_a and ref_b in [min_deg, max_deg] (nominal =
                         the starting position).
      kind='distance' -> distance in [min_mm, max_mm].
    Returns the mate name. (Angle can return OverDefined=5 even when it creates fine.)"""
    md = _md(); md.ClearSelection2(True)
    if not (_select(ref_a, False) and _select(ref_b, True)):
        md.ClearSelection2(True)
        raise RuntimeError("limit_mate: reference selection failed.")
    if kind == "angle":
        nom = C.deg(min_deg if nominal_deg is None else nominal_deg)
        m, err = _asm().AddMate5(MATE_KINDS["angle"], ALIGN[align], bool(flip),
                                 0.0, 0.0, 0.0, 1, 1,
                                 nom, C.deg(max_deg), C.deg(min_deg), False, False, 0)
    elif kind == "distance":
        nom = C.mm(min_mm if nominal_mm is None else nominal_mm)
        m, err = _asm().AddMate5(MATE_KINDS["distance"], ALIGN[align], bool(flip),
                                 nom, C.mm(max_mm), C.mm(min_mm), 1, 1,
                                 0.0, 0.0, 0.0, False, False, 0)
    else:
        raise ValueError(f"kind '{kind}' is invalid (angle|distance).")
    md.ClearSelection2(True)
    if m is None or err not in (MATE_NOERROR, MATE_OVERDEFINED):
        raise RuntimeError(f"limit_mate {kind} failed (ErrorStatus={err}; NoError==1).")
    return _last_mate_name(md)


def _comp_ref(comp, name: str, asm_title: str) -> str:
    """QUALIFIED name of a named reference (plane/axis) of a COMPONENT, for SelectByID2 in
    the assembly: 'name@comp-instance@assembly-title'. GOTCHA: the SUFFIX with the
    assembly title is MANDATORY (e.g. 'mid_x@clevis-1@Assem2'); without it SelectByID2
    returns False. See recipe assembly-mate-planes."""
    return f"{name}@{comp.Name2}@{asm_title}"


def mate_planes(comp_a, name_a: str, comp_b, name_b: str, kind: str = "coincident", *,
                sel_type: str = "PLANE", align: str = "closest", distance_mm: float = 0.0) -> str:
    """Mate between two NAMED references (planes/axes) of two components -- the
    datum->mate bridge: robust, with no fragile coordinate. The parts must have the 
    datums created and renamed (part.reference_plane(..., name=...)).
    E.g. align mid-planes to CENTER a part (replaces the width mate, unavailable on
    SW 2017).
    kind: coincident|parallel|distance|perpendicular|angle. sel_type: 'PLANE'|'AXIS'.
    Returns the mate name."""
    if kind not in MATE_KINDS:
        raise ValueError(f"kind '{kind}' is invalid. Options: {sorted(MATE_KINDS)}")
    md = _md(); md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    title = md.GetTitle()
    ra, rb = _comp_ref(comp_a, name_a, title), _comp_ref(comp_b, name_b, title)
    if not (ext.SelectByID2(ra, sel_type, 0, 0, 0, False, 0, None, 0) and
            ext.SelectByID2(rb, sel_type, 0, 0, 0, True, 0, None, 0)):
        md.ClearSelection2(True)
        raise RuntimeError(f"mate_planes: selection failed ({ra} / {rb}; "
                           f"sel_type={sel_type}).")
    d = C.mm(distance_mm)
    m, err = _asm().AddMate5(MATE_KINDS[kind], ALIGN[align], False, d, d, d,
                             1, 1, 0.0, 0.0, 0.0, False, False, 0)
    md.ClearSelection2(True)
    if m is None or err not in (MATE_NOERROR, MATE_OVERDEFINED):
        raise RuntimeError(f"mate_planes {kind} failed (ErrorStatus={err}; NoError==1).")
    return _last_mate_name(md)


def mate_to_assembly(comp, comp_ref: str, asm_ref: str, kind: str = "coincident", *,
                     comp_type: str = "PLANE", asm_type: str = "PLANE", align: str = "closest") -> str:
    """Ground/relate a NAMED reference of a COMPONENT to a reference of the ASSEMBLY
    itself (e.g. 'Front Plane', 'Top Plane', 'Right Plane'). Gives the assembly a DATUM/
    orientation WITH MEANING -- an origin and symmetry that are useful for loading/
    supporting in FEA and for the drawing, instead of the 1st component 'fixed in a
    corner'. comp_type/asm_type: 'PLANE'|'AXIS'. Returns the mate name."""
    if kind not in MATE_KINDS:
        raise ValueError(f"kind '{kind}' is invalid. Options: {sorted(MATE_KINDS)}")
    md = _md(); md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    ra = _comp_ref(comp, comp_ref, md.GetTitle())
    if not (ext.SelectByID2(ra, comp_type, 0, 0, 0, False, 0, None, 0) and
            ext.SelectByID2(asm_ref, asm_type, 0, 0, 0, True, 0, None, 0)):
        md.ClearSelection2(True)
        raise RuntimeError(f"mate_to_assembly: selection failed ({ra} / {asm_ref}).")
    m, err = _asm().AddMate5(MATE_KINDS[kind], ALIGN[align], False, 0.0, 0.0, 0.0,
                             1, 1, 0.0, 0.0, 0.0, False, False, 0)
    md.ClearSelection2(True)
    if m is None or err not in (MATE_NOERROR, MATE_OVERDEFINED):
        raise RuntimeError(f"mate_to_assembly {kind} failed (ErrorStatus={err}; NoError==1).")
    return _last_mate_name(md)


def delete_mate(mate_name: str):
    """Delete a mate by name (isolation/editing) and rebuild."""
    md = _md()
    ext = C.cast(md.Extension, "IModelDocExtension")
    if ext.SelectByID2(mate_name, "MATE", 0, 0, 0, False, 0, None, 0):
        md.EditDelete()
        md.ClearSelection2(True)
        md.EditRebuild3()
        return True
    return False


def rebuild() -> bool:
    """Rebuild the active assembly. True if SW accepted it."""
    return bool(_md().EditRebuild3())


def _math():
    return C.cast(C.app().GetMathUtility(), "IMathUtility")


def _make_transform(arr16):
    v = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8, list(arr16))
    return C.cast(_math().CreateTransform(v), "IMathTransform")


def free_translations(comp, *, delta_mm: float = 3.0):
    """EMPIRICALLY test which translations (assembly X/Y/Z) of the component are FREE:
    nudge it via SetTransformAndSolve and see whether it STAYS displaced (free DOF) or the
    mates bring it back (constrained). Returns the free ['X','Y','Z'] -- a gate to catch a
    part that is 'floating' (the classic failure mode: a pin that slides along its axis).
    Restores the pose at the end.
    (Note: SW does not expose DOF through pywin32 -- GetRemainingDOFs does not marshal its
    byrefs; this empirical test is the robust path. It only covers TRANSLATION; free joint
    rotation is expected.)"""
    if comp.IsFixed():
        return []
    base = list(C.cast(comp.Transform2, "IMathTransform").ArrayData)
    tol = C.mm(delta_mm) * 0.5
    free = []
    for ax, nm in ((0, "X"), (1, "Y"), (2, "Z")):
        arr = list(base); arr[9 + ax] += C.mm(delta_mm)
        comp.SetTransformAndSolve(_make_transform(arr))
        _md().EditRebuild3()   # force the mate solver before measuring
        moved = C.cast(comp.Transform2, "IMathTransform").ArrayData[9 + ax] - base[9 + ax]
        if abs(moved) > tol:
            free.append(nm)
        comp.SetTransformAndSolve(_make_transform(base)); _md().EditRebuild3()   # restore
    return free


def move_component(comp, xyz=(0.0, 0.0, 0.0)):
    """Put the component's BOX CENTER at xyz (mm) -- the assembly's WAY BACK.

    Deleting a mate does NOT return the part to where it was, and no other verb here
    moves a component: without this, fixing a badly positioned part meant removing and
    re-inserting it. `xyz` is the same reference as `add_component` and `component_box`.

    Until 2026-08-22 this function put the part's ORIGIN at xyz while the docstring
    DECLARED it to be "the same reference as add_component". Measured on six parts:
    after `add_component(xyz)` the CENTER matches xyz;
    after `move_component(xyz)` the part went to `xyz + (center - origin)` -- up to 50 mm
    of silent drift. The two references really are the same now.

    Returns the position MEASURED after the solve, which may NOT be the one requested: the
    mates that already exist hold the axes they lock. Declaring "it moved" without looking
    would be a lie.
    """
    if comp.IsFixed():
        raise RuntimeError("a FIXED component does not move -- call float_() first")
    bx = comp.GetBox(False, False)
    center = [(bx[i] + bx[i + 3]) * 500.0 for i in range(3)]
    arr = list(C.cast(comp.Transform2, "IMathTransform").ArrayData)
    off = [center[i] - arr[9 + i] * 1000.0 for i in range(3)]   # origin -> center
    for i in range(3):
        arr[9 + i] = C.mm(float(xyz[i]) - off[i])
    comp.SetTransformAndSolve(_make_transform(arr))
    _md().EditRebuild3()
    bx = comp.GetBox(False, False)
    return [round((bx[i] + bx[i + 3]) * 500.0, 3) for i in range(3)]


def mate_errors():
    """Mates WITH ERRORS (over-defined / unsatisfied): [(name, code, is_warning)]. Empty =
    a healthy assembly. A gate against OVER-DEFINED -- which the translation test
    (free_translations) does NOT catch (over-defined is excess constraint, not a shortage).
    Uses IFeature.GetErrorCode2."""
    md = _md(); out = []
    raw = md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2().startswith("MateGroup"):
            sub = f.GetFirstSubFeature()
            while sub is not None:
                s = C.cast(sub, "IFeature")
                res = s.GetErrorCode2()
                code = res[0] if isinstance(res, (tuple, list)) else res
                warn = bool(res[1]) if isinstance(res, (tuple, list)) and len(res) > 1 else False
                if int(code) != 0:
                    out.append((s.Name, int(code), warn))
                sub = s.GetNextSubFeature()
        raw = f.GetNextFeature()
    return out


def _component(comp):
    """An IComponent2 from itself or from its name ('pin-1')."""
    if not isinstance(comp, str):
        return C.cast(comp, "IComponent2")
    for c in components():
        if c.Name2 == comp or c.Name2.split("/")[-1] == comp:
            return c
    raise ValueError(f"component '{comp}' not found. Components: "
                     f"{[c.Name2 for c in components()]}")


def _mate_label(value, enum, prefix):
    name = C.enum_names(enum).get(value)
    return (name[len(prefix):] if name and name.startswith(prefix) else str(value)).lower()


def mates(comp=None) -> list:
    """READ the assembly's mates (also ones made outside this MCP) -- what holds each
    component. `comp` (a component or its name, e.g. 'pin-1') keeps only the mates that
    touch it. Each mate: {name, type (coincident|concentric|distance|...), align
    (aligned|anti|closest), components, references [{component, entity, point_mm,
    direction, radius_mm}], value (mm or deg, for distance/angle mates), status ('ok'
    or the SolidWorks error name), suppressed}. `references` carry the geometry the mate
    uses, in ASSEMBLY coordinates: e.g. a concentric mate gives each cylinder's axis
    point and direction. To answer "why can this part still move/rotate", read its mates
    here and measure what is left free with free_dof(comp)."""
    want = _component(comp).Name2 if comp is not None else None
    feat_err = C.enum_names("swFeatureError_e")
    out, raw = [], _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2().startswith("MateGroup"):
            sub = f.GetFirstSubFeature()
            while sub is not None:
                s = C.cast(sub, "IFeature")
                sub = s.GetNextSubFeature()
                try:
                    m = C.cast(s.GetSpecificFeature2(), "IMate2")
                    count = m.GetMateEntityCount()
                except Exception:  # noqa: BLE001 -- a folder/other feature under Mates
                    continue
                refs = []
                for i in range(count):
                    me = C.cast(m.MateEntity(i), "IMateEntity2")
                    rc = me.ReferenceComponent
                    p = list(me.EntityParams or [0.0] * 8)
                    ref = {"component": C.cast(rc, "IComponent2").Name2 if rc is not None else None,
                           "entity": _mate_label(me.ReferenceType2, "swSelectType_e", "swSel"),
                           "point_mm": [round(v * 1000.0, 4) for v in p[0:3]],
                           "direction": [round(v, 6) + 0.0 for v in p[3:6]]}
                    if len(p) > 6 and p[6] > 0:
                        ref["radius_mm"] = round(p[6] * 1000.0, 4)
                    refs.append(ref)
                names = [r["component"] for r in refs]
                if want is not None and want not in names:
                    continue
                res = s.GetErrorCode2()
                code = int(res[0] if isinstance(res, (tuple, list)) else res)
                supp = s.IsSuppressed2(1, None)            # swThisConfiguration
                item = {"name": s.Name,
                        "type": _mate_label(m.Type, "swMateType_e", "swMate"),
                        "align": {0: "aligned", 1: "anti", 2: "closest"}.get(m.Alignment,
                                                                            str(m.Alignment)),
                        "components": names, "references": refs,
                        "status": "ok" if code == 0 else feat_err.get(code, f"code {code}"),
                        "suppressed": bool(supp[0] if isinstance(supp, (tuple, list)) else supp)}
                dd = s.GetFirstDisplayDimension()
                if dd is not None:
                    dim = C.cast(C.cast(dd, "IDisplayDimension").GetDimension2(0), "IDimension")
                    v = dim.GetSystemValue3(1, None)[0]
                    angular = dim.GetType() == 1
                    item["value"] = round(v * (57.29577951308232 if angular else 1000.0), 6)
                    item["unit"] = "deg" if angular else "mm"
                out.append(item)
        raw = f.GetNextFeature()
    return out


def free_dof(comp, *, delta_mm: float = 3.0, delta_deg: float = 10.0) -> dict:
    """Which degrees of freedom the mates LEAVE FREE on a component, measured: nudge it
    along and about each assembly axis (X/Y/Z) with SetTransformAndSolve, rebuild, and see
    whether the solver keeps the move (free) or pulls it back (constrained). Restores the
    pose at the end. `comp` = the component or its name ('pin-1').
    Returns {component, fixed, free_translations:['X',...], free_rotations:['Y',...],
    dof}. A pin with a concentric mate and a face mate -> free_rotations ['Y'] (it spins
    about its own axis) and no free translation. Rotations are tested about the
    component's box centre and the ASSEMBLY axes: a joint on an inclined axis may show up
    as partly free on more than one axis. Why it is empirical: SolidWorks'
    GetRemainingDOFs does not marshal through pywin32 (measured on 2017 and 2026)."""
    c = _component(comp)
    if c.IsFixed():
        return {"component": c.Name2, "fixed": True, "free_translations": [],
                "free_rotations": [], "dof": 0}
    trans = free_translations(c, delta_mm=delta_mm)
    base = list(C.cast(c.Transform2, "IMathTransform").ArrayData)
    rots = []
    for ax, nm in ((0, "X"), (1, "Y"), (2, "Z")):
        bx = c.GetBox(False, False)
        ctr = [(bx[i] + bx[i + 3]) * 500.0 for i in range(3)]
        axis = [1.0 if i == ax else 0.0 for i in range(3)]
        cur = C.cast(c.Transform2, "IMathTransform")
        c.SetTransformAndSolve(C.cast(_rot_transform(ctr, axis, delta_deg).Multiply(cur),
                                      "IMathTransform"))
        _md().EditRebuild3()
        arr = list(C.cast(c.Transform2, "IMathTransform").ArrayData)
        # how much of the rotation matrix changed: sin(10 deg) ~ 0.17 when it is kept
        if max(abs(arr[i] - base[i]) for i in range(9)) > 0.5 * abs(math.sin(C.deg(delta_deg))):
            rots.append(nm)
        c.SetTransformAndSolve(_make_transform(base)); _md().EditRebuild3()   # restore
    return {"component": c.Name2, "fixed": False, "free_translations": trans,
            "free_rotations": rots, "dof": len(trans) + len(rots)}


def _rot_transform(point_mm, axis_dir, angle_deg):
    m = _math()
    p = C.cast(m.CreatePoint(C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8,
              [C.mm(point_mm[0]), C.mm(point_mm[1]), C.mm(point_mm[2])])), "IMathPoint")
    v = C.cast(m.CreateVector(C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8,
              [float(x) for x in axis_dir])), "IMathVector")
    return C.cast(m.CreateTransformRotateAxis(p, v, C.deg(angle_deg)), "IMathTransform")


def cylinder_axis_world(comp, radius_mm):
    """Axis (point_mm, direction) of a COMPONENT's cylindrical face in ASSEMBLY
    coordinates. CylinderParams comes in PART coordinates -> transform it by Transform2
    (via IMathPoint/Vector.MultiplyTransform). Useful for rotating about the REAL axis
    (e.g. a pin, for the sweep)."""
    f = find_face(comp, "cylinder", radius_mm=radius_mm)
    cp = C.cast(f.GetSurface(), "ISurface").CylinderParams
    m = _math(); xf = C.cast(comp.Transform2, "IMathTransform")
    p = C.cast(m.CreatePoint(C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8,
              [cp[0], cp[1], cp[2]])), "IMathPoint")
    v = C.cast(m.CreateVector(C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8,
              [cp[3], cp[4], cp[5]])), "IMathVector")
    pw = C.cast(p.MultiplyTransform(xf), "IMathPoint").ArrayData
    vw = C.cast(v.MultiplyTransform(xf), "IMathVector").ArrayData
    return (pw[0] * 1000, pw[1] * 1000, pw[2] * 1000), (vw[0], vw[1], vw[2])


def sweep_collision_angle(comp, axis_point_mm, axis_dir, *, step=15, amax=170):
    """Rotate `comp` about the axis (axis_point_mm, axis_dir) via SetTransformAndSolve --
    WITHOUT creating mates -- and find the 1st angle with material interference. Restores
    the pose at the end. Returns (largest_ok_angle, collision_angle|None). Robust: it does
    not pile up over-defined angle mates (unlike creating/deleting rigid mates on an
    already constrained part)."""
    base = C.cast(comp.Transform2, "IMathTransform")
    base_arr = list(base.ArrayData)
    last_ok, hit = 0, None
    for a in range(step, amax + 1, step):
        newxf = C.cast(_rot_transform(axis_point_mm, axis_dir, a).Multiply(base), "IMathTransform")
        comp.SetTransformAndSolve(newxf); _md().EditRebuild3()
        if interferences():
            hit = a; break
        last_ok = a
    comp.SetTransformAndSolve(_make_transform(base_arr)); _md().EditRebuild3()   # restore
    return last_ok, hit


def interferences(*, coincidence_counts=False):
    """Run the assembly's INTERFERENCE DETECTION (a realism gate: an honest assembly has
    no overlapping material). Returns [{volume_mm3, comps:[names]}]. By default it IGNORES
    coincidences (volume ~0: faces that merely touch, e.g. a head seat) -> it only catches
    REAL material overlap; coincidence_counts=True includes the coincidences."""
    idm = C.cast(_asm().InterferenceDetectionManager, "IInterferenceDetectionMgr")
    idm.TreatCoincidenceAsInterference = bool(coincidence_counts)
    idm.UseTransform = False
    out = []
    try:
        for itf in (idm.GetInterferences() or []):
            it = C.cast(itf, "IInterference")
            comps = [C.cast(c, "IComponent2").Name2 for c in (it.Components or [])]
            out.append({"volume_mm3": it.Volume * 1e9, "comps": comps})
    finally:
        # `Done()` is NOT optional hygiene: without it the detection session stays open
        # and THE NEXT SELECTION FAILS -- `IEntity.Select4` returns False and the symptom
        # shows up as "it did not select the faces" on a mate that was correct, far from
        # the cause. Measured with a control on the cloud front in 2026-08-19 (A without
        # calling it / B with / C another reader); see the recipe
        # `assembly-interference-done-and-open-part.md`. The defect here was the SAME:
        # this is the local port, and `smoke_clevis` calls interference at the very end,
        # where nobody selects anything afterwards -- which is why it never hurt.
        try:
            idm.Done()
        except Exception:  # noqa: BLE001
            pass
        C.active("IModelDoc2").ClearSelection2(True)
    return out


# ── exploded view (essential for an assembly drawing) ─────────────────────────
_EXPLODE_AXES = {"x": ("Right", 0), "y": ("Top", 1), "z": ("Front", 2)}


def _top_components():
    return [c for c in components() if "/" not in c.Name2 and not c.IsSuppressed()]


def _box_mm(comp):
    """Axis-aligned box of a component in assembly mm, [x0, y0, z0, x1, y1, z1]. It
    follows the EXPLODED position while the explode view is shown (measured)."""
    return [v * 1000.0 for v in comp.GetBox(False, False)]


def _boxes_overlap(a, b, tol=1e-3):
    return all(a[i] < b[i + 3] - tol and b[i] < a[i + 3] - tol for i in range(3))


def _exit_move(box, obstacles, clearance):
    """The cheapest straight way out for `box` among the six axis directions.
    Returns (axis, sign, travel_mm, path_clear). A direction is CLEAR when no obstacle box
    lies in the region the box sweeps ahead of itself -- a conservative test: it can reject
    a real exit (a box is bigger than the part) but never approves a colliding one. The
    travel ends `clearance` past every obstacle the box overlaps in the other two axes.
    With no clear direction it still returns the shortest move that ends clear."""
    best = None
    for axis, (_plane, i) in _EXPLODE_AXES.items():
        others = [j for j in range(3) if j != i]
        for sign in (1, -1):
            clear, travel = True, 0.0
            for ob in obstacles:
                if not all(box[j] < ob[j + 3] - 1e-3 and ob[j] < box[j + 3] - 1e-3
                           for j in others):
                    continue          # never in the way along this axis
                if sign > 0:
                    ahead = ob[i + 3] > box[i + 3] + 1e-3
                    need = ob[i + 3] - box[i]
                else:
                    ahead = ob[i] < box[i] - 1e-3
                    need = box[i + 3] - ob[i]
                clear = clear and not ahead
                travel = max(travel, need)
            if travel <= 0:
                continue              # nothing to leave along this direction
            cand = (not clear, travel + clearance, axis, sign)
            if best is None or cand < best:
                best = cand
    if best is None:
        return None
    blocked, travel, axis, sign = best
    return axis, sign, round(travel, 3), not blocked


def _add_explode_step(cfg, comp, axis, distance_mm):
    """One explode step: `comp` moves `distance_mm` along the assembly axis (negative =
    the other way). Selection: the component with mark 1, the direction as the assembly
    plane NORMAL to that axis with mark 2. The sense of the plane normal is checked on the
    component's box afterwards and the step is redone reversed if it went the wrong way.
    Returns the step name."""
    md = _md()
    ext = C.cast(md.Extension, "IModelDocExtension")
    plane_key, i = _EXPLODE_AXES[axis]
    plane = C.resolve_plane(md, plane_key)
    before = _box_mm(comp)[i]
    for reverse in (distance_mm < 0, distance_mm >= 0):
        md.ClearSelection2(True)
        ok = (ext.SelectByID2(comp.GetSelectByIDString(), "COMPONENT", 0, 0, 0, False, 1,
                              None, 0)
              and ext.SelectByID2(plane, "PLANE", 0, 0, 0, True, 2, None, 0))
        if not ok:
            raise RuntimeError(f"explode: could not select {comp.Name2} / plane {plane}.")
        step = cfg.AddExplodeStep(C.mm(abs(distance_mm)), reverse, False, False)
        md.ClearSelection2(True)
        md.EditRebuild3()
        if step is None:
            raise RuntimeError(f"explode: AddExplodeStep returned None for {comp.Name2}.")
        name = C.cast(step, "IExplodeStep").Name
        moved = _box_mm(comp)[i] - before
        if abs(moved - distance_mm) < 0.01 * abs(distance_mm) + 1e-3:
            return name
        cfg.DeleteExplodeStep(name)
        md.EditRebuild3()
    raise RuntimeError(f"explode: the step on {comp.Name2} did not move it "
                       f"{distance_mm} mm along {axis}.")


def exploded_overlaps() -> list:
    """Pairs of components whose boxes still overlap in the CURRENT state (exploded or
    not): [[name_a, name_b], ...]. The gate for an exploded drawing view -- in the
    assembled state it lists every nested pair, which is expected."""
    comps = _top_components()
    boxes = {c.Name2: _box_mm(c) for c in comps}
    names = list(boxes)
    return [[a, b] for k, a in enumerate(names) for b in names[k + 1:]
            if _boxes_overlap(boxes[a], boxes[b])]


def explode(steps=None, *, clearance_mm: float = 10.0, method: str = "plan") -> dict:
    """Create an EXPLODED VIEW of the active configuration. The drawing shows it through
    `dwg.set_exploded`; collapse() folds it back.
      method='plan' (DEFAULT) -> a controlled explosion. Fixed components stay; the others
        leave one at a time, last-inserted first, each along the axis direction whose path
        is clear of the other parts (see _exit_move), until its box is `clearance_mm` past
        every part it was nested in. Nested parts come out clean (the clevis tongue leaves
        the fork through the slot instead of staying inside it).
      steps -> explicit steps instead of the plan: [{"component": "pin-1", "axis": "x",
        "distance_mm": 80}, ...] (negative distance = the other way).
      method='auto' -> SW's AutoExplode (moves loose parts, leaves nested ones overlapping).
    Returns {view, method, steps: [{component, axis, distance_mm, path_clear}], overlaps}
    where overlaps are the component pairs whose boxes still overlap when exploded
    (exploded_overlaps) -- an empty list is the goal.
    MEASURED 2026-09-25: AddExplodeStep is NOT a no-op through pywin32 -- the old probe
    never called IAssemblyDoc.CreateExplodedView first. With it, the v1 step (which SW
    2017 also has) moves the part the exact distance. On SW 2026 CreateExplodedView also
    adds an automatic 'Chain1' step that moved even the FIXED part; it is deleted here."""
    asm, md = _asm(), _md()
    if method == "auto":
        if not asm.AutoExplode():
            raise RuntimeError("explode: AutoExplode failed (does the assembly have movable "
                               "components?).")
        md.EditRebuild3()
        names = list(asm.GetExplodedViewNames() or [])
        return {"view": names[-1] if names else "", "method": "auto", "steps": [],
                "overlaps": exploded_overlaps()}
    if method != "plan":
        raise ValueError(f"method '{method}' is invalid ('plan'|'auto').")
    views_before = set(asm.GetExplodedViewNames() or [])
    if not asm.CreateExplodedView():
        raise RuntimeError("explode: CreateExplodedView failed.")
    cfg = C.cast(md.GetActiveConfiguration(), "IConfiguration")
    for k in reversed(range(cfg.GetNumberOfExplodeSteps())):
        cfg.DeleteExplodeStep(C.cast(cfg.GetExplodeStep(k), "IExplodeStep").Name)
    md.EditRebuild3()
    view = next((n for n in (asm.GetExplodedViewNames() or []) if n not in views_before), "")
    comps = _top_components()
    by_name = {c.Name2: c for c in comps}
    done = []
    if steps is not None:
        for s in steps:
            comp = by_name.get(s["component"])
            if comp is None:
                raise ValueError(f"explode: no component '{s['component']}' "
                                 f"({sorted(by_name)}).")
            axis = str(s["axis"]).lower()
            if axis not in _EXPLODE_AXES:
                raise ValueError(f"explode: axis '{s['axis']}' is invalid (x|y|z).")
            _add_explode_step(cfg, comp, axis, float(s["distance_mm"]))
            done.append({"component": comp.Name2, "axis": axis,
                         "distance_mm": float(s["distance_mm"]), "path_clear": None})
    else:
        anchors = [c for c in comps if c.IsFixed()] or comps[:1]
        movers = [c for c in reversed(comps) if c.Name2 not in {a.Name2 for a in anchors}]
        for comp in movers:
            obstacles = [_box_mm(o) for o in comps if o.Name2 != comp.Name2]
            move = _exit_move(_box_mm(comp), obstacles, clearance_mm)
            if move is None:
                continue              # already clear of everything
            axis, sign, travel, clear = move
            _add_explode_step(cfg, comp, axis, sign * travel)
            done.append({"component": comp.Name2, "axis": axis,
                         "distance_mm": sign * travel, "path_clear": clear})
    md.EditRebuild3()
    return {"view": view, "method": "plan" if steps is None else "steps", "steps": done,
            "overlaps": exploded_overlaps()}


def collapse() -> bool:
    """Collapse (UN-exploded state) the exploded view of the active config. Returns True."""
    _asm().ShowExploded(False)
    _md().EditRebuild3()
    return True


# ── save / export (parasolid/step for Mechanical) ─────────────────────────────
def save(path: str) -> str:
    """Save the assembly to `path` (silent SaveAs -- see the Save3 gotcha)."""
    md = _md()
    sw = C.app()
    import os
    prev = sw.GetOpenDocumentByName(path)
    if prev is not None:
        pm = C.cast(prev, "IModelDoc2")
        if pm.GetPathName() != md.GetPathName():
            sw.CloseDoc(os.path.basename(path))
    if not md.SaveAs(path):
        raise RuntimeError(f"SaveAs failed: {path}")
    return path


def export(path: str) -> str:
    """Export the assembly (format from the extension: .x_t Parasolid, .step, ...)."""
    if not _md().SaveAs(path):
        raise RuntimeError(f"export failed: {path}")
    return path
