# -*- coding: utf-8 -*-
"""
sw_parts -- layer 1: PART verbs (part.*). See docs/ARCHITECTURE.md and solidworks/README.md.

Each verb consolidates a FAMILY of COM methods behind a parameter (neither 400 tools nor
1). Homogeneous families -> 1 verb with `kind` (e.g. extrude boss|cut); divergent families
-> sub-verbs (e.g. pattern.*, still to do). They return stable names/ids so calls can be
chained (mode 1).

Units: mm and degrees at the boundary; converted internally by sw_com.mm/deg.
"""
import os

from . import sw_com as C
from . import sw_inspect as I
from . import sw_sketch as S

# swEndConditions_e
BLIND, THROUGH_ALL = 0, 1
# swFeatureFilletType_e (Simple=0) / swFeatureFilletOptions_e (Propagate=1, UniformRadius=2)
FILLET_SIMPLE, FILLET_PROPAGATE, FILLET_UNIFORM = 0, 1, 2
# swChamferType_e (AngleDistance=1, DistanceDistance=2) / swFeatureChamferOption_e (TangentProp=4)
CHAMFER_ANGLE_DIST, CHAMFER_DIST_DIST, CHAMFER_TANGENT_PROP = 1, 2, 4
# swRefPlaneReferenceConstraints_e (Distance=8)
REFPLANE_DISTANCE = 8
# swBodyOperationType_e (combine)
BODY_ADD, BODY_CUT, BODY_INTERSECT = 15903, 15902, 15901
# swDraftType_e
DRAFT_NEUTRAL_PLANE, DRAFT_PARTING_LINE, DRAFT_STEP = 0, 1, 3


# ── document ──────────────────────────────────────────────────────────────────
def _last_feature(md):
    """The last CONSTRUCTION feature of the tree -- ignoring the FLAT PATTERN.

    MEASURED GOTCHA (2026-08-29): in a SHEET METAL part, `FeatureByPositionReverse(0)`
    is not the just-created feature -- it is always `Flat-Pattern1`, which SW keeps
    rolled at the end of the tree. A verb that returns "the feature I just created"
    through that path lies on every sheet metal part, and lies silently (the name does
    exist). On an ordinary part the result is identical.
    """
    feat = C.cast(md.FeatureByPositionReverse(0), "IFeature")
    if feat.GetTypeName2() != "FlatPattern":
        return feat
    last, raw = None, md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() != "FlatPattern":
            last = f
        raw = f.GetNextFeature()
    return last


def _last_sketch(md):
    """Name of the last SKETCH in the tree (same gotcha as `_last_feature`)."""
    feat = _last_feature(md)
    if feat.GetTypeName2() == "ProfileFeature":
        return feat.Name
    name, raw = "", md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() == "ProfileFeature":
            name = f.Name
        raw = f.GetNextFeature()
    return name


def new_part() -> str:
    """Create a new part (default template). Returns the title."""
    sw = C.app()
    tpl = sw.GetUserPreferenceStringValue(C.TPL_PART)
    sw.NewDocument(tpl, 0, 0, 0)
    return C.active("IModelDoc2").GetTitle()


def open_part(path: str) -> str:
    """Open an existing part. Returns the title."""
    sw = C.app()
    # Errors/Warnings are mandatory AT THE CALL SITE (the generated wrapper does not
    # treat them as pure output): with 4 args SW 2017 raises "Type mismatch". It returns
    # the tuple (doc, errors, warnings), of which only the active document matters here.
    sw.OpenDoc6(path, C.DOC_PART, 1, "", 0, 0)
    return C.active("IModelDoc2").GetTitle()


def save(path: str = "") -> str:
    """Save (empty path = Save) or Save-as. Returns the effective path.
    SaveAs fails if the target file is already OPEN (another window) -> close the
    previous instance before overwriting."""
    md = C.active("IModelDoc2")
    if path:
        sw = C.app()
        prev = sw.GetOpenDocumentByName(path)
        if prev is not None:
            prevmd = C.cast(prev, "IModelDoc2")  # the return comes back dynamic -> cast
            if prevmd.GetPathName() != md.GetPathName():
                sw.CloseDoc(os.path.basename(path))
        # SILENT: IModelDoc2's SaveAs can open the save box and stall automation waiting
        # for the user (see sw_com.save_as).
        return C.save_as(md, path)
    # MEASURED GOTCHA (2026-08-30): `IModelDoc2.Save3` raises 'Type mismatch' under early
    # binding with this typelib (its two byref out-params) -- the same class of defect as
    # ActivateDoc2/ActivateDoc3. Saving OVER the document's own path via the silent SaveAs
    # works and already handles the return tuple.
    current = md.GetPathName()
    if not current:
        raise RuntimeError("save(): document has no path -- pass the path (Save-as)")
    return C.save_as(md, current)


def export(path: str) -> str:
    """Export the part in the format inferred from the extension (.step/.x_t/.stl/.iges).
    E.g. export('part.x_t') -> Parasolid; export('part.step') -> STEP."""
    md = C.active("IModelDoc2")
    if not md.SaveAs(path):
        raise RuntimeError(f"export failed: {path}")
    return path


