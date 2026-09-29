# -*- coding: utf-8 -*-
"""
sw_sketch -- SKETCH sub-domain (part.sketch.*): create entities (returning the segment
so calls can be chained) + geometric RELATIONS (tangent/parallel/concentric/...).

A sketch driven by coordinates alone is fragile; robust CAD uses RELATIONS + dimensions.
Flow:
  begin(plane) -> line/circle/centerline/point/rect (they keep refs) -> relation(...) ->
  dimension(...) -> end() -> sketch name (for extrude/revolve).

Relations go through ISketchRelationManager.AddRelation(entities, swConstraintType_e) --
pass the segment/point objects directly (no dependence on the selection).
"""
from . import sw_com as C

# swConstraintType_e
RELATIONS = {
    "horizontal": 4, "vertical": 5, "tangent": 6, "parallel": 7,
    "perpendicular": 8, "coincident": 9, "concentric": 10, "symmetric": 11,
    "colinear": 27, "fixed": 17,
}


def _sm():
    return C.cast(C.active("IModelDoc2").SketchManager, "ISketchManager")


# ── sketch lifecycle ──────────────────────────────────────────────────────────
def begin(plane: str):
    """Open a sketch on the given plane ('Front'/'Top'/'Right' or the real name)."""
    md = C.active("IModelDoc2")
    C.select_plane(md, plane)
    _sm().InsertSketch(True)


def begin_on_face(face):
    """Open a sketch on a FACE (IFace2, e.g. sw_inspect.find_face(...)). Lets features
    go on any face, not just the default planes. The sketch coordinates live in the
    face's LOCAL system."""
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    C.cast(face, "IEntity").Select4(False, None)
    _sm().InsertSketch(True)


def end() -> str:
    """Close the active sketch and return the name of the sketch feature.

    MEASURED GOTCHA (2026-08-29, sheet metal): `FeatureByPositionReverse(0)` is NOT the
    just-closed sketch in a SHEET METAL part -- it is always `Flat-Pattern1`, which sits
    rolled at the end of the tree (the same invariant that breaks `extrude`'s default
    `sketch_name`). Here the answer only comes from that path when it IS a sketch; if it
    is not, walk the tree and return the LAST ProfileFeature.
    """
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    _sm().InsertSketch(True)
    feat = C.cast(md.FeatureByPositionReverse(0), "IFeature")
    if feat.GetTypeName2() == "ProfileFeature":
        return feat.Name
    name, raw = "", md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() == "ProfileFeature":
            name = f.Name
        raw = f.GetNextFeature()
    return name


# ── entities (return the segment/point so relations can chain off them) ───────
# Every entity but the tangent arc is created through `C.sketch_direct` (no sketch
# inference): with inference on, a short segment could be swallowed depending on the
# window's zoom -- see sw_com.sketch_direct.
def _made(obj, what, iface):
    if obj is None:
        raise RuntimeError(f"{what}: SolidWorks created nothing (is a sketch open? is the "
                           f"entity degenerate -- zero length or radius?)")
    return C.cast(obj, iface)


def line(x1, y1, x2, y2):
    """Line in the active sketch, from (x1,y1) to (x2,y2) in mm. Returns the segment."""
    with C.sketch_direct(_sm()) as sm:
        seg = sm.CreateLine(C.mm(x1), C.mm(y1), 0, C.mm(x2), C.mm(y2), 0)
    return _made(seg, f"line ({x1},{y1})-({x2},{y2})", "ISketchSegment")


def centerline(x1, y1, x2, y2):
    """Construction line (revolve axis / symmetry)."""
    with C.sketch_direct(_sm()) as sm:
        seg = sm.CreateCenterLine(C.mm(x1), C.mm(y1), 0, C.mm(x2), C.mm(y2), 0)
    return _made(seg, f"centerline ({x1},{y1})-({x2},{y2})", "ISketchSegment")


def circle(cx, cy, r):
    """Circle in the active sketch: center (cx,cy) and radius r, in mm."""
    with C.sketch_direct(_sm()) as sm:
        seg = sm.CreateCircleByRadius(C.mm(cx), C.mm(cy), 0, C.mm(r))
    return _made(seg, f"circle r={r} at ({cx},{cy})", "ISketchSegment")


def arc_tangent(x1, y1, x2, y2, arc_type=0):
    """Tangent arc from point (x1,y1) to (x2,y2). Its tangency is found by sketch
    INFERENCE on the segment that ends at (x1,y1), so -- unlike the other entities --
    it is created with inference on."""
    return _made(_sm().CreateTangentArc(C.mm(x1), C.mm(y1), 0, C.mm(x2), C.mm(y2), 0,
                                        arc_type), "arc_tangent", "ISketchSegment")


def point(x, y):
    """Point in the active sketch, in mm (anchor for relations and dimensions)."""
    with C.sketch_direct(_sm()) as sm:
        pt = sm.CreatePoint(C.mm(x), C.mm(y), 0)
    return _made(pt, f"point ({x},{y})", "ISketchPoint")


