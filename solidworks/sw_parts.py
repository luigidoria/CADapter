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

from typing import Literal

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
    if not tpl:
        raise RuntimeError("no default part template configured in SW "
                           "(Tools > Options > Default Templates).")
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
    sw = C.app()
    prev = sw.GetOpenDocumentByName(path)
    if prev is not None and C.cast(prev, "IModelDoc2").GetPathName() != md.GetPathName():
        sw.CloseDoc(os.path.basename(path))   # SaveAs fails onto a file open elsewhere
    if not md.SaveAs(path):
        raise RuntimeError(f"export failed: {path}")
    return path


# ── faces by NAME (the address a caller can say without holding a handle) ─────
# A sketch opened ON A FACE does not inherit the global coordinate system: SolidWorks
# orients it by the face normal, and only the +Z face coincides with the global one.
# Measured face by face on a 50x30x20 block (x 0..50, y 0..30, z 0..20), asking for the
# same point and reading the hole centre back from the shift of the centre of mass:
#
#   face    normal   sketch coords     ->  global point
#   front     +Z        (10,  10)          x=10, y=10     (identity)
#   back      -Z        (-10, 10)          x=10, y=10     (u inverted)
#   top       +Y        (10, -10)          x=10, z=10     (v inverted)
#   bottom    -Y        (10,  10)          x=10, z=10
#   right     +X        (-10, 10)          z=10, y=10     (u inverted)
#   left      -X        (10,  10)          z=10, y=10
#
# Without this translation, asking for (10,10) on the top face drew the circle at z=-10
# -- OUTSIDE the material -- and the cut failed. Only `front` worked, by accident. The
# names follow the OUTWARD normal (top = +Y, front = +Z), not what looks "on top".
_FACES = {
    # face:    (axis, want_max, pair of global axes, sign of u, sign of v)
    "front":  (2, True,  "x,y", +1, +1),
    "back":   (2, False, "x,y", -1, +1),
    "top":    (1, True,  "x,z", +1, -1),
    "bottom": (1, False, "x,z", +1, +1),
    "right":  (0, True,  "z,y", -1, +1),
    "left":   (0, False, "z,y", +1, +1),
}
FACE_NAMES = tuple(sorted(_FACES))


def _no_face(verb: str, face: str) -> str:
    """A 'face not found' message that TEACHES the way out.

    Measured 2026-08-15 on a composite part: a model called a cylinder verb with
    face='top' SEVENTEEN times in a row. The part had no face pointing at +Y: a cylinder
    grows along +Z (Front plane), so ITS top face is 'front'. The message states the rule
    and the next step instead of sending the caller to investigate the wrong thing."""
    return (f"{verb}: no planar face points to the '{face}' side. A face is named by "
            f"the GLOBAL AXIS of its outward normal, not by your idea of 'up': "
            f"front/back = +Z/-Z, top/bottom = +Y/-Y, right/left = +X/-X. CAREFUL: "
            f"part.cylinder and part.block on the default Front plane grow along +Z, so "
            f"the TOP face of such a cylinder is 'front', not 'top'. Call "
            f"inspect.faces with info=true to see which faces exist and where they sit.")


def _face_by_name(verb: str, face: str, face_point=None, body=None):
    """(IFace2, su, sv) for a face NAME, or the error that teaches."""
    key = str(face).strip().lower()
    if key not in _FACES:
        raise ValueError(f"{verb}: face '{face}' is not a face name. The names are "
                         f"{', '.join(FACE_NAMES)} (by the outward normal: top = +Y, "
                         f"front = +Z, right = +X); or pass the face object itself.")
    axis, want_max, _pair, su, sv = _FACES[key]
    f2 = I._face_at(axis, want_max, face_point, body)
    if f2 is None:
        raise RuntimeError(_no_face(verb, key))
    return f2, su, sv


def _open_sketch(verb: str, plane: str = "Front", face=None, face_point=None,
                 body=None) -> tuple:
    """Open a sketch on a plane, on a face object, or on a face by NAME ('top').
    Returns (su, sv), the sign of each sketch axis against the global pair of that face
    -- whoever draws afterwards applies it (see `_uv`). (1, 1) when there is no named
    face.

    The name exists because it was the missing path: to put material ON TOP of a part
    you sketch on its top face, and a handle is something the caller does not have
    without inspecting first. Measured: told to sketch on face='top' with no such path,
    a model fell back to the default 'Top' plane, which passes through the ORIGIN, not
    through the top of the part -- and the material came out detached."""
    md = C.active("IModelDoc2")
    su = sv = 1
    if isinstance(face, str) and face.strip():
        f2, su, sv = _face_by_name(verb, face, face_point, body)
        md.ClearSelection2(True)
        C.cast(f2, "IEntity").Select4(False, None)
    elif face is not None:
        md.ClearSelection2(True)
        if not C.cast(face, "IEntity").Select4(False, None):
            raise RuntimeError(f"{verb}: could not select the face that was passed.")
    else:
        C.select_plane(md, plane)
    C.cast(md.SketchManager, "ISketchManager").InsertSketch(True)
    return su, sv