# ── sketch (family: sketch primitives) ────────────────────────────────────────
def sketch(plane: str, shape: str, **p) -> str:
    """Create a sketch on `plane` ('Front'/'Top'/'Right' or the real name) and draw
    `shape`.
    shapes (mm):
      rect    -> x1,y1,x2,y2        (rectangle by corners)
      circle  -> r [, cx=0, cy=0]   (circle by radius)
    Closes the sketch. Returns the sketch name (for the following extrude/revolve)."""
    md = C.active("IModelDoc2")
    C.select_plane(md, plane)
    sm = C.cast(md.SketchManager, "ISketchManager")
    sm.InsertSketch(True)
    s = shape.lower()
    if s in ("rect", "rectangle"):
        with C.sketch_direct(sm):          # no inference (see sw_com.sketch_direct)
            sm.CreateCornerRectangle(C.mm(p["x1"]), C.mm(p["y1"]), 0.0,
                                     C.mm(p["x2"]), C.mm(p["y2"]), 0.0)
    elif s == "circle":
        with C.sketch_direct(sm):
            sm.CreateCircleByRadius(C.mm(p.get("cx", 0.0)), C.mm(p.get("cy", 0.0)), 0.0,
                                    C.mm(p["r"]))
    else:
        md.ClearSelection2(True)
        raise ValueError(f"shape '{shape}' not supported (rect|circle).")
    md.ClearSelection2(True)
    sm.InsertSketch(True)  # closes it
    return _last_sketch(md)