def polygon(cx, cy, r, sides=6, inscribed=True):
    """Regular polygon with `sides` sides, center (cx,cy), radius `r` (mm). Returns the
    list of segments (sides + construction circle)."""
    with C.sketch_direct(_sm()) as sm:
        segs = sm.CreatePolygon(C.mm(cx), C.mm(cy), 0, C.mm(cx + r), C.mm(cy), 0,
                                int(sides), bool(inscribed))
    return [C.cast(s, "ISketchSegment") for s in (segs or [])]


def slot(x1, y1, x2, y2, width):
    """Straight slot from (x1,y1) to (x2,y2), width `width` (mm). Returns ISketchSlot."""
    with C.sketch_direct(_sm()) as sm:
        sl = sm.CreateSketchSlot(0, 0, C.mm(width), C.mm(x1), C.mm(y1), 0, C.mm(x2),
                                 C.mm(y2), 0, 0, 0, 0, 0, False)
    return _made(sl, "slot", "ISketchSlot")


def spline(points):
    """Spline through points [(x,y),...] (mm). Returns ISketchSegment."""
    data = []
    for (x, y) in points:
        data += [C.mm(x), C.mm(y), 0.0]
    arr = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8, data)
    with C.sketch_direct(_sm()) as sm:
        seg = sm.CreateSpline(arr)
    return _made(seg, "spline", "ISketchSegment")


def rect(x1, y1, x2, y2):
    """Rectangle by corners. Returns the list of 4 segments (the sides)."""
    with C.sketch_direct(_sm()) as sm:
        segs = sm.CreateCornerRectangle(C.mm(x1), C.mm(y1), 0, C.mm(x2), C.mm(y2), 0)
    return [C.cast(s, "ISketchSegment") for s in (segs or [])]


# ── parametric dimensions (they drive the sketch; marked for drawing import) ──
def dimension(entities, at, *, orient: str = "aligned", name: str = "") -> str:
    """Add a parametric DIMENSION to the ACTIVE sketch (call it BETWEEN begin() and end()).
    `entities` = 1+ segments/points returned by line/circle/point/... (or just one, no list):
      1 straight segment -> length;
      1 circle/arc       -> diameter;
      2 points/segments  -> distance between them.
    `at`=(x,y) mm where to PLACE the dimension (outside the body). orient:
    'aligned'|'horizontal'|'vertical' (horizontal/vertical project the distance onto the
    axis; aligned = the real distance).
    `name` (optional) renames the dimension to a STABLE name that the loop drives via
    set_dimension. Marks the dimension for the drawing to import (import_model_dims).
    Returns the dimension's NAME ('D1@SketchN' or the renamed one)."""
    if not isinstance(entities, (list, tuple)):
        entities = [entities]
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    for i, e in enumerate(entities):
        # Select4 on the CONCRETE INTERFACE (the ISketchSegment/ISketchPoint that
        # line/circle/point already return) -- re-casting to IEntity gives a 'Type
        # mismatch' on the Data argument of this signature.
        e.Select4(i > 0, None)
    placer = {"aligned": md.AddDimension2, "horizontal": md.AddHorizontalDimension2,
              "vertical": md.AddVerticalDimension2}.get(orient)
    if placer is None:
        md.ClearSelection2(True)
        raise ValueError(f"orient '{orient}' is invalid (aligned|horizontal|vertical).")
    disp = placer(C.mm(at[0]), C.mm(at[1]), 0.0)
    md.ClearSelection2(True)
    if disp is None:
        raise RuntimeError(
            "dimension: no dimension created (entities not selected / sketch closed?).")
    dim = C.cast(C.cast(disp, "IDisplayDimension").GetDimension2(0), "IDimension")
    if name:
        # rename the 'D1' part of FullName ('D1@Sketch1' -> 'name@Sketch1'). Best-effort:
        # if the typelib will not let Name be set, keep the default name (does not break
        # the verb).
        try:
            dim.Name = name
        except Exception:  # noqa: BLE001  (pywin32 com_error / AttributeError)
            pass
    return dim.FullName


# ── geometric relations ───────────────────────────────────────────────────────
def relation(entities, kind: str):
    """Add a geometric relation between `entities` (segments/points):
    kind in {tangent, parallel, perpendicular, coincident, concentric, symmetric,
    horizontal, vertical, colinear, fixed}. Returns the SketchRelation created."""
    if kind not in RELATIONS:
        raise ValueError(f"relation '{kind}' is invalid. Options: {sorted(RELATIONS)}")
    sk = C.cast(_sm().ActiveSketch, "ISketch")
    rm = C.cast(sk.RelationManager, "ISketchRelationManager")
    arr = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_DISPATCH,
                          [e._oleobj_ for e in entities])
    rel = rm.AddRelation(arr, RELATIONS[kind])
    if rel is None:
        raise RuntimeError(f"AddRelation({kind}) failed.")
    return rel