def _pair(verb: str, key: str, i: int, point) -> tuple:
    """A point on a face is a PAIR. Measured: the common mistake is a TRIPLE that repeats
    the coordinate of the face itself -- the one the pair does not carry."""
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        n = len(point) if isinstance(point, (list, tuple)) else 1
        raise ValueError(
            f"{verb}: `{key}`[{i}] has {n} value(s). A point on a face or plane is a PAIR "
            f"of millimetres in THAT plane, measured from the model origin -- two numbers, "
            f"not three: the third coordinate is the face itself.")
    return float(point[0]), float(point[1])


# ── sketch (family: sketch primitives) ────────────────────────────────────────
SKETCH_SHAPES = ("rect", "rectangle", "circle", "line", "centerline", "arc", "point",
                 "polygon", "slot", "spline")


def sketch(plane: str = "Front", shape: str = "", *, x1: float = None, y1: float = None,
           x2: float = None, y2: float = None, cx: float = 0.0, cy: float = 0.0,
           r: float = None, x: float = None, y: float = None, width: float = None,
           sides: int = 6, inscribed: bool = True, points=None, arc_type: int = 0,
           face=None, face_point=None, body=None) -> str:
    """A complete sketch in ONE call: opens it on `plane` ('Front'/'Top'/'Right' or the
    real name) or on a `face` (a name top|bottom|front|back|left|right, or the face
    object), draws ONE `shape` and closes it. Returns the sketch name (for the following
    extrude/revolve).
    shapes (mm): rect -> x1,y1,x2,y2 (corners) | circle -> r [, cx, cy] | line,
      centerline, arc (tangent) -> x1,y1,x2,y2 | point -> x,y | polygon -> r [, cx, cy,
      sides, inscribed] | slot -> x1,y1,x2,y2,width | spline -> points [[x,y],...].
    On a named face the coordinates are the GLOBAL pair of that face's plane -- (x,y)
    for front/back, (x,z) for top/bottom, (z,y) for right/left -- converted here to the
    face's own sketch axes. `face_point` [x,y,z] picks between two faces with the same
    name. For several shapes in one sketch use sketch.begin / sketch.* / sketch.end."""
    from . import sw_sketch as S
    s = str(shape or "").strip().lower()
    if s not in SKETCH_SHAPES:
        raise ValueError(f"shape '{shape}' is not supported ({', '.join(SKETCH_SHAPES)}).")
    need = {"rect": ("x1", "y1", "x2", "y2"), "rectangle": ("x1", "y1", "x2", "y2"),
            "circle": ("r",), "line": ("x1", "y1", "x2", "y2"),
            "centerline": ("x1", "y1", "x2", "y2"), "arc": ("x1", "y1", "x2", "y2"),
            "point": ("x", "y"), "polygon": ("r",),
            "slot": ("x1", "y1", "x2", "y2", "width"), "spline": ("points",)}[s]
    given = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "r": r, "x": x, "y": y,
             "width": width, "points": points}
    missing = [k for k in need if given[k] is None]
    if missing:
        raise ValueError(f"sketch: shape='{s}' requires {', '.join(need)} "
                         f"(missing: {', '.join(missing)}).")
    md = C.active("IModelDoc2")
    su, sv = _open_sketch("sketch", plane, face, face_point, body)
    u = lambda v: su * float(v)            # noqa: E731
    w = lambda v: sv * float(v)            # noqa: E731
    try:
        if s in ("rect", "rectangle"):
            S.rect(u(x1), w(y1), u(x2), w(y2))
        elif s == "circle":
            S.circle(u(cx), w(cy), r)
        elif s == "line":
            S.line(u(x1), w(y1), u(x2), w(y2))
        elif s == "centerline":
            S.centerline(u(x1), w(y1), u(x2), w(y2))
        elif s == "arc":
            S.arc_tangent(u(x1), w(y1), u(x2), w(y2), arc_type)
        elif s == "point":
            S.point(u(x), w(y))
        elif s == "polygon":
            S.polygon(u(cx), w(cy), r, sides, inscribed)
        elif s == "slot":
            S.slot(u(x1), w(y1), u(x2), w(y2), width)
        else:
            S.spline([(u(a), w(b)) for a, b in
                      (_pair("sketch", "points", i, pt) for i, pt in enumerate(points))])
    finally:
        md.ClearSelection2(True)
        C.cast(md.SketchManager, "ISketchManager").InsertSketch(True)  # closes it
    return _last_sketch(md)