# ── extrude (HOMOGENEOUS family: boss|cut through kind) ───────────────────────
def extrude(depth_mm: float, kind: str = "boss", *, reverse: bool = False,
            through_all: bool = False, sketch_name: str = "", merge: bool = True,
            thin_mm: float = 0.0) -> str:
    """Extrude the sketch (the most recent one, or `sketch_name`) as a boss or a cut.
    kind='boss' (adds material) | 'cut' (removes). through_all ignores depth_mm.
    merge=False creates a separate BODY (multibody, for combine).
    thin_mm>0 => THIN WALL extrusion (thickness thin_mm; the profile may be OPEN --
    e.g. a line/arc becomes a shell/tube). Only for kind='boss'. Returns the feature."""
    md = C.active("IModelDoc2")
    sk = sketch_name or _last_sketch(md)
    md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    if not ext.SelectByID2(sk, "SKETCH", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError(f"could not select sketch '{sk}'.")
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    t1 = THROUGH_ALL if through_all else BLIND
    d1 = 0.0 if through_all else C.mm(depth_mm)
    if kind == "boss" and thin_mm > 0:
        # FeatureExtrusionThin2: thin wall of thickness thin_mm (open/closed profile).
        feat = fm.FeatureExtrusionThin2(
            True, False, bool(reverse), t1, 0, d1, 0.0,
            False, False, False, False, C.deg(0), C.deg(0),
            False, False, False, False, bool(merge),
            C.mm(thin_mm), 0.0, 0.0, False, False, False, 0.0,
            True, True, 0, 0.0, False)
    elif kind == "boss":
        feat = fm.FeatureExtrusion2(
            True, False, bool(reverse), t1, 0, d1, 0.01,
            False, False, False, False, C.deg(1), C.deg(1),
            False, False, False, False, bool(merge), True, True, 0, 0, False)
    elif kind == "cut":
        feat = fm.FeatureCut4(
            True, False, bool(reverse), t1, 0, d1, 0.01,
            False, False, False, False, C.deg(0), C.deg(0),
            False, False, False, False, False, True, True,
            False, False, False, 0, 0.0, False, False)
    else:
        raise ValueError(f"kind '{kind}' is invalid (boss|cut).")
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError(f"extrude {kind} failed (returned None).")
    return feat.Name


# ── revolve (profile + centerline as the axis) ────────────────────────────────
def revolve(angle_deg: float = 360.0, kind: str = "boss", *, sketch_name: str = "",
            reverse: bool = False) -> str:
    """Revolve the sketch (closed profile + 1 centerline = axis) as a boss or a cut.
    The most recent sketch (or `sketch_name`) must contain ONE centerline. Returns the
    feature."""
    md = C.active("IModelDoc2")
    sk = sketch_name or _last_sketch(md)
    md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    if not ext.SelectByID2(sk, "SKETCH", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError(f"could not select sketch '{sk}'.")
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.FeatureRevolve2(
        True, True, False, kind == "cut", bool(reverse), False,
        0, 0, C.deg(angle_deg), 0.0, False, False, 0.0, 0.0,
        0, 0.0, 0.0, True, True, True)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError(f"revolve {kind} failed (invalid profile/axis?).")
    return feat.Name


# ── sweep / loft (profile+path features / between profiles) ───────────────────
def sweep(profile_sketch: str, path_sketch: str, *, thin_mm: float = 0.0,
          merge: bool = True) -> str:
    """Sweep a closed PROFILE along a PATH (boss). Both are sketch names (the profile on
    one plane, the path crossing it). thin_mm>0 = thin wall (the profile may be open).
    Returns the feature. Typical flow: draw the path on one plane, the profile on another
    plane at the tip of the path, then call sweep(profile, path)."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    # SELECTION: profile = mark 1, path = mark 4.
    if not ext.SelectByID2(profile_sketch, "SKETCH", 0, 0, 0, False, 1, None, 0):
        raise RuntimeError(f"sweep: could not select the profile '{profile_sketch}'.")
    ext.SelectByID2(path_sketch, "SKETCH", 0, 0, 0, True, 4, None, 0)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    thin = thin_mm > 0
    feat = fm.InsertProtrusionSwept3(
        False, False, 0, False, False, 0, 0, thin, C.mm(thin_mm), 0.0, 0, 0,
        bool(merge), True, True, 0.0, True)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("sweep failed (invalid or disconnected profile/path).")
    return C.cast(feat, "IFeature").Name


def loft(profile_sketches, *, closed: bool = False, merge: bool = True) -> str:
    """Loft/blend between 2+ PROFILES (list of sketch names, in order). closed=True
    closes the loft (joins the last to the first). Returns the feature. The profiles
    usually sit on offset parallel planes (use reference_plane)."""
    if len(profile_sketches) < 2:
        raise ValueError("loft requires 2+ profiles.")
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    for i, sk in enumerate(profile_sketches):
        # each profile with mark 1; select near the SAME corner so it does not twist
        if not ext.SelectByID2(sk, "SKETCH", 0, 0, 0, i > 0, 1, None, 0):
            raise RuntimeError(f"loft: could not select the profile '{sk}'.")
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.InsertProtrusionBlend2(
        bool(closed), False, False, 0.1, 0, 0, 0.0, 0.0, False, False,
        False, 0.0, 0.0, 0, bool(merge), True, True, 0)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("loft failed (profiles not loftable / invalid order).")
    return C.cast(feat, "IFeature").Name


# ── hole (simple positioned hole; 1+ holes in one cut) ────────────────────────
def hole(plane: str, diameter_mm: float, positions, *, through_all: bool = True,
         depth_mm: float = 0.0, reverse: bool = False) -> str:
    """Simple hole(s): draw circles of diameter `diameter_mm` at `positions`
    [(x,y),...] (mm, on `plane`) and cut them in a single feature. through_all or
    depth_mm. For STANDARD fastener holes (countersunk/counterbored/threaded) use the
    HoleWizard through the escape hatch (see recipe `part-hole`). Returns the feature."""
    S.begin(plane)
    for (x, y) in positions:
        S.circle(x, y, diameter_mm / 2.0)
    S.end()
    return extrude(depth_mm, "cut", through_all=through_all, reverse=reverse)


# ── dimension (parametric -- the optimization loop drives dims through here) ──
def _param(md, name):
    p = md.Parameter(name)
    if p is None:
        raise RuntimeError(
            f"dimension '{name}' does not exist. Feature dimensions (e.g. "
            f"'D1@Boss-Extrude1', 'D1@Fillet1') always exist; sketch dimensions only if "
            f"they were ADDED (CreateCornerRectangle creates no dimensions).")
    return C.cast(p, "IDimension")


# swSetValueInConfiguration_e.swSetValue_InSpecificConfigurations
_IN_SPECIFIC = 3


def _config_names(names):
    """The configuration-name array SetSystemValue3/GetSystemValue3 take. MEASURED: a
    plain Python list is accepted and IGNORED (Get returns None, Set changes nothing);
    only an explicit VT_ARRAY|VT_BSTR works."""
    names = [names] if isinstance(names, str) else list(names)
    return C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_BSTR, names)


def _check_config(md, config):
    names = list(md.GetConfigurationNames() or [])
    if config not in names:
        raise ValueError(f"configuration '{config}' does not exist. Options: {names}")


def get_dimension(name: str, *, config: str = "") -> float:
    """Read a dimension by name (e.g. 'D1@Boss-Extrude1' or a renamed one) in mm.
    `config` reads its value in that configuration (default: the active one)."""
    md = C.active("IModelDoc2")
    dim = _param(md, name)
    if not config:
        return dim.SystemValue * 1000.0
    _check_config(md, config)
    return dim.GetSystemValue3(_IN_SPECIFIC, _config_names(config))[0] * 1000.0


def set_dimension(name: str, value_mm: float, *, rebuild: bool = True,
                  config: str = "") -> float:
    """Set a dimension (mm) and rebuild (rebuild=False to defer). Returns the value.
    `config`: '' = the document's current setting (usually the active configuration);
    a configuration name = ONLY that one (e.g. 'Large'); 'all' = every configuration."""
    md = C.active("IModelDoc2")
    dim = _param(md, name)
    if not config:
        dim.SystemValue = C.mm(value_mm)
    elif config == "all":
        # MEASURED (SW 2026): swSetValue_InAllConfigurations, and InSpecific with every
        # name in one array, set the active configuration and left the others reading
        # 0 -- one configuration per call is the path that holds
        for cfg in md.GetConfigurationNames() or []:
            dim.SetSystemValue3(C.mm(value_mm), _IN_SPECIFIC, _config_names(cfg))
    else:
        _check_config(md, config)
        dim.SetSystemValue3(C.mm(value_mm), _IN_SPECIFIC, _config_names(config))
    if rebuild:
        md.EditRebuild3()
    return value_mm


def set_dimensions(deltas: dict, *, rebuild: bool = True) -> dict:
    """Set SEVERAL dimensions {name: mm} and rebuild ONCE (the optimization loop uses
    this). Returns the dict that was applied."""
    for name, val in deltas.items():
        set_dimension(name, val, rebuild=False)
    if rebuild:
        C.active("IModelDoc2").EditRebuild3()
    return deltas


# swTolType_e
TOL_NONE, TOL_BASIC, TOL_BILAT, TOL_LIMIT, TOL_SYMMETRIC = 0, 1, 2, 3, 4
TOL_FIT, TOL_FITWITHTOL, TOL_FITTOLONLY = 7, 8, 9


def set_tolerance(dim_name: str, kind: str = "bilateral", *, upper_mm: float = 0.0,
                  lower_mm: float = 0.0, hole_fit: str = "", shaft_fit: str = "",
                  rebuild: bool = True) -> str:
    """Apply a TOLERANCE to a model dimension (propagates to the drawing on import).
      kind='bilateral' -> deviations: upper_mm (upper, e.g. +0.2) and lower_mm (lower,
                          WITH SIGN, e.g. -0.1 or 0.0).
      kind='symmetric' -> +/- upper_mm (one value).
      kind='fit'       -> ISO fit by LETTER+IT: hole_fit (e.g. 'H7') and/or shaft_fit
                          (e.g. 'g6'); shows the fit symbol + the computed deviations.
      kind='basic'     -> basic dimension (boxed), no tolerance.
    Dimension names as in set_dimension ('D1@Sketch1', 'D1@Boss-Extrude1' or renamed).
    Returns the name. Requires the dimension to EXIST (dimension the part first).
    GOTCHA: use `IDimensionTolerance.SetValues(min, max)` -- `SetValues2` returns False
    and does NOT apply on SW 2017 (leaves 0/0). Values in METERS (via sw_com.mm)."""
    md = C.active("IModelDoc2")
    dim = _param(md, dim_name)
    tol = C.cast(dim.Tolerance, "IDimensionTolerance")
    k = kind.lower()
    if k == "bilateral":
        tol.Type = TOL_BILAT
        tol.SetValues(C.mm(lower_mm), C.mm(upper_mm))
    elif k == "symmetric":
        tol.Type = TOL_SYMMETRIC
        tol.SetValues(C.mm(-abs(upper_mm)), C.mm(abs(upper_mm)))
    elif k == "fit":
        if not (hole_fit or shaft_fit):
            raise ValueError("kind='fit' requires hole_fit and/or shaft_fit (e.g. 'H7').")
        tol.Type = TOL_FITWITHTOL
        tol.SetFitValues(hole_fit, shaft_fit)
    elif k == "basic":
        tol.Type = TOL_BASIC
    else:
        raise ValueError(f"kind '{kind}' is invalid (bilateral|symmetric|fit|basic).")
    if rebuild:
        md.EditRebuild3()
    return dim_name


# ── embedded equations (relations/global variables INSIDE the model) ──────────
def add_equation(equation: str, *, rebuild: bool = True) -> int:
    """Add an EQUATION embedded in the model (parametric fit INSIDE the file).
    Formats (units = the document's units, typically mm):
      global variable:   '"clearance" = 0.3'
      drive a dimension: '"D1@Fillet1" = "hole_radius" - "clearance"'
                         '"D1@Boss-Extrude1" = 25'
    Returns the index of the created equation. Rebuilds by default (the dimension
    follows the equation). See recipe `part-equations-embedded`.

    Requirements (otherwise the API returns -1): the LEFT-HAND dimension must EXIST
    (feature dimensions 'D1@Feature' always do; sketch dimensions from
    CreateCornerRectangle do NOT) and must NOT already be driven by another equation.
    ALWAYS use this function (the 2-arg Add, at index = current count); Add2/Add3 fail
    through pywin32 on SW 2017."""
    md = C.active("IModelDoc2")
    em = C.cast(md.GetEquationMgr(), "IEquationMgr")
    idx = em.Add(em.GetCount(), equation)  # Index = next (append); the 2-arg Add
    if idx < 0:
        raise RuntimeError(
            f"add_equation failed (ret=-1): '{equation}'. Causes: the left-hand "
            f"dimension does not exist (dimension the part / use a feature dimension "
            f"name), it is already driven by another equation, or the syntax/names are "
            f"wrong.")
    if rebuild:
        em.EvaluateAll()
        md.EditRebuild3()
    return idx


def equations() -> list:
    """List the model's embedded equations: [(index, text, value)]."""
    em = C.cast(C.active("IModelDoc2").GetEquationMgr(), "IEquationMgr")
    return [(i, em.Equation(i), em.Value(i)) for i in range(em.GetCount())]


# ── configurations (part or assembly: the ACTIVE document) ────────────────────
def configurations() -> dict:
    """The configurations of the active part/assembly: {active, names:[...]}."""
    md = C.active("IModelDoc2")
    active = C.cast(md.GetActiveConfiguration(), "IConfiguration").Name
    return {"active": active, "names": list(md.GetConfigurationNames() or [])}


def activate_configuration(name: str) -> str:
    """Make `name` the active configuration (and rebuild). Returns the active name."""
    md = C.active("IModelDoc2")
    _check_config(md, name)
    if not md.ShowConfiguration2(name):
        raise RuntimeError(f"activate_configuration: SolidWorks refused '{name}'")
    md.EditRebuild3()
    return C.cast(md.GetActiveConfiguration(), "IConfiguration").Name


def add_configuration(name: str, *, activate: bool = True, comment: str = "") -> str:
    """Create a configuration (a copy of the ACTIVE one's feature states and values).
    `activate`=False keeps the current one active. Then change what differs with
    set_dimension(..., config=name). Returns the name."""
    md = C.active("IModelDoc2")
    if name in list(md.GetConfigurationNames() or []):
        raise ValueError(f"configuration '{name}' already exists")
    opts = 0 if activate else 128                      # swConfigOption_DontActivate
    if md.AddConfiguration3(name, comment, "", opts) is None:
        raise RuntimeError(f"add_configuration: SolidWorks did not create '{name}'")
    return name


def rename_configuration(name: str, new_name: str) -> str:
    """Rename a configuration. Returns the new name."""
    md = C.active("IModelDoc2")
    _check_config(md, name)
    if new_name in list(md.GetConfigurationNames() or []):
        raise ValueError(f"configuration '{new_name}' already exists")
    C.cast(md.GetConfigurationByName(name), "IConfiguration").Name = new_name
    if new_name not in list(md.GetConfigurationNames() or []):
        raise RuntimeError(f"rename_configuration: '{name}' was not renamed")
    return new_name


def delete_configuration(name: str) -> list:
    """Delete a configuration (not the ACTIVE one -- activate another first). Returns the
    names left."""
    md = C.active("IModelDoc2")
    _check_config(md, name)
    if C.cast(md.GetActiveConfiguration(), "IConfiguration").Name == name:
        raise ValueError(f"'{name}' is the active configuration: activate another one "
                         "before deleting it")
    if not md.DeleteConfiguration2(name):
        raise RuntimeError(f"delete_configuration: SolidWorks refused to delete '{name}'")
    return list(md.GetConfigurationNames() or [])


# ── custom properties (metadata; feed the drawing titleblock via $PRPSHEET) ───
def set_property(name: str, value: str, *, config: str = "") -> str:
    """Set a custom PROPERTY of the part (metadata). The drawing titleblock reads
    several through `$PRPSHEET:"name"` -> e.g. 'Material', 'Finish',
    'Description'/'Title', 'Weight'. Empty `config` = file properties (not per
    configuration). Returns the name. swCustomInfoText=30; option 1 = overwrite if it
    already exists."""
    ext = C.cast(C.active("IModelDoc2").Extension, "IModelDocExtension")
    cpm = C.cast(ext.CustomPropertyManager(config), "ICustomPropertyManager")
    cpm.Add3(name, 30, str(value), 1)
    return name


def get_property(name: str, *, config: str = "", resolved: bool = True) -> str:
    """Read a custom PROPERTY (round-trip with set_property). `resolved`=True returns the
    RESOLVED value (equations/links evaluated, e.g. '$PRPSHEET' expanded); False returns
    the raw text. Empty `config` = file properties. Returns str ('' if absent).
    SW 2017 GOTCHA: there is no Get6; the newest is Get5, which returns
    (status, valOut, resolvedOut, wasResolved). For a PLAIN TEXT property resolvedOut
    comes back EMPTY (it only fills in when there is a '$' link) -> fall back to valOut."""
    ext = C.cast(C.active("IModelDoc2").Extension, "IModelDocExtension")
    cpm = C.cast(ext.CustomPropertyManager(config), "ICustomPropertyManager")
    res = cpm.Get5(name, False)
    if not isinstance(res, (tuple, list)):
        return res or ""
    val = res[1] if len(res) > 1 else ""
    resolved_val = res[2] if len(res) > 2 else ""
    return ((resolved_val or val) if resolved else val) or ""


# ── material (essential for FEA: E, nu, rho) ──────────────────────────────────
def set_material(name: str, database: str = "SOLIDWORKS Materials", config: str = "") -> str:
    """Set the part's material (e.g. '6061 Alloy', 'AISI 1020', 'Aluminum 7075-T6').
    Affects density/E/nu (FEA reads them from here). Returns the name."""
    pd = C.cast(C.active("IPartDoc"), "IPartDoc")
    pd.SetMaterialPropertyName2(config, database, name)
    return name


def get_material(config: str = ""):
    """Return (material_name, database) of the active part."""
    pd = C.cast(C.active("IPartDoc"), "IPartDoc")
    res = pd.GetMaterialPropertyName2(config)  # Database is a byref out -> comes in the return
    if isinstance(res, (tuple, list)):
        return (res[0], res[1] if len(res) > 1 else "")
    return (res, "")


# ── body_op: scale / combine / move / split (BODY operations, multibody) ──────
def scale(factor: float, *, about: str = "centroid") -> str:
    """UNIFORMLY scale the part by `factor` (about: 'centroid'|'origin'). Volume x
    factor^3. Returns the feature name."""
    md = C.active("IModelDoc2")
    about_i = {"centroid": 0, "origin": 1}.get(about, 0)
    md.InsertScale(factor, factor, factor, True, about_i)
    return _last_feature(md).Name


def combine(operation: str = "add", *, main_body=None, tool_bodies=None) -> str:
    """Boolean of BODIES (multibody -> one body). `operation`:
      'add'       -> unions ALL bodies (SWBODYADD);
      'common'    -> intersection of ALL bodies (SWBODYINTERSECT);
      'subtract'  -> main_body MINUS tool_bodies (SWBODYCUT).
    SW 2017 convention (discovered live): add/common pass main=Nothing and all bodies as
    tools; subtract passes main=what stays and tools=what is removed. With no args:
    add/common use every body; subtract requires explicit main_body and tool_bodies.
    Bodies = IBody2 (e.g. sw_inspect.bodies()). Returns the feature. Requires MULTIBODY
    (create bodies with extrude(..., merge=False))."""
    op = {"add": BODY_ADD, "subtract": BODY_CUT, "common": BODY_INTERSECT}.get(operation)
    if op is None:
        raise ValueError(f"operation '{operation}' is invalid (add|subtract|common).")
    bs = I.bodies()
    n0 = len(bs)
    pd = C.cast(C.active("IPartDoc"), "IPartDoc")
    if operation == "subtract":
        if main_body is None or tool_bodies is None:
            raise ValueError("combine('subtract') requires main_body (stays) and "
                             "tool_bodies (removed).")
        main_raw = C.cast(main_body, "IBody2")._oleobj_
    else:  # add / common: main = Nothing, tools = every body
        if n0 < 2:
            raise RuntimeError(f"combine '{operation}' requires 2+ bodies (multibody); "
                               f"there are {n0}.")
        main_raw = None
        tool_bodies = bs
    tools = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_DISPATCH,
                            [C.cast(b, "IBody2")._oleobj_ for b in tool_bodies])
    ok = pd.InsertCombineFeature(op, main_raw, tools)
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    # InsertCombineFeature returns a BOOL (True=ok) on SW 2017, not the Feature -> take
    # it from the tree.
    if not ok:
        raise RuntimeError(f"combine {operation} failed (disjoint bodies / no "
                           f"intersection?).")
    if len(I.bodies()) >= n0:
        raise RuntimeError(f"combine {operation} did not reduce the body count "
                           f"({n0} -> {len(I.bodies())}).")
    return _last_feature(md).Name


def delete_body(bodies) -> str:
    """Delete the selected body/bodies (IBody2, e.g. sw_inspect.bodies()). Useful to
    clean up tool bodies after a manual boolean, or to discard the wrong half of a
    multibody. Returns the 'Delete Body' feature."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    for i, b in enumerate(bodies):
        C.cast(b, "IBody2").Select2(i > 0, None)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.InsertDeleteBody()
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("delete_body failed (no body selected?).")
    return C.cast(feat, "IFeature").Name


# NOTE (body move/copy -> ESCAPE HATCH): `IFeatureManager.InsertMoveCopyBody2` (and v1)
# return Nothing and do NOT move the body through pywin32 on SW 2017, tested with the
# body selected via Body2.Select2, via SelectByID2 'SOLIDBODY', with translation in
# meters and with direction+distance, move and copy. Unreliable marshaling (same class
# as GetRemainingDOFs). If you need it, use sw_call + the FeatureData path
# (CreateDefinition/IMoveCopyBodyFeatureData.ISetBodies/CreateFeature). See recipe
# `part-body-ops-combine` (move section).

# NOTE (body split -> ESCAPE HATCH): `PreSplitBody2` computes the pieces (returns the
# tuple of bodies), but `PostSplitBody(marks, consume, origins, savepaths)` returns
# Nothing and does not materialize the bodies through pywin32 (tested with marks I4,
# consume T/F, origins/savepaths None/empty). To split bodies, use sw_call or a cutting
# plane + combine('subtract').


# ── wall_op: shell / rib / draft ──────────────────────────────────────────────
def shell(thickness_mm: float, remove_faces, *, outward: bool = False) -> str:
    """Shell (hollow out) the part with wall thickness `thickness_mm`, OPENING the
    `remove_faces` (list of IFace2). Great for shedding mass. Returns the feature."""
    md = C.active("IModelDoc2")
    I.select(remove_faces)
    md.InsertFeatureShell(C.mm(thickness_mm), bool(outward))
    md.ClearSelection2(True)
    return _last_feature(md).Name


# NOTE (rib -> ESCAPE HATCH): `IFeatureManager.InsertRib` (10-arg, void) does NOT create
# the feature through pywin32 on SW 2017 -- a silent no-op ('Rib' never shows up in the
# tree, mass does not change) tested with the sketch open AND closed-and-selected, with a
# diagonal gusset joining the two faces of an L-bracket, ReferenceEdgeIndex -1 and 0, and
# the 4 combinations of norm_to_sketch x reverse_material. Since a rib depends heavily on
# a perfect profile<->wall fit, use sw_call when you need one (see recipe
# `part-body-ops-combine`).


def draft(neutral, faces, angle_deg: float, *, reverse: bool = False) -> str:
    """Manufacturing DRAFT angle: tilt the `faces` (list of IFace2) by `angle_deg`
    relative to the NEUTRAL plane/face `neutral` (IFace2 or a plane name
    'Front'/'Top'/'Right'). reverse flips the direction. Returns the feature."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    # SELECTION (discovered live): NEUTRAL plane/face = mark 1; faces to tilt = mark 2.
    if isinstance(neutral, str):
        C.cast(md.Extension, "IModelDocExtension").SelectByID2(
            C.resolve_plane(md, neutral), "PLANE", 0, 0, 0, False, 1, None, 0)
    else:
        I.select_marked(neutral, 1)
    for f in faces:
        I.select_marked(f, 2, append=True)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.InsertMultiFaceDraft(C.deg(angle_deg), bool(reverse), False,
                                   DRAFT_NEUTRAL_PLANE, False, False)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("draft failed (returned None; check neutral/faces/direction).")
    return C.cast(feat, "IFeature").Name


def _top_face():
    f = I.find_face("plane", axis=1, want_max=True)
    if f is None:
        raise RuntimeError("top face (+Y) not found.")
    return f


def counterbore(positions, d_hole_mm: float, d_bore_mm: float, bore_depth_mm: float) -> str:
    """COUNTERBORED hole on the top face: through hole d_hole + coaxial counterbore
    d_bore of depth bore_depth. positions=[(u,v),...] in FACE coordinates (two cuts on
    the SAME face -> coaxial). Robust, with no standards database."""
    S.begin_on_face(_top_face())
    for (u, v) in positions:
        S.circle(u, v, d_hole_mm / 2.0)
    S.end()
    extrude(0, "cut", through_all=True)
    S.begin_on_face(_top_face())          # find it again (the face changed after the cut)
    for (u, v) in positions:
        S.circle(u, v, d_bore_mm / 2.0)
    S.end()
    return extrude(bore_depth_mm, "cut")


def countersink(positions, d_hole_mm: float, d_sink_mm: float, angle_deg: float = 90.0) -> str:
    """COUNTERSUNK hole on the top face: through hole d_hole + cone d_sink (a cut with a
    draft angle = countersink). positions in FACE coordinates. Returns the feature."""
    S.begin_on_face(_top_face())
    for (u, v) in positions:
        S.circle(u, v, d_hole_mm / 2.0)
    S.end()
    extrude(0, "cut", through_all=True)
    # cone: depth = sink_radius / tan(half the angle)
    import math
    half = math.radians(angle_deg / 2.0)
    depth = (d_sink_mm / 2.0) / math.tan(half) if half > 0 else d_sink_mm
    S.begin_on_face(_top_face())
    for (u, v) in positions:
        S.circle(u, v, d_sink_mm / 2.0)
    S.end()
    md = C.active("IModelDoc2")
    sk = _last_sketch(md)
    md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    ext.SelectByID2(sk, "SKETCH", 0, 0, 0, False, 0, None, 0)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    # FeatureCut4 with draft (Dchk1=True, Ddir1=True inward): a cone narrowing inward
    feat = fm.FeatureCut4(
        True, False, False, BLIND, 0, C.mm(depth), 0.01,
        True, False, True, False, half, 0.0,
        False, False, False, False, False, True, True,
        False, False, False, 0, 0.0, False, False)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("countersink: the cone (FeatureCut4 with draft) failed.")
    return feat.Name


# ── fillet / chamfer (edge treatment) ─────────────────────────────────────────
def fillet(radius_mm: float, edges=None) -> str:
    """Round edges (constant radius). `edges` = list of IEdge (e.g. sw_inspect.edges());
    if None, uses the current selection. Returns the feature name."""
    md = C.active("IModelDoc2")
    if edges is not None:
        I.select(edges)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.FeatureFillet3(FILLET_PROPAGATE | FILLET_UNIFORM, C.mm(radius_mm), 0, 0,
                             FILLET_SIMPLE, 0, 0,
                             None, None, None, None, None, None, None)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("fillet failed (returned None).")
    return feat.Name


def chamfer(dist_mm: float, angle_deg: float = 45.0, edges=None) -> str:
    """Chamfer edges (distance+angle). `edges` = list of IEdge; if None, uses the current
    selection. Returns the feature name."""
    md = C.active("IModelDoc2")
    if edges is not None:
        I.select(edges)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.InsertFeatureChamfer(CHAMFER_TANGENT_PROP, CHAMFER_ANGLE_DIST,
                                   C.mm(dist_mm), C.deg(angle_deg), 0.0, 0.0, 0.0, 0.0)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("chamfer failed (returned None).")
    return feat.Name


# ── reference (named datums; the bridge to mates) ─────────────────────────────
def reference_plane(from_plane: str, offset_mm: float, name: str = "") -> str:
    """Create a reference plane parallel to `from_plane` ('Front'/'Top'/'Right' or the
    real name), offset by `offset_mm`. If `name` is given, rename it (useful as a mate
    reference). Returns the feature name."""
    md = C.active("IModelDoc2")
    C.select_plane(md, from_plane)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    # CAREFUL: the distance args are Double -> pass 0.0 (int 0 gives 'Type mismatch').
    # The return comes back as a tuple of floats (not a Feature) -> take the feature from
    # the tree.
    fm.InsertRefPlane(REFPLANE_DISTANCE, C.mm(offset_mm), 0, 0.0, 0, 0.0)
    md.ClearSelection2(True)
    feat = _last_feature(md)
    if feat.GetTypeName2() != "RefPlane":
        raise RuntimeError(f"reference_plane failed (most recent feature = "
                           f"{feat.GetTypeName2()}).")
    if name:
        feat.Name = name
    return feat.Name


def axis_from_face(face, name: str = "") -> str:
    """Create a reference axis from a cylindrical FACE (the cylinder's axis).
    A robust NAMED reference for mates (datum->mate): it does not depend on 'find the
    1st cylindrical face' (which breaks when a fillet/head adds another cylinder).
    Returns the name."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    C.cast(face, "IEntity").Select4(False, None)
    if not md.InsertAxis():
        md.ClearSelection2(True)
        raise RuntimeError("axis_from_face: InsertAxis failed (select a cylindrical face).")
    md.ClearSelection2(True)
    feat = _last_feature(md)
    if feat.GetTypeName2() != "RefAxis":
        raise RuntimeError(f"axis_from_face: most recent feature = {feat.GetTypeName2()}.")
    if name:
        feat.Name = name
    return feat.Name


# NOTE (parametric fit): EMBEDDED equations/global variables on SW 2017 ARE viable via
# `IEquationMgr.Add(GetCount(), eq)` -- see add_equation() above (re-investigated
# 2026-07-23, correcting the earlier finding). What does NOT work: Add2/Add3 (the extra
# params break marshaling -> always -1). No modal dialog when Add is used correctly (the
# risk came from EvaluateAll AFTER a failure, or from AddDiameterDimension2). Even so,
# for the OPTIMIZATION LOOP the engine keeps driving dimensions through the parameter
# layer (set_dimensions) -- a single source of truth, with no hidden state in the file;
# embedded equations are for a PERMANENT FIT (e.g. pin_r = hole_r - clearance locked into
# the part). See recipes `part-equations-embedded` and assembly-realism-checklist.


def reference_axis(plane_a: str = "Front", plane_b: str = "Right", name: str = "") -> str:
    """Create a reference axis at the INTERSECTION of two planes (e.g. Front+Right = the
    vertical axis through the origin). Useful as a circular pattern axis. Returns the
    name."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    ext.SelectByID2(C.resolve_plane(md, plane_a), "PLANE", 0, 0, 0, False, 0, None, 0)
    ext.SelectByID2(C.resolve_plane(md, plane_b), "PLANE", 0, 0, 0, True, 0, None, 0)
    if not md.InsertAxis2(True):
        raise RuntimeError("reference_axis failed (InsertAxis2=False).")
    md.ClearSelection2(True)
    feat = _last_feature(md)
    if feat.GetTypeName2() != "RefAxis":
        raise RuntimeError(f"reference_axis: most recent feature = {feat.GetTypeName2()}.")
    if name:
        feat.Name = name
    return feat.Name


# swRefPointType_e (the useful subset)
REFPT_FACE_CENTER, REFPT_CENTER_EDGE, REFPT_INTERSECTION, REFPT_SKETCH_POINT = 4, 3, 6, 7


def reference_point(entities, kind: str = "face_center", name: str = "") -> str:
    """Create a reference POINT (datum) from the selected entities:
      'face_center'  -> center of the selected FACE (1 IFace2);
      'center_edge'  -> center of an edge/arc (1 IEdge);
      'intersection' -> intersection of 2 entities (e.g. 2 edges / axis+face).
    `entities` = list of IFace2/IEdge. Returns the feature name."""
    kmap = {"face_center": REFPT_FACE_CENTER, "center_edge": REFPT_CENTER_EDGE,
            "intersection": REFPT_INTERSECTION, "sketch_point": REFPT_SKETCH_POINT}
    if kind not in kmap:
        raise ValueError(f"kind '{kind}' is invalid ({sorted(kmap)}).")
    md = C.active("IModelDoc2")
    I.select(entities)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    # returns a TUPLE (array of points) under early binding -> take the feature from the tree.
    fm.InsertReferencePoint(kmap[kind], 0, 0.0, 1)
    md.ClearSelection2(True)
    feat = _last_feature(md)
    if feat.GetTypeName2() != "RefPoint":
        raise RuntimeError(f"reference_point '{kind}' failed (most recent feature = "
                           f"{feat.GetTypeName2()}; invalid selection?).")
    if name:
        feat.Name = name
    return feat.Name


def reference_csys(origin, x_entity=None, y_entity=None, *, name: str = "") -> str:
    """Create a COORDINATE SYSTEM from an ORIGIN (IVertex/ISketchPoint/face) and,
    optionally, entities defining the X and Y axes. A reference for assembly/FEA
    (positioning by csys). Returns the feature name.
    Selection: origin first; then the axis entities. For anything beyond the trivial case,
    use sw_call (the Coordinate System UI has many reference variants)."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    C.cast(origin, "IEntity").Select4(False, None)
    for e in (x_entity, y_entity):
        if e is not None:
            C.cast(e, "IEntity").Select4(True, None)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.InsertCoordinateSystem(False, False, False)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("reference_csys failed (select a valid origin + axes).")
    feat = C.cast(feat, "IFeature")
    if name:
        feat.Name = name
    return feat.Name


# ── pattern.* (SUB-FAMILY: argument shapes diverge -> sub-verbs) ──────────────
def pattern_linear(seed_feature: str, direction_edge, count: int, spacing_mm: float,
                   *, reverse: bool = False) -> str:
    """LINEAR pattern of a feature along a direction edge.
    direction_edge = IEdge (e.g. sw_inspect.find_edge(0)). Returns the feature."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    I.select_marked(direction_edge, 1)              # direction 1 = mark 1
    I.select_feature(seed_feature, 4, append=True)  # seed = mark 4
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.FeatureLinearPattern5(
        count, C.mm(spacing_mm), 1, 0.0, bool(reverse), False, "", "",
        False, False, False, False, True, True, False, False,
        False, False, 0.0, 0.0, False, False)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("pattern_linear failed (returned None).")
    return feat.Name


def pattern_circular(seed_feature: str, axis, count: int, total_angle_deg: float = 360.0,
                     *, equal: bool = True, reverse: bool = False) -> str:
    """CIRCULAR pattern of a feature about an axis. axis = IEntity (e.g. reference_axis)
    or an axis name. Returns the feature."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    if isinstance(axis, str):
        C.cast(md.Extension, "IModelDocExtension").SelectByID2(
            axis, "AXIS", 0, 0, 0, False, 1, None, 0)
    else:
        I.select_marked(axis, 1)                    # axis = mark 1
    I.select_feature(seed_feature, 4, append=True)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.FeatureCircularPattern5(
        count, C.deg(total_angle_deg), bool(reverse), "", False, bool(equal),
        False, False, False, False, 0, 0.0, "", False)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("pattern_circular failed (returned None).")
    return feat.Name


def pattern_mirror(seed_feature: str, mirror_plane: str) -> str:
    """Mirror a feature about a plane ('Front'/'Top'/'Right' or a datum).
    Returns the feature."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    real = C.resolve_plane(md, mirror_plane)
    ext = C.cast(md.Extension, "IModelDocExtension")
    ext.SelectByID2(real, "PLANE", 0, 0, 0, False, 2, None, 0)  # mirror plane = mark 2
    I.select_feature(seed_feature, 1, append=True)              # feature = mark 1
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.InsertMirrorFeature2(False, False, True, False, 0)
    md.ClearSelection2(True)
    if feat is None:
        raise RuntimeError("pattern_mirror failed (returned None).")
    return _last_feature(md).Name


# NOTE (FILL and CURVE-DRIVEN pattern -> ESCAPE HATCH, niche): `FeatureFillPattern`
# (19 args, fills a REGION with a layout) and `FeatureLocalCurveDrivenPattern` require a
# specific selection setup (boundary + direction + seed, with undocumented marks) and are
# little used on the target (TopOpt). Use sw_call if you need them. The common pattern
# shapes are already verbs: pattern_linear / pattern_circular / pattern_mirror.