# Schema hints for the typed surfaces (the agent's schema builder). `shape` is required even
# though it has a default here (the positional order `sketch(plane, shape)` is kept for
# existing callers), and each shape's parameters are required only for THAT shape --
# declaring them all required makes a model invent numbers to satisfy the schema.
sketch.schema = {"required": ["shape"], "params": {"shape": {"enum": list(SKETCH_SHAPES)}},
                 "combinations": {"shape": {
                     "rect|rectangle": ["x1", "x2", "y1", "y2"], "circle": ["r"],
                     "arc|centerline|line": ["x1", "x2", "y1", "y2"],
                     "point": ["x", "y"], "polygon": ["r"],
                     "slot": ["width", "x1", "x2", "y1", "y2"], "spline": ["points"]}}}


# ── extrude (HOMOGENEOUS family: boss|cut through kind) ───────────────────────
def extrude(depth_mm: float, kind: Literal["boss", "cut"] = "boss", *, reverse: bool = False,
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
        raise RuntimeError(
            f"extrude {kind} failed (SolidWorks returned Nothing)"
            + (". If it is a cut from a plane, the direction may be flipped -> repeat it "
               "with reverse=True." if kind == "cut" else "."))
    return feat.Name


# ── revolve (profile + centerline as the axis) ────────────────────────────────
def revolve(angle_deg: float = 360.0, kind: Literal["boss", "cut"] = "boss", *, sketch_name: str = "",
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
def hole(plane: str = "Front", diameter_mm: float = None, positions=None, *,
         through_all: bool = True, depth_mm: float = 0.0, reverse: bool = False,
         face=None, face_point=None, body=None) -> str:
    """Simple hole(s): circles of diameter `diameter_mm` at `positions` [[a,b],...] (mm)
    cut in a single feature, through_all or depth_mm. Returns the feature.
    Sketched on `plane`, or -- the usual case on an existing part -- on a FACE found by
    its NAME (face='top'|'bottom'|'front'|'back'|'left'|'right', by the outward normal)
    or passed as an object. On a named face each position is the GLOBAL pair of that
    face's plane: (x,y) for front/back, (x,z) for top/bottom, (z,y) for right/left; the
    cut goes FROM the face into the material. `face_point` [x,y,z] picks between two
    faces with the same name (two bosses at the same height) -- any point on the one you
    mean. For STANDARD fastener holes (countersunk/counterbored/threaded) see
    part.counterbore / part.countersink or the HoleWizard recipe `part-hole`."""
    if diameter_mm is None or positions is None:
        raise ValueError("hole: diameter_mm and positions are required.")
    pts = [_pair("hole", "positions", i, p) for i, p in enumerate(positions)]
    if not pts:
        raise ValueError("hole: `positions` is empty -- give at least one [a, b] pair.")
    su, sv = _open_sketch("hole", plane, face, face_point, body)
    with C.sketch_direct(C.cast(C.active("IModelDoc2").SketchManager,
                                "ISketchManager")) as sm:
        for a, b in pts:
            sm.CreateCircleByRadius(C.mm(su * a), C.mm(sv * b), 0.0,
                                    C.mm(float(diameter_mm) / 2.0))
    S.end()
    try:
        return extrude(depth_mm, "cut", through_all=through_all, reverse=reverse)
    except RuntimeError:
        if isinstance(face, str) and face.strip().lower() in _FACES:
            pair = _FACES[face.strip().lower()][2]
            raise RuntimeError(
                f"hole: the cut failed. On face '{face}' each position is the pair "
                f"({pair}) in millimetres, measured from the model origin -- check that "
                f"the points fall INSIDE the face (inspect.faces with info=true gives "
                f"its box) and that the {diameter_mm} mm diameter fits.") from None
        raise RuntimeError(
            "hole: the cut failed. Most common cause on a PLANE: the cut went to the "
            "WRONG SIDE of it (the direction depends on how the part was extruded) -> "
            "repeat with reverse=True. If it persists, the circles are outside the body; "
            "on an existing part prefer face='<name>', which cuts from the face "
            "inward.") from None


hole.schema = {"required": ["diameter_mm", "positions"]}


# ── primitives in ONE call (block, cylinder) ──────────────────────────────────
# They exist because making a caller orchestrate is what costs: "a 30 mm block" was
# sketch + extrude and, for a local model, the handle chaining between the calls was
# where it failed most. They do not replace sketch + extrude -- whoever needs control
# still has it.
def block(width_mm: float, height_mm: float, depth_mm: float, *, plane: str = "Front",
          x: float = 0.0, y: float = 0.0, at=None, face=None, face_point=None,
          body=None, reverse: bool = False, merge: bool = True) -> str:
    """A rectangular block (or a boss ON a face) in ONE call: width_mm x height_mm in the
    sketch plane, depth_mm is ALWAYS the extrusion -- as in part.extrude.
    On the default Front plane width_mm is X, height_mm is Y and depth_mm grows along +Z;
    the corner is at (x, y), or `at`=[a, b] is the rectangle's CENTRE.
    With face='top'|'bottom'|'front'|'back'|'left'|'right' it is a BOSS: sketched on that
    face of the existing part and extruded OUT of it, with x/y/at in the GLOBAL pair of
    the face's plane ((x,z) for top/bottom, (x,y) for front/back, (z,y) for right/left).
    Returns the feature.
    MEASURED 2026-08-23: `depth_mm` is the extrusion here as in part.extrude on purpose --
    a boss verb that called the in-plane measurement depth_mm had models swap the two
    (the raw model kept doing it even after the description was rewritten; only the
    shared name fixed it)."""
    su, sv = _open_sketch("block", plane, face, face_point, body)
    if at is not None:
        ca, cb = _pair("block", "at", 0, at)
        u0, v0, u1, v1 = ca - width_mm / 2.0, cb - height_mm / 2.0, \
            ca + width_mm / 2.0, cb + height_mm / 2.0
    else:
        u0, v0, u1, v1 = float(x), float(y), float(x) + width_mm, float(y) + height_mm
    md = C.active("IModelDoc2")
    # BOTH corners converted, not just the origin: with sv=-1, `y + h` has to become
    # `-(y + h)`, not `-y + h` -- the rectangle would come out on the other side
    with C.sketch_direct(C.cast(md.SketchManager, "ISketchManager")) as sm:
        sm.CreateCornerRectangle(C.mm(su * u0), C.mm(sv * v0), 0.0,
                                 C.mm(su * u1), C.mm(sv * v1), 0.0)
    S.end()
    try:
        return extrude(depth_mm, "boss", reverse=reverse, merge=merge)
    except RuntimeError:
        where = (f"On face '{face}' the position is the pair "
                 f"({_FACES[face.strip().lower()][2]}) in mm, measured from the model "
                 f"origin -- check that it fits INSIDE the face."
                 if isinstance(face, str) and face.strip().lower() in _FACES
                 else "Zero depth, or the sketch was left open?")
        raise RuntimeError(f"block: the extrusion failed. {where}") from None


def cylinder(diameter_mm: float, height_mm: float, *, plane: str = "Front",
             cx: float = 0.0, cy: float = 0.0, face=None, face_point=None, body=None,
             reverse: bool = False, merge: bool = True) -> str:
    """A cylinder in ONE call: a circle of diameter_mm centred at (cx, cy), extruded
    height_mm. On the default Front plane it grows along +Z, so its end faces are 'front'
    and 'back'. With face='<name>' it is a round boss on that face of the existing part,
    with (cx, cy) in the GLOBAL pair of the face's plane. Returns the feature."""
    su, sv = _open_sketch("cylinder", plane, face, face_point, body)
    md = C.active("IModelDoc2")
    with C.sketch_direct(C.cast(md.SketchManager, "ISketchManager")) as sm:
        sm.CreateCircleByRadius(C.mm(su * float(cx)), C.mm(sv * float(cy)), 0.0,
                                C.mm(float(diameter_mm) / 2.0))
    S.end()
    try:
        return extrude(height_mm, "boss", reverse=reverse, merge=merge)
    except RuntimeError:
        raise RuntimeError("cylinder: the extrusion failed (zero height? on a face, "
                           "does the circle fall inside it?).") from None


def delete_feature(name: str) -> str:
    """Delete ONE feature by its tree name (Cut-Extrude1, Fillet2), taking its absorbed
    sketch with it. Returns the name.
    It is the way back after a wrong operation: a fillet, chamfer or cut creates no BODY
    (part.delete_body cannot undo it), and retrying on a part that still carries the wrong
    feature fails forever -- measured 2026-08-17: a chamfer rejected correctly cost 41
    calls answering "the volume did not go down", because it was still there."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    if not ext.SelectByID2(str(name), "BODYFEATURE", 0, 0, 0, False, 0, None, 0):
        raise ValueError(
            f"delete_feature: there is no feature '{name}' in the tree. Use the EXACT name "
            f"SolidWorks gave it (Cut-Extrude1, Fillet2), which inspect.list_features "
            f"shows -- not a description of what it does.")
    # swDelete_Absorbed(1) takes the sketch along; EditDelete would leave it orphaned
    ok = ext.DeleteSelection2(1)
    md.ClearSelection2(True)
    if not ok:
        raise RuntimeError(f"delete_feature: SolidWorks refused to delete '{name}' (does "
                           f"another feature depend on it? delete from the NEWEST to the "
                           f"oldest).")
    return str(name)


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


def set_tolerance(dim_name: str,
                  kind: Literal["bilateral", "symmetric", "fit", "basic"] = "bilateral", *, upper_mm: float = 0.0,
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
# The name SolidWorks accepts != the name a person (or a model) writes, and an unknown
# name is SILENTLY IGNORED by SetMaterialPropertyName2: no error, and the part keeps the
# default 1000 kg/m3. Measured: "Aluminum 1060" left the mass 2.7x too low with no warning.
# Common spellings map to the database name; anything else passes through unchanged.
MATERIALS = {
    "aluminum 1060": "1060 Alloy", "aluminium 1060": "1060 Alloy",
    "aluminio 1060": "1060 Alloy", "al 1060": "1060 Alloy",
    "aluminum 6061": "6061 Alloy", "aluminio 6061": "6061 Alloy", "al 6061": "6061 Alloy",
    "aluminum 7075": "7075 Alloy", "aluminio 7075": "7075 Alloy",
    "aluminio": "1060 Alloy", "aluminum": "1060 Alloy", "aluminium": "1060 Alloy",
    "aco 1020": "AISI 1020", "steel 1020": "AISI 1020",
    "aco 1045": "AISI 1045", "steel 1045": "AISI 1045",
    "aco inox": "AISI 304", "inox": "AISI 304", "stainless": "AISI 304",
    "aco inox 304": "AISI 304", "aco inox 316": "AISI 316",
    "aco carbono": "Plain Carbon Steel", "carbon steel": "Plain Carbon Steel",
    "aco": "Plain Carbon Steel", "steel": "Plain Carbon Steel",
    "aco liga": "Alloy Steel", "alloy steel": "Alloy Steel",
    "latao": "Brass", "brass": "Brass", "cobre": "Copper", "copper": "Copper",
    "bronze": "Bronze", "titanio": "Titanium", "titanium": "Titanium",
    "abs": "ABS", "nylon": "Nylon 6/10", "pvc": "PVC Rigid",
    "acrilico": "Acrylic (Medium-high impact)", "acrylic": "Acrylic (Medium-high impact)",
}


def set_material(name: str, database: str = "SOLIDWORKS Materials", config: str = "") -> str:
    """Set the part's material (e.g. '6061 Alloy', 'AISI 1020', 'Aluminum 7075-T6').
    Affects density/E/nu (FEA reads them from here). Common spellings ('aluminum 6061',
    'stainless', 'aco 1020') are mapped to the database name. Returns the name READ BACK
    from the part -- SolidWorks silently ignores a name it does not know, so this raises
    instead of reporting a material that was never applied."""
    sent = MATERIALS.get(str(name).strip().lower(), str(name).strip())
    pd = C.cast(C.active("IPartDoc"), "IPartDoc")
    pd.SetMaterialPropertyName2(config, database, sent)
    applied = get_material(config)[0]
    if not applied or str(applied).strip() != sent:
        raise RuntimeError(
            f"set_material: '{name}' was NOT applied (SolidWorks has '{applied or 'none'}'). "
            f"The name has to exist in the '{database}' database exactly, e.g. "
            f"'1060 Alloy', '6061 Alloy', 'AISI 1020', 'AISI 304', 'Plain Carbon Steel'.")
    return str(applied)


set_material.schema = {"suggest": {"name": sorted(set(MATERIALS.values()))}}


def get_material(config: str = ""):
    """Return (material_name, database) of the active part."""
    pd = C.cast(C.active("IPartDoc"), "IPartDoc")
    res = pd.GetMaterialPropertyName2(config)  # Database is a byref out -> comes in the return
    if isinstance(res, (tuple, list)):
        return (res[0], res[1] if len(res) > 1 else "")
    return (res, "")


# ── body_op: scale / combine / move / split (BODY operations, multibody) ──────
def scale(factor: float, *, about: Literal["centroid", "origin"] = "centroid") -> str:
    """UNIFORMLY scale the part by `factor` (about: 'centroid'|'origin'). Volume x
    factor^3. Returns the feature name."""
    md = C.active("IModelDoc2")
    about_i = {"centroid": 0, "origin": 1}.get(about, 0)
    md.InsertScale(factor, factor, factor, True, about_i)
    return _last_feature(md).Name


def combine(operation: Literal["add", "subtract", "common"] = "add", *, main_body=None, tool_bodies=None) -> str:
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
    if not isinstance(bodies, (list, tuple)):
        bodies = [bodies]          # one body given loose is a list of one
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
def shell(thickness_mm: float, remove_faces, *, outward: bool = False, face_point=None,
          body=None) -> str:
    """SHELL: hollow the solid into a thin-walled box of wall `thickness_mm`, leaving OPEN
    the `remove_faces` -- face NAMES (top, bottom, front, back, left, right) or face
    objects, one or a list. `face_point` picks between two faces with the same name.
    Returns the feature."""
    if isinstance(remove_faces, (str,)) or not isinstance(remove_faces, (list, tuple)):
        # one face given loose ("top" or a single object) is a list of one -- iterating
        # the string would select five characters (the `delete_body` defect, 2026-08)
        remove_faces = [remove_faces]
    faces = [(_face_by_name("shell", f, face_point, body)[0]
              if isinstance(f, str) else f) for f in remove_faces]
    md = C.active("IModelDoc2")
    before = {f["name"] for f in I.list_features()}
    I.select(faces)
    md.InsertFeatureShell(C.mm(thickness_mm), bool(outward))
    md.ClearSelection2(True)
    feat = _last_feature(md)
    # InsertFeatureShell returns nothing: whether a feature was born is read in the tree
    if feat is None or feat.Name in before:
        raise RuntimeError("shell failed: SolidWorks created no feature (is the wall "
                           "thicker than the part allows? does each face to open exist?).")
    return feat.Name


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
    # BOTH are CHECKED before InsertMultiFaceDraft: with no neutral selected SolidWorks
    # has no draft direction and opens the MODAL "The draft direction is not defined",
    # which blocks the whole COM until somebody clicks it (measured 2026-08-19: a suite
    # sat 11 minutes). A verb may fail; no verb may open a modal.
    if isinstance(neutral, str):
        ok = C.cast(md.Extension, "IModelDocExtension").SelectByID2(
            C.resolve_plane(md, neutral), "PLANE", 0, 0, 0, False, 1, None, 0)
        if not ok:
            raise RuntimeError(
                f"draft: the neutral plane '{neutral}' was not selected. The neutral is "
                f"where the taper is MEASURED from: a plane from the tree (Front/Top/Right "
                f"or its real name) or a planar FACE of the part.")
    elif not I.select_marked(neutral, 1):
        raise RuntimeError("draft: the neutral that was passed selected nothing -- it has "
                           "to be a planar FACE (inspect.faces) or a plane name.")
    if isinstance(faces, (str,)) or not isinstance(faces, (list, tuple)):
        faces = [faces]
    n = sum(1 for f in faces if I.select_marked(f, 2, append=True))
    if not n:
        md.ClearSelection2(True)
        raise RuntimeError("draft: none of the faces to taper was selected. `faces` is a "
                           "list of face objects (inspect.faces), and it cannot include "
                           "the neutral face.")
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


def _circles_on_face(verb: str, positions, diameter_mm: float, face, face_point, body):
    """Sketch circles on the TOP face in its own FACE coordinates (face=None, the
    original contract), or on a NAMED face with each position as the GLOBAL pair of that
    face's plane (the part.hole convention). The face is found AGAIN on every call: a cut
    changes it, and a stale IFace2 selects nothing."""
    if face is None:
        S.begin_on_face(_top_face())
        for (u, v) in positions:
            S.circle(u, v, diameter_mm / 2.0)
    else:
        su, sv = _open_sketch(verb, "Front", face, face_point, body)
        for i, p in enumerate(positions):
            a, b = _pair(verb, "positions", i, p)
            S.circle(su * a, sv * b, diameter_mm / 2.0)
    return S.end()


def counterbore(positions, d_hole_mm: float, d_bore_mm: float, bore_depth_mm: float, *,
                face=None, face_point=None, body=None) -> str:
    """COUNTERBORED hole: through hole d_hole + coaxial counterbore d_bore of depth
    bore_depth, both cut from the SAME face (so they are coaxial). Without `face`: the
    top face, positions=[(u,v),...] in that face's own coordinates. With face='<name>'
    (top|bottom|front|back|left|right): that face, positions as the GLOBAL pair of its
    plane, as in part.hole. Robust, with no standards database."""
    _circles_on_face("counterbore", positions, d_hole_mm, face, face_point, body)
    extrude(0, "cut", through_all=True)
    _circles_on_face("counterbore", positions, d_bore_mm, face, face_point, body)
    return extrude(bore_depth_mm, "cut")


def countersink(positions, d_hole_mm: float, d_sink_mm: float, angle_deg: float = 90.0, *,
                face=None, face_point=None, body=None) -> str:
    """COUNTERSUNK hole: through hole d_hole + cone d_sink (a cut with a draft angle =
    countersink). Without `face`: the top face, positions in that face's own coordinates.
    With face='<name>': that face, positions as the GLOBAL pair of its plane, as in
    part.hole. Returns the feature."""
    _circles_on_face("countersink", positions, d_hole_mm, face, face_point, body)
    extrude(0, "cut", through_all=True)
    # cone: depth = sink_radius / tan(half the angle)
    import math
    half = math.radians(angle_deg / 2.0)
    depth = (d_sink_mm / 2.0) / math.tan(half) if half > 0 else d_sink_mm
    _circles_on_face("countersink", positions, d_sink_mm, face, face_point, body)
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
# Edge kinds, with the synonyms models actually write (a closed domain: an enum).
_EDGE_KINDS = {"curved": "curved", "curvas": "curved", "circular": "curved",
               "circulares": "curved", "straight": "straight", "retas": "straight",
               "all": "all"}
EdgeKind = Literal[tuple(sorted(_EDGE_KINDS))] | None


def _pick_edges(verb: str, edges, face, only, body) -> str:
    """Select the edges a fillet/chamfer works on; returns how they were chosen.

    Three ways, in this order: edges='all' (every edge of the body); `only`/`face` (edges
    by DESCRIPTION -- 'curved' hole and radius rims, 'straight' sharp corners, optionally
    only those of one face); a list of IEdge. With none of them, the current selection."""
    if isinstance(edges, str):
        if edges.strip().lower() != "all":
            raise ValueError(f"{verb}: edges='{edges}' -- as text only 'all' is accepted "
                             f"(every edge); otherwise pass edge objects, or describe "
                             f"them with only='curved'|'straight' and face='<name>'.")
        n = I.select(I.edges(body))
        if not n:
            raise RuntimeError(f"{verb}: no edge selected (does the part have a body?).")
        return "all"
    if only is not None or face is not None:
        kind = _EDGE_KINDS.get(str(only or "all").strip().lower())
        if kind is None:
            raise ValueError(f"{verb}: only='{only}' -- use curved (hole and radius rims), "
                             f"straight (sharp corners) or all.")
        axis = want_max = None
        if face is not None:
            key = str(face).strip().lower()
            if key not in _FACES:
                raise ValueError(f"{verb}: face '{face}' is not a face name "
                                 f"({', '.join(FACE_NAMES)}); they follow the outward "
                                 f"normal: top = +Y, front = +Z, right = +X.")
            axis, want_max = _FACES[key][0], _FACES[key][1]
        chosen = I._edges_where(kind, axis, bool(want_max), body)
        if not chosen:
            raise RuntimeError(
                f"{verb}: no edge matches only='{kind}'"
                + (f" on face '{face}'" if face is not None else "")
                + ". Call inspect.edges with info=true to see which edges the part has: "
                  "'curved' only exists where there is a hole or a radius, 'straight' "
                  "only on a sharp corner. To take EVERY edge use edges='all'.")
        I.select(chosen)
        return "described"
    if edges is not None:
        I.select(edges)
        return "list"
    return "selection"


def fillet(radius_mm: float, edges=None, *, face=None, only: EdgeKind = None,
           body=None) -> str:
    """Round edges (constant radius). Returns the feature name. Which edges:
      edges='all'                    -> every edge of the body;
      only='curved'|'straight'|'all' -> by DESCRIPTION ('curved' = hole and radius rims,
                                        'straight' = sharp corners), and face='<name>'
                                        keeps only the edges of that face;
      edges=[IEdge, ...]             -> exactly those (e.g. from inspect.edges);
      nothing                        -> the current selection."""
    md = C.active("IModelDoc2")
    how = _pick_edges("fillet", edges, face, only, body)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    feat = fm.FeatureFillet3(FILLET_PROPAGATE | FILLET_UNIFORM, C.mm(radius_mm), 0, 0,
                             FILLET_SIMPLE, 0, 0,
                             None, None, None, None, None, None, None)
    md.ClearSelection2(True)
    if feat is None:
        # The message does NOT assert the cause: a version that said only "the radius is
        # too large, try half" sent a model halving 1 -> 0.007 mm across 41 calls, when
        # the fillet had ALREADY been applied and SolidWorks refused the second one.
        if how == "all":
            raise RuntimeError(
                f"fillet: SolidWorks refused a radius of {radius_mm} mm on ALL the edges "
                f"at once -- usually ONE edge cannot take it (typically the rim of a hole "
                f"already rounded or chamfered). IN THIS ORDER: (1) KEEP the radius and "
                f"reduce the SET: only='straight' takes the sharp corners, only='curved' "
                f"the hole rims, face= limits it to one side; (2) inspect.edges with "
                f"info=true shows each edge's length_mm -- round only the ones that fit; "
                f"(3) only if the request really is the WHOLE part, a smaller radius, and "
                f"then SAY the radius that came out. Do NOT repeat the same call.")
        raise RuntimeError(
            f"fillet: SolidWorks refused a radius of {radius_mm} mm on those edges. The two "
            f"common causes, IN THIS ORDER: (1) the fillet HAS already been applied -- "
            f"check inspect.list_features, the step may be DONE; (2) the radius does not "
            f"fit (it cannot exceed half the smallest neighbouring face) -- then try half, "
            f"ONCE. The same error with another value means it is not the value.")
    return feat.Name


def chamfer(dist_mm: float, angle_deg: float = 45.0, edges=None, *, face=None,
            only: EdgeKind = None, body=None) -> str:
    """Chamfer edges (distance + angle). Returns the feature name. Which edges: the same
    four ways as part.fillet -- edges='all', only='curved'|'straight'|'all' with an
    optional face='<name>', a list of IEdge, or the current selection."""
    md = C.active("IModelDoc2")
    how = _pick_edges("chamfer", edges, face, only, body)
    fm = C.cast(md.FeatureManager, "IFeatureManager")
    # 8 arguments, FLAGS and TYPE in separate positions, distances as FLOATs -- ORing the
    # two flags into one argument, or passing int 0, gives 'Type mismatch' in COM
    feat = fm.InsertFeatureChamfer(CHAMFER_TANGENT_PROP, CHAMFER_ANGLE_DIST,
                                   C.mm(dist_mm), C.deg(angle_deg), 0.0, 0.0, 0.0, 0.0)
    md.ClearSelection2(True)
    if feat is None:
        if how == "all":
            raise RuntimeError(
                f"chamfer: SolidWorks refused a distance of {dist_mm} mm on ALL the edges "
                f"at once -- normally it does not fit on ONE edge. IN THIS ORDER: (1) KEEP "
                f"the distance and reduce the SET (only='straight'|'curved', face=); (2) "
                f"inspect.edges with info=true, and chamfer only the edges that fit; (3) "
                f"only if the request really is the WHOLE part, a smaller distance, and "
                f"then SAY the distance that came out. Do NOT repeat the same call.")
        raise RuntimeError(
            f"chamfer: SolidWorks refused a distance of {dist_mm} mm on those edges. The "
            f"two common causes, IN THIS ORDER: (1) the chamfer HAS already been applied "
            f"-- check inspect.list_features; (2) the distance does not fit -- then try "
            f"half, ONCE. The same error with another value means it is not the value.")
    return feat.Name


# `edges` is a list of handles OR the word 'all': the name heuristic alone would make it
# array-only, and a schema-validating surface would then refuse edges='all' (measured
# 2026-09-27 with the agent's goldens: 5 cases refused before reaching SolidWorks).
_EDGES_OR_ALL = {"type": ["array", "string"], "items": {"type": "string"},
                 "description": "'all' (every edge of the body) or a list of edge "
                                "handles ('@h3')"}
fillet.schema = {"params": {"edges": _EDGES_OR_ALL}}
chamfer.schema = {"params": {"edges": _EDGES_OR_ALL}}


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


def axis_from_face(face=None, name: str = "", *, radius_mm: float = None,
                   body=None) -> str:
    """Create a reference axis from a CYLINDRICAL face (the cylinder's axis) -- a robust
    NAMED reference for mates (datum->mate). `face` is OPTIONAL: without it the part's
    cylindrical face is found here (`radius_mm` picks among several). Create the axis
    BEFORE the holes: afterwards there are several cylinders and the automatic choice
    may land on a hole. Returns the name."""
    md = C.active("IModelDoc2")
    if face is None:
        face = I.find_face("cylinder", body=body, radius_mm=radius_mm)
        if face is None:
            raise RuntimeError(
                "axis_from_face: the part has no cylindrical face"
                + (f" of radius {radius_mm} mm" if radius_mm else "")
                + ". An axis is only born from a cylindrical face (a cylinder, a hole, a "
                  "round boss); on a prismatic part use part.reference_axis (the "
                  "intersection of two planes).")
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
# an optimization loop should keep driving dimensions through the parameter layer
# (set_dimensions) -- a single source of truth, with no hidden state in the file;
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


def reference_point(entities, kind: Literal["face_center", "center_edge", "intersection",
                                           "sketch_point"] = "face_center",
                    name: str = "") -> str:
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
                   *, reverse: bool = False, body=None) -> str:
    """LINEAR pattern of a feature along a direction: `direction_edge` is an AXIS NAME
    ('x', 'y', 'z' -- a straight edge parallel to it is found here) or an IEdge.
    The edge found may point to the NEGATIVE side of the axis; then the copies are born
    outside the material and SolidWorks creates a pattern with no instances -- that is
    what reverse=True fixes. Returns the feature."""
    if isinstance(direction_edge, str):
        axis = {"x": 0, "y": 1, "z": 2}.get(direction_edge.strip().lower())
        if axis is None:
            raise ValueError(f"pattern_linear: direction_edge='{direction_edge}' -- as text "
                             f"it is an axis name: x, y or z.")
        direction_edge = I.find_edge(axis, body)
        if direction_edge is None:
            raise RuntimeError(
                f"pattern_linear: the part has no STRAIGHT edge parallel to axis "
                f"{'XYZ'[axis]}. Pick another axis, or pass an edge from inspect.edges.")
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


pattern_linear.schema = {"suggest": {"direction_edge": ["x", "y", "z"]},
                         "params": {"direction_edge": {"type": "string"}}}


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
