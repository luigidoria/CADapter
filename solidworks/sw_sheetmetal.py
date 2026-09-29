# -*- coding: utf-8 -*-
"""
sw_sheetmetal -- layer 1: SHEET METAL verbs (sheet.*).

Same rule as the other domains: each verb consolidates a FAMILY of COM methods and
already resolves internally the gotchas that cost time. What the SW 2017 API does NOT
deliver through pywin32 is listed under "Escape hatch" at the end of this module -- that
is not an omission, it is measurement (the evidence is in `solidworks/tests/probes/probe_sheetmetal.py`,
which runs against the real SW).

Units: mm and degrees at the boundary, converted by sw_com.mm/deg.

FOUR rounds of live measurement: the 1st (2026-08-29) mapped the family; the 2nd knocked
two items out of the escape hatch (SKETCHED bend and MITER flange -- in both the old
verdict was about the PATH, not the feature) and opened up PARAMETRIC editing; the 3rd
(2026-08-30) CLOSED the front: it knocked out two more (LOFTED BEND, through the SELECTION
-- segments with mark=1 -- and the K-FACTOR, through the TARGET FEATURE -- the flange, not
the document), covered sheet metal in an ASSEMBLY, GAUGE selection from the table and
MULTI-BODY, and measured as uncooperative the open leads of jog, closed corner, corner
trim, gusset and perpendicular plane; the 4th (2026-08-30d) knocked out CONVERT TO SHEET
METAL and CLOSED CORNER, both from macros recorded in the UI.
See solidworks/docs/SHEET_METAL.md.

THE MAIN PATH (what almost every sheet metal request wants):

    from solidworks import sw_com as C, sw_sheetmetal as SM
    C.connect(); C.close_untitled()
    SM.new_sheet("Front", 100, 60, thickness_mm=2)   # the base flange
    SM.box_flanges(20)                               # becomes a box: one flange per side
    SM.flatten(True)                                 # flatten it
    SM.export_flat(r"C:\\...\\box.dxf")              # the cutting DXF

And what the 2nd round added:

    SM.sketched_bend(line=((60, -10, 2), (60, 70, 2)))   # bend with NO edge
    SM.miter_flange(SM.free_edges()[0], 20)              # flange with its own profile
    SM.set_bend_radius(8); SM.set_flange(angle_deg=45)   # parametric editing
    SM.unfold(); SM.cut(sk); SM.fold()                   # cut ACROSS the bend

And what the 3rd round added:

    SM.lofted_bend(sk_a, sk_b, 2.0)          # TRANSITION sheet between two profiles
    SM.set_gauge("Gauge 3")                   # pick the thickness FROM the gauge TABLE
    SM.set_k_factor(0.3)                      # K-factor (per FLANGE -- the global one won't budge)
    SM.sheet_bodies(); SM.cut_lists()         # multi-body: one record per body

And the 4th round (2026-08-30d), which converted a SOLID into sheet metal -- the path for
whoever already has the part modelled as an ordinary solid and wants the cutting DXF:

    SM.sharp_bend_edges()                    # the CONCAVE sharp corners (the bends)
    SM.convert_to_sheet(2.0)                 # L/U solid -> SHEET METAL part
    SM.open_corners(); SM.closed_corner()    # close the gap between two neighbouring flanges
"""
import os
import re as _re

from . import sw_com as C
from . import sw_inspect as I
from . import sw_parts as P

# ── constants (from the generated constants makepy module, not from the docs) ─
# swBendAllowanceTypes_e: the value SW already uses by default on a new sheet (measured by
# reading ICustomBendAllowance.Type on a freshly created part) is 2 = K-factor.
CBA_KFACTOR = 2
# swBendAllowanceBendTable: the allowance comes from a .btl file (round f, 2026-08-31)
CBA_BEND_TABLE = 1
BLIND = 0                                             # swEndConditions_e
# swSheetMetalReliefTypes_e
RELIEF = {"auto": 0, "rect": 1, "tear": 2, "obround": 3, "none": 4}
# swInsertEdgeFlangeXxx (bitmask of the edge flange's BooleanOptions)
EF_DEF_RADIUS, EF_DEF_RELIEF = 1, 128
# swFlangePositionType_e
POS = {"material_inside": 1, "material_outside": 2, "bend_outside": 3,
       "bend_centerline": 4}
# swFlangeDimType_e
SHARP_OUTER = 1
# swHemType_e / swHemPositionType_e
HEM = {"open": 0, "closed": 1, "teardrop": 2, "rolled": 3, "double": 4}
HEM_POS = {"inside": 0, "outside": 1}
# swCornerReliefType (break corner)
CORNER = {"fillet": 0, "chamfer": 1}
# InsertRefPlane with TWO references: the constraint codes and the MARKS the UI records
# for 'perpendicular to an edge, coincident with a vertex'. A DIFFERENT mark per reference
# is what pulls the method out of its no-op (invariant 37).
REFPLANE_EDGE, REFPLANE_VERTEX = 2, 4
REFPLANE_MARK_EDGE, REFPLANE_MARK_VERTEX = 0, 1
# swExportFlatPatternOption_e
FLAT_KEEP_BENDS, FLAT_REMOVE_BENDS = 0, 1


def _md():
    return C.active("IModelDoc2")


def _fm(md=None):
    return C.cast((md or _md()).FeatureManager, "IFeatureManager")


def _sm(md=None):
    return C.cast((md or _md()).SketchManager, "ISketchManager")


def _ext(md=None):
    return C.cast((md or _md()).Extension, "IModelDocExtension")


def _select_sketch(md, name):
    md.ClearSelection2(True)
    if not _ext(md).SelectByID2(name, "SKETCH", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError(f"could not select sketch '{name}'.")


# ── tree: finding features in a sheet metal part ─────────────────────────────
def _subfeatures(feat):
    out, sub = [], feat.GetFirstSubFeature()
    while sub is not None:
        sf = C.cast(sub, "IFeature")
        out.append(sf)
        sub = sf.GetNextSubFeature()
    return out


def _count_of_type(type_name) -> int:
    """How many features of a type the tree has. A verb whose COM method returns nothing
    (the closed corner is `Sub` even in the recorded VBA) needs this: `_feat_by_type` would
    find the feature from the PREVIOUS call and report a false success on the second."""
    n, raw = 0, _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        n += f.GetTypeName2() == type_name
        raw = f.GetNextFeature()
    return n


def _feat_by_type(type_name, nested=False):
    """First feature (or sub-feature, if `nested`) whose GetTypeName2 == type_name."""
    raw = _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() == type_name:
            return f
        if nested:
            for sf in _subfeatures(f):
                if sf.GetTypeName2() == type_name:
                    return sf
        raw = f.GetNextFeature()
    return None


def last_sketch() -> str:
    """Name of the LAST sketch in the tree.

    MEASURED GOTCHA: in a SHEET METAL part, `FeatureByPositionReverse(0)` does NOT return
    the just-created feature -- it always returns `Flat-Pattern1`, which sits rolled at the
    end of the tree. Any code that assumes "the last feature is the one I just created"
    picks up the flat pattern by mistake; that is the case of `sw_parts.extrude`'s default
    `sketch_name`. To cut a hole in a sheet, pass `sketch_name=SM.last_sketch()`.
    """
    name = ""
    raw = _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() == "ProfileFeature":
            name = f.Name
        raw = f.GetNextFeature()
    return name


def last_feature() -> str:
    """Name of the LAST construction feature in the tree, IGNORING the flat pattern.

    SAME gotcha as `last_sketch`, and this one catches whoever writes a verb: in a sheet
    metal part `FeatureByPositionReverse(0)` is always `Flat-Pattern1`. A verb that
    returned the "name of the just-created feature" through that path would lie on EVERY
    sheet metal part -- and the lie is silent, because the name does exist and the chaining
    appears to work.
    """
    name = ""
    raw = _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() != "FlatPattern":
            name = f.Name
        raw = f.GetNextFeature()
    return name


def is_sheet() -> bool:
    """Is the active part sheet metal?"""
    return _feat_by_type("SheetMetal") is not None


def bends():
    """Names of the BENDS (OneBend features) of the part -- the input to `unfold`/`fold`.

    MEASURED GOTCHA: the bend does NOT live at the top of the tree. `EdgeBend1` is a
    SUB-feature of `Edge-Flange1`, so a sweep that only walks GetNextFeature never finds it
    -- that is what made `unfold` create the `UnFold1` feature without unfolding anything.
    """
    names, raw = [], _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        names += [sf.Name for sf in _subfeatures(f) if sf.GetTypeName2() == "OneBend"]
        raw = f.GetNextFeature()
    return names


# ── selection by geometry (the "eyes" for sheet metal) ───────────────────────
def base_face():
    """The planar face with the LARGEST AREA -- in practice, the base flange's face.

    MEASURED GOTCHA: "the highest face" does NOT work for chaining flanges. After the first
    20mm flange, the face with the largest Z becomes the top OF THE FLANGE, and the next
    flange is born on top of the first (the box came out 42mm tall instead of 22).
    """
    planar = [f for f in I.faces() if C.cast(f.GetSurface(), "ISurface").IsPlane()]
    if not planar:
        raise RuntimeError("part has no planar face.")
    return max(planar, key=lambda f: f.GetArea())


def _edge_len(e2):
    p = e2.GetCurveParams2()
    return abs(p[7] - p[6])


def free_edges(face=None, min_mm=1.0):
    """Edges of the face that do NOT yet have a bend, longest to shortest.

    The criterion is geometric: an edge that already received a flange starts touching the
    bend's cylinder. That way flanges can be chained without counting indices -- the edge
    just used excludes itself from the next round.
    """
    face = face or base_face()
    free = []
    for e in (face.GetEdges() or []):
        e2 = C.cast(e, "IEdge")
        adj = [C.cast(x, "IFace2") for x in (e2.GetTwoAdjacentFaces2() or []) if x]
        if any(C.cast(o.GetSurface(), "ISurface").IsCylinder() for o in adj):
            continue
        if _edge_len(e2) * 1000 >= min_mm:
            free.append(e2)
    return sorted(free, key=_edge_len, reverse=True)


def bbox_mm(body=None):
    """Bounding box in mm: (dx, dy, dz). This is how you check, with no human eye, whether
    the part is bent (tall) or flattened (thin).

    With no argument, it is the box enclosing ALL solid bodies. Until 2026-08-30 it was
    `bodies()[0]`'s, and on a MULTI-BODY part that reported an arbitrary body -- the order
    of `GetBodies2` is not the creation order (measured: with two base flanges, [0] was the
    SECOND). Pass `body` to measure a specific body.
    """
    bodies_ = [body] if body is not None else I.bodies()
    if not bodies_:
        raise RuntimeError("part has no solid body.")
    boxes = [C.cast(b, "IBody2").GetBodyBox() for b in bodies_]
    lo = [min(c[i] for c in boxes) for i in range(3)]
    hi = [max(c[i + 3] for c in boxes) for i in range(3)]
    return tuple((hi[i] - lo[i]) * 1000 for i in range(3))


def sheet_bodies():
    """One record per solid BODY: {name, sheet, box_mm}. The "eyes" of multi-body.

    `IBody2.IsSheetMetal()` tells sheet metal bodies from ordinary ones on a mixed part --
    everything else in `sw_sheetmetal` assumes ONE body, and this verb is what lets the
    caller find out that is not the case before believing a number.
    """
    out = []
    for raw in I.bodies():
        b = C.cast(raw, "IBody2")
        c = b.GetBodyBox()
        out.append({"name": b.Name, "sheet": bool(b.IsSheetMetal()),
                    "box_mm": tuple(round((c[i + 3] - c[i]) * 1000, 4) for i in range(3))})
    return out


def _cut_list_folders():
    """Cut list folders, without repeats. They show up TWICE in the sweep (at the top of
    the tree and as a sub-feature of the parent folder) -- deduplicated by name."""
    found, seen, raw = [], set(), _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        for cand in (f, *_subfeatures(f)):
            if cand.GetTypeName2() == "CutListFolder" and cand.Name not in seen:
                seen.add(cand.Name)
                found.append(cand)
        raw = f.GetNextFeature()
    return found


_NUMBER = _re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _value(txt):
    """What the cut list delivers is ALWAYS text -- here it becomes a number when it is one.

    `CustomPropertyManager.Get2` returns a string for everything: "100", "2", "1". Whoever
    consumes fabrication data (buying the material rectangle, summing cut length, sending
    it to a spreadsheet) needs a number, and converting at the call site is the kind of
    thing you forget at one of the five places. So it lives HERE, in the funnel that both
    `cut_list` and `cut_lists` go through.

    It converts only what is an INTEGER or a pure decimal (the regex rejects "2 mm", "1e",
    "nan" and "inf"). Text stays text: material, description, folder name.
    CAVEAT: a TEXT property whose value looks like a number (a part code "12345") also
    becomes a number -- that is the price of there being no type on SW's side.
    """
    if not isinstance(txt, str):
        return txt
    t = txt.strip()
    return float(t) if _NUMBER.match(t) else txt


def _folder_props(folder) -> dict:
    cpm = folder.CustomPropertyManager
    out = {}
    for name in (cpm.GetNames() or []):
        r = cpm.Get2(name, "", "")
        out[name] = _value(r[1] if isinstance(r, tuple) and len(r) > 1 else r)
    return out


def cut_lists():
    """Cut list of EACH body: [{folder, bodies, **properties}]. `cut_list`'s multi-body
    path -- a sheet metal part with N bodies has N `Sheet<i>` folders, each with ITS OWN
    material rectangle."""
    md = _md()
    md.ForceRebuild3(False)
    folders = _cut_list_folders()
    if not folders:
        raise RuntimeError("part has no cut list (not sheet metal?)")
    out = []
    for f in folders:
        bf = C.cast(f.GetSpecificFeature2(), "IBodyFolder")
        names = [C.cast(x, "IBody2").Name for x in (bf.GetBodies() or [])]
        out.append({"folder": f.Name, "bodies": names, **_folder_props(f)})
    return out


# ── base flange ──────────────────────────────────────────────────────────────
def base_flange(thickness_mm: float, *, radius_mm: float = 3.0,
                sketch_name: str = "", reverse: bool = False,
                relief: str = "rect", relief_ratio: float = 0.5) -> str:
    """Turn a CLOSED SKETCH into the base flange (the part's first solid).

    Uses the sketch `sketch_name`, or the last one in the tree. `reverse` thickens toward
    the other side of the plane. Returns the feature name.

    MEASURED GOTCHA: `InsertSheetMetalBaseFlange2` (19 args, the NEWEST version -- the one
    intuition would tell you to use) is a SILENT NO-OP on SW 2017: it returns None and
    creates no body at all, in every argument combination tested (zero distance, no feature
    scope, relief by measure, PCBA as a VARIANT...). The 16-arg v1 works AND returns the
    Feature. See the `base` block of solidworks/tests/probes/probe_sheetmetal.py.
    """
    md = _md()
    _select_sketch(md, sketch_name or last_sketch() or md.FeatureByPositionReverse(0).Name)
    feat = _fm(md).InsertSheetMetalBaseFlange(
        C.mm(thickness_mm), bool(reverse), C.mm(radius_mm),
        C.mm(10), 0.0, False, BLIND, BLIND, 0, None,
        True, RELIEF.get(relief, 1), 0.0, 0.0, float(relief_ratio), True)
    md.ClearSelection2(True)
    if feat is None or not I.bodies():
        raise RuntimeError("base flange created no body (closed sketch? empty part?)")
    return C.cast(feat, "IFeature").Name


def new_sheet(plane: str, w_mm: float, h_mm: float, thickness_mm: float = 2.0,
              *, radius_mm: float = 3.0, reverse: bool = False) -> str:
    """Convenience: NEW part + a w x h rectangle on `plane` + base flange.
    Returns the base flange feature's name."""
    P.new_part()
    md = _md()
    C.select_plane(md, plane)
    sm = _sm(md)
    sm.InsertSketch(True)
    sm.CreateCornerRectangle(0.0, 0.0, 0.0, C.mm(w_mm), C.mm(h_mm), 0.0)
    md.ClearSelection2(True)
    sm.InsertSketch(True)
    return base_flange(thickness_mm, radius_mm=radius_mm, reverse=reverse,
                       sketch_name=md.FeatureByPositionReverse(0).Name)


# -- converting an ordinary solid into sheet metal -----------------------------
# 0.01 mm: it fits inside a sheet wall (2 mm in the typical case) and is large enough for
# GetClosestPointOn not to confuse it with numerical error.
_EPS_FACE = 1.0e-5


def _direction_into_face(face, normal, mid, tangent):
    """Unit direction that leaves the MIDDLE of the edge and ENTERS the face.

    The sign comes from a test, not from a convention: of the two sides perpendicular to
    the edge, the one that still BELONGS to the face (GetClosestPointOn returns the point
    itself). It deliberately does not use an "interior point of the face" -- on an L or U
    face the bounding box center falls OUTSIDE the face, and the snapped point ends up on
    the BORDER: that is what classified two CONVEX edges as concave on both measured
    goldens.
    """
    u = (tangent[1] * normal[2] - tangent[2] * normal[1],
         tangent[2] * normal[0] - tangent[0] * normal[2],
         tangent[0] * normal[1] - tangent[1] * normal[0])
    n = sum(x * x for x in u) ** 0.5
    if n < 1e-12:
        return None
    u = [x / n for x in u]
    for s in (1.0, -1.0):
        p = [mid[k] + s * _EPS_FACE * u[k] for k in range(3)]
        q = face.GetClosestPointOn(*p)
        if sum((q[k] - p[k]) ** 2 for k in range(3)) < 1e-18:
            return [s * x for x in u]
    return None


def sharp_bend_edges(min_mm: float = 1.0):
    """CONCAVE sharp-corner edges -- the bend candidates for `convert_to_sheet`.

    Concave = the material is on the INSIDE of the dihedral: the INNER corner of an L or U
    profile. The convex ones (the same profile's outer corner) do not work as bends.
    Returns [(edge, midpoint, [face_a, face_b])], longest to shortest.
    """
    out = []
    for e in I.edges():
        e2 = C.cast(e, "IEdge")
        adj = [C.cast(x, "IFace2") for x in (e2.GetTwoAdjacentFaces2() or []) if x]
        if len(adj) != 2:
            continue
        if not all(C.cast(f.GetSurface(), "ISurface").IsPlane() for f in adj):
            continue
        n1, n2 = adj[0].Normal, adj[1].Normal
        if abs(sum(n1[k] * n2[k] for k in range(3))) > 1e-6:
            continue                                    # 90-degree corners only
        cp = e2.GetCurveParams2()
        mid = tuple((cp[i] + cp[i + 3]) / 2.0 for i in range(3))
        t = [cp[i + 3] - cp[i] for i in range(3)]
        nt = sum(x * x for x in t) ** 0.5
        if nt * 1000 < min_mm:
            continue
        t = [x / nt for x in t]
        u2 = _direction_into_face(adj[1], n2, mid, t)
        if u2 is None:
            continue
        if sum(n1[k] * u2[k] for k in range(3)) > 1e-9:
            out.append((e2, mid, adj))
    return sorted(out, key=lambda r: _edge_len(r[0]), reverse=True)


def convert_to_sheet(thickness_mm: float = 2.0, *, bend_edges=None, fixed_face=None,
                     radius_mm: float = 3.0, gap_mm: float = 2.0,
                     relief: str = "auto", relief_ratio: float = 0.5,
                     keep_body: bool = False) -> str:
    """Convert an ordinary SOLID (an L profile, a U, a shelled box) into a SHEET METAL part.

    With no arguments it works it out on its own: the bend edges are the CONCAVE sharp
    corners (`sharp_bend_edges`) and the fixed face is the LARGER of the two faces adjacent
    to the first of them. Returns the name of the `SolidToSheetMetal` feature.

    MEASURED GOTCHA (2026-08-30, from a macro recorded in the UI) -- this item left the
    escape hatch with no new API. FOUR things have to be right at the same time, and the
    earlier rounds never crossed the first two:

      1. The METHOD is `InsertConvertToSheetMetal2` (v2, 10 args). The 7-arg v1 is a no-op
         when a 2nd entity is selected -- and it was v1 that ALL the variants with an edge
         used, while v2 had only ever been called with the fixed face ALONE. Hence the old
         verdict of "15 variants, does not work".
      2. The BEND EDGE has to be selected. With the fixed face alone the method runs, marks
         the part as sheet metal and DESTROYS everything but that face's wall
         (60x40x50 -> 58x2x50) -- that was the result invariant 15 described.
      3. The fixed face has to be the CONCAVE one (the inner one). Through the OUTER face
         nothing happens, with `ReverseThickDir` at any value -- and `base_face()` (the
         largest by AREA) returns precisely the outer one on an L profile.
      4. The edges go in through `IEntity.Select4` (the OBJECT). Through `SelectByID2` with
         a coordinate the SECOND edge is silently refused (returns False) and the U comes
         out with a single bend, losing a wall.

    Mark and ReliefType are INDIFFERENT (measured 0/1/2/4 and 0..4). `ReverseThickDir=True`
    grows inward and SHRINKS the part (60x40 -> 58x38), which is why it stays False. And
    the method returns `False` even when it builds: the verdict here is GEOMETRIC.
    """
    md = _md()
    if is_sheet():
        raise RuntimeError("the active part is ALREADY sheet metal.")
    if bend_edges is None:
        found = sharp_bend_edges()
        if not found:
            raise RuntimeError(
                "no CONCAVE sharp corner found -- pass bend_edges explicitly (a solid "
                "with no inner corner has nothing to bend).")
        edges_ = [r[0] for r in found]
        neighbours = found[0][2]
    else:
        edges_ = (list(bend_edges) if isinstance(bend_edges, (list, tuple))
                  else [bend_edges])
        neighbours = [C.cast(x, "IFace2")
                      for x in (C.cast(edges_[0], "IEdge").GetTwoAdjacentFaces2() or [])
                      if x]
    face = fixed_face or (max(neighbours, key=lambda f: f.GetArea()) if neighbours else None)
    if face is None:
        raise RuntimeError("could not determine the fixed face -- pass fixed_face.")
    box_before = [round(v, 1) for v in bbox_mm()]

    md.ClearSelection2(True)
    if not C.cast(face, "IEntity").Select4(False, None):
        raise RuntimeError("the fixed face cannot be selected.")
    for e in edges_:
        # Select4 with the OBJECT: by coordinate the 2nd edge is silently refused.
        if not C.cast(e, "IEntity").Select4(True, None):
            raise RuntimeError("one of the bend edges cannot be selected.")
    _fm(md).InsertConvertToSheetMetal2(
        C.mm(thickness_mm), False, False, C.mm(radius_mm), C.mm(gap_mm),
        RELIEF.get(relief, 0), float(relief_ratio), 0, 0.5, bool(keep_body))
    md.ClearSelection2(True)
    md.EditRebuild3()

    # The method returns False even when it builds -> the verdict HAS to be geometric.
    feat = _feat_by_type("SolidToSheetMetal")
    if feat is None or not is_sheet():
        raise RuntimeError(
            "convert created no sheet metal (is the fixed face the CONCAVE one? is there "
            "a sharp corner?).")
    box_after = [round(v, 1) for v in bbox_mm()]
    if box_after != box_before:
        raise RuntimeError(
            f"convert ran but CHANGED the geometry from {box_before} to {box_after} -- "
            "typically the fixed face was the OUTER one (only its wall survives) or a "
            "bend edge was missing.")
    return feat.Name


# ── flanges ──────────────────────────────────────────────────────────────────
def _xform(sketch_feat):
    s = C.cast(sketch_feat.GetSpecificFeature2(), "ISketch")
    return C.cast(s.ModelToSketchTransform, "IMathTransform").ArrayData


def _pt_to_sketch(m, p):
    x, y, z = p
    return (m[0] * x + m[3] * y + m[6] * z + m[9],
            m[1] * x + m[4] * y + m[7] * z + m[10],
            m[2] * x + m[5] * y + m[8] * z + m[11])


def _vec_to_sketch(m, v):
    x, y, z = v
    return (m[0] * x + m[3] * y + m[6] * z,
            m[1] * x + m[4] * y + m[7] * z,
            m[2] * x + m[5] * y + m[8] * z)


def edge_flange(edge, length_mm: float, *, angle_deg: float = 90.0,
                radius_mm: float = 3.0, margin_mm: float = 0.0,
                face=None, position: str = "material_inside") -> str:
    """Flange bent from an edge of the sheet. `edge` comes from `free_edges()`.

      length_mm  -> flange length (perpendicular to the sheet, up to the virtual corner)
      angle_deg  -> bend angle (90 = a straight flange)
      margin_mm  -> pulls the flange back at both ends (narrower than the edge)
      face       -> the face the edge came from; it is what defines WHICH WAY the flange
                    goes. Default: the largest-area face (the base flange).

    TWO MEASURED THINGS, and neither is in the COM method's signature:
      1. `InsertSketchForEdgeFlange` creates an EMPTY sketch. It is not the distance
         parameter that defines the flange -- it is the CLOSED RECTANGLE you draw in that
         sketch. Draw nothing and SW still hands back an `Edge-Flange1` Feature, with no
         error and no geometry: the flange has zero size.
      2. The rectangle has to sit on the side of the plane OPPOSITE the material. Drawn
         over the material SW refuses (returns None). The correct sign comes from the
         face's OUTWARD normal, projected onto the sketch's Y axis -- which is why `face`
         matters: to bend downward, pass the edge and the sheet's BOTTOM face.
    """
    md = _md()
    face = face or base_face()
    e2 = C.cast(edge, "IEdge")
    sk = md.InsertSketchForEdgeFlange(e2, C.deg(angle_deg), False)
    if sk is None:
        raise RuntimeError("InsertSketchForEdgeFlange returned None (invalid edge?)")
    skf = C.cast(sk, "IFeature")
    m = _xform(skf)
    p = e2.GetCurveParams2()
    a, b = _pt_to_sketch(m, p[0:3]), _pt_to_sketch(m, p[3:6])
    sign = 1.0 if _vec_to_sketch(m, face.Normal)[1] >= 0 else -1.0
    x0, x1 = sorted((a[0], b[0]))
    x0, x1 = x0 + C.mm(margin_mm), x1 - C.mm(margin_mm)
    if x1 <= x0:
        raise ValueError(f"margin_mm={margin_mm} does not fit on the edge.")
    _select_sketch(md, skf.Name)
    sm = _sm(md)
    sm.InsertSketch(True)
    sm.CreateCornerRectangle(x0, a[1], 0.0, x1, a[1] + sign * C.mm(length_mm), 0.0)
    md.ClearSelection2(True)
    sm.InsertSketch(True)
    feat = _fm(md).InsertSheetMetalEdgeFlange(
        e2, skf, EF_DEF_RADIUS | EF_DEF_RELIEF, C.deg(angle_deg), C.mm(radius_mm),
        POS.get(position, 1), 0.0, RELIEF["rect"], 0.5, 0.0, 0.0, SHARP_OUTER, None)
    if feat is None:
        raise RuntimeError(
            "edge flange refused by SW (is the profile on the material side? edge already "
            "bent?)")
    md.EditRebuild3()
    return C.cast(feat, "IFeature").Name


def box_flanges(length_mm: float, *, sides: int = 4, angle_deg: float = 90.0,
                radius_mm: float = 3.0, margin_mm: float = 0.0):
    """One flange per FREE edge of the base flange -- this is how a sheet becomes a BOX.

    The face and the edges are RE-FETCHED for each flange on purpose: a stored IEdge dies
    on the next rebuild, and the base face changes identity with each feature.
    Returns the list of names of the flanges created.
    """
    made = []
    for _ in range(sides):
        free = free_edges(base_face(), min_mm=10.0)
        if not free:
            break
        made.append(edge_flange(free[0], length_mm, angle_deg=angle_deg,
                                radius_mm=radius_mm, margin_mm=margin_mm))
    return made


# -- closed corner -------------------------------------------------------------
def open_corners(max_gap_mm: float = 0.0):
    """Corner CANDIDATES: pairs of SIDE faces of neighbouring flanges that face each other
    across a gap. This is what `closed_corner` closes.

    Returns [(face_a, face_b, gap_mm)], smallest gap first. `max_gap_mm` = 0 computes the
    limit as 5x the sheet thickness.

    MEASURED LIMIT: these are CANDIDATES, not "corners still open" -- closing a corner does
    NOT take it off this list, because the two side faces still exist after the `CornerFeat`
    (measured on a 4-flange box: 4 before and 4 after closing all four). That is why
    `closed_corner` judges by GEOMETRY, trying candidate after candidate.
    """
    thick = thickness()
    limit = max_gap_mm or (5.0 * thick)
    # A flange's SIDE face has a very distinct bounding box signature (measured on a 2 mm
    # sheet with a 20 flange): dimensions [0, 2.0, 17.0] -- the MIDDLE one is exactly the
    # THICKNESS and the largest is the flange's usable height. Filtering by area alone does
    # not work: the bend reliefs of the same corner give [0,5,5], [0,3,5] and [0,2,5], and
    # came in as false corners (measured: 3 candidate pairs where there is 1 corner).
    smalls = []
    for f in I.faces():
        if not C.cast(f.GetSurface(), "ISurface").IsPlane():
            continue
        area = f.GetArea() * 1e6
        if area >= 200.0:
            continue
        b = f.GetBox()
        dims = sorted((b[k + 3] - b[k]) * 1000.0 for k in range(3))
        if abs(dims[1] - thick) > 0.1 * thick or dims[2] < 3.0 * thick:
            continue
        smalls.append((f, _point_on_face(f), area))
    out = []
    for i in range(len(smalls)):
        for j in range(i + 1, len(smalls)):
            (fa, pa, aa), (fb, pb, ab) = smalls[i], smalls[j]
            # the two faces of the corner are nearly PERPENDICULAR to each other
            na, nb = fa.Normal, fb.Normal
            if abs(sum(na[k] * nb[k] for k in range(3))) > 0.2:
                continue
            d = sum((pa[k] - pb[k]) ** 2 for k in range(3)) ** 0.5 * 1000.0
            if d <= limit:
                out.append((fa, fb, d))
    return sorted(out, key=lambda t: t[2])


def closed_corner(face=None, *, corner: int = 0) -> str:
    """Close the corner between two neighbouring flanges (the gap left when two adjacent
    flanges come up). With no argument, closes the `corner`-th open corner.

    Returns the name of the `CornerFeat` feature.

    MEASURED GOTCHA (2026-08-30, from a macro recorded in the UI) -- this item left the
    escape hatch with ZERO new arguments, because `InsertSheetMetalClosedCorner` HAS no
    arguments: it is 100% selection. The 11 earlier variants got the face wrong, not the
    mark:

      - what works is ONE face, the **flange's side face** (the little ~34 mm2 face looking
        into the corner gap). The old attempts always selected TWO faces, and the two BIG
        ones (the flange walls) -- nothing happens that way, with any pair of marks.
      - the face has to be at a corner where TWO flanges meet. The side face at the other
        end of the same flange (where there is no neighbour) does nothing -- measured on
        the same part's four 34 mm2 side faces: only the two at the open corner respond.
      - marks 0 and 1 work; **2 and 4 do not**. Since the UI records mark 1, that is the
        one used.

    The method returns nothing (it is `Sub` even in the recorded VBA), so the verdict is
    GEOMETRIC: the tree gains a `CornerFeat` and the volume GROWS (material is closed in).
    """
    md = _md()
    if not is_sheet():
        raise RuntimeError("closed_corner only applies to a sheet metal part.")
    if face is not None:
        candidates = [face]
    else:
        corners = open_corners()
        if not corners:
            raise RuntimeError(
                "no OPEN corner found (does the part have two neighbouring flanges?).")
        # try one candidate at a time until the GEOMETRY changes: a corner that is already
        # closed still has its side faces, so `open_corners` lists it again (measured on a
        # 4-flange box -- closing the 1st does not take it off the list).
        candidates = [c[0] for c in corners[corner:]] + [c[1] for c in corners[corner:]]
    n0 = _count_of_type("CornerFeat")
    for target_face in candidates:
        v0 = I.mass()["volume_m3"]
        md.ClearSelection2(True)
        # mark 1 is what the UI records; measured: 0 also works, 2 and 4 do not
        if not I.select_marked(target_face, 1, append=False):
            continue
        md.InsertSheetMetalClosedCorner()
        md.ClearSelection2(True)
        md.EditRebuild3()
        if _count_of_type("CornerFeat") > n0 and I.mass()["volume_m3"] > v0:
            return _last_of_type("CornerFeat").Name
    raise RuntimeError(
        "closed corner closed nothing -- the face has to be the SIDE face of a flange at a "
        "corner where ANOTHER flange meets it (the flange's big face will not do), and the "
        "corner must not already be closed.")


def hem(edge, *, kind: str = "closed", length_mm: float = 5.0,
        radius_mm: float = 1.0, gap_mm: float = 0.0, angle_deg: float = 0.0,
        position: str = "inside", reverse: bool = False) -> str:
    """Edge hem on an edge: closes the sharp cut and stiffens the sheet.
    kind: 'closed'|'open'|'teardrop'|'rolled'|'double'. Returns the feature name."""
    md = _md()
    I.select([C.cast(edge, "IEdge")])
    feat = _fm(md).InsertSheetMetalHem2(
        HEM.get(kind, 1), HEM_POS.get(position, 0), bool(reverse),
        C.mm(length_mm), C.mm(gap_mm), C.deg(angle_deg), C.mm(radius_mm), 0.0,
        None, True, RELIEF["rect"], 1, True, 0.5, 0.0, 0.0)
    md.ClearSelection2(True)
    md.EditRebuild3()
    if not feat:
        raise RuntimeError("hem refused by SW (invalid edge?)")
    return last_feature()


def break_corner(entities, radius_mm: float, kind: str = "fillet") -> str:
    """Break corner (round or chamfer the corners) of the selected faces/edges.
    kind: 'fillet' (radius) | 'chamfer' (distance)."""
    md = _md()
    I.select(list(entities))
    md.InsertSheetMetalBreakCorner(CORNER.get(kind, 0), C.mm(radius_mm))
    md.ClearSelection2(True)
    md.EditRebuild3()
    return last_feature()


def _point_beside(face, p1, p2, fraction: float = 0.25):
    """A point INSIDE the face, offset to one side of the line p1->p2 (meters).

    The jog needs this: re-selecting the fixed face by a point that lands ON TOP of the
    bend line leaves SW unable to tell which half to hold (see the gotcha in `jog`).

    Adding a vector and requiring the point to land on the face is not enough: the line is
    given in MODEL coordinates and may sit on a plane PARALLEL to the face (the sheet has
    thickness -- the line comes in at z=0 and the face is at z=-2). So everything is
    projected with `GetClosestPointOn`, and of the two sides we keep the one that ends up
    FURTHEST from the line -- which is what guarantees an interior point of one half.
    """
    mid = face.GetClosestPointOn(*[(p1[k] + p2[k]) / 2.0 for k in range(3)])[:3]
    t = [p2[k] - p1[k] for k in range(3)]
    nt = sum(x * x for x in t) ** 0.5
    if nt < 1e-12:
        return _point_on_face(face)
    t = [x / nt for x in t]
    n = face.Normal
    u = (t[1] * n[2] - t[2] * n[1], t[2] * n[0] - t[0] * n[2],
         t[0] * n[1] - t[1] * n[0])
    nu = sum(x * x for x in u) ** 0.5
    if nu < 1e-12:
        return _point_on_face(face)
    u = [x / nu for x in u]
    box = face.GetBox()
    d = fraction * sum((box[k + 3] - box[k]) ** 2 for k in range(3)) ** 0.5
    best, best_d = None, -1.0
    for s in (1.0, -1.0):
        p = [mid[k] + s * d * u[k] for k in range(3)]
        q = face.GetClosestPointOn(*p)[:3]
        w = [q[k] - p1[k] for k in range(3)]
        proj = sum(w[k] * t[k] for k in range(3))
        dist = sum((w[k] - proj * t[k]) ** 2 for k in range(3)) ** 0.5
        if dist > best_d:
            best, best_d = (q[0], q[1], q[2]), dist
    return best if best_d > 1e-9 else _point_on_face(face)


def jog(line=None, *, face=None, fixed_at=None, sketch_name: str = "",
        segment: str = "Line1", offset_mm: float = 10.0, angle_deg: float = 90.0,
        radius_mm: float = 3.0, flip: bool = True,
        fix_projected_length: bool = True,
        dim_position: int = 1, bend_position: int = 0) -> str:
    """JOG (step): two bends in one go, offsetting half the sheet.

    It is the Z step -- the panel on one side of the line rises `offset_mm` and goes back
    to being parallel. `line` = ((x1,y1,z1),(x2,y2,z2)) in mm in the MODEL, crossing the
    face; if you have already drawn it, pass `sketch_name`. The side that stays PUT is the
    `face`'s side, and it HAS to be the face the sketch was created on.

    MEASURED GOTCHA (2026-08-31, from a macro recorded in the UI) -- this item left the
    escape hatch after 18 variants, and the cause was a SINGLE one:

      **`InsertSheetMetalJog` wants the angle in DEGREES, not radians.** Here the whole
      project's convention (angles through `C.deg`) is INVERTED. With `C.deg(90)` (=1.57)
      the method is a silent no-op; with `90` it builds. Isolated: with 90 degrees it works
      even with the `DimPos`/`BendPos` of the old variants, and with radians it does not
      work even with the ones the UI records. `DimPos`/`BendPos` change the RESULT (1/0
      gives the box z=12; 3/2 gives z=10), never the whether-it-works.

    The selection is the same as the UI's: the SEGMENT as EXTSKETCHSEGMENT (append=False,
    mark 0) and then the fixed face by COORDINATE (append=True). The return is None even
    when it builds -- the verdict is the bounding box growing.

    SECOND GOTCHA, measured while writing this verb: the point the fixed face is
    re-selected with must NOT land ON TOP of the bend line. The center of a 100x60 sheet
    with the line at x=50 lands exactly on it, and then SW cannot tell which side to hold:
    instead of the step it rotates half the sheet 90 degrees (box 12x60x100 instead of
    100x60x12), with no error at all. That is why the point is offset to the SIDE of the
    line; `fixed_at` (x,y,z in mm) dictates the side explicitly.
    """
    md = _md()
    if not is_sheet():
        raise RuntimeError("jog only applies to a sheet metal part.")
    face = face or base_face()
    fixed_pt = _point_on_face(face)
    if line is not None:
        (x1, y1, z1), (x2, y2, z2) = line
        fixed_pt = _point_beside(face, (C.mm(x1), C.mm(y1), C.mm(z1)),
                                 (C.mm(x2), C.mm(y2), C.mm(z2)))
        md.ClearSelection2(True)
        C.cast(face, "IEntity").Select4(False, None)
        sm = _sm(md)
        sm.InsertSketch(True)
        m = C.cast(C.cast(sm.ActiveSketch, "ISketch").ModelToSketchTransform,
                   "IMathTransform").ArrayData
        a = _pt_to_sketch(m, (C.mm(x1), C.mm(y1), C.mm(z1)))
        b = _pt_to_sketch(m, (C.mm(x2), C.mm(y2), C.mm(z2)))
        sm.CreateLine(a[0], a[1], 0.0, b[0], b[1], 0.0)
        md.ClearSelection2(True)
        sm.InsertSketch(True)
    if fixed_at is not None:
        fixed_pt = tuple(C.mm(v) for v in fixed_at)
    name = sketch_name or last_sketch()
    if not name:
        raise RuntimeError("no sketch for the jog: pass `line` or `sketch_name`.")

    box0 = bbox_mm()
    md.ClearSelection2(True)
    if not _ext(md).SelectByID2(f"{segment}@{name}", "EXTSKETCHSEGMENT",
                                0, 0, 0, False, 0, None, 0):
        raise RuntimeError(
            f"could not select segment '{segment}@{name}' (does the sketch have a line?).")
    if not _ext(md).SelectByID2("", "FACE", fixed_pt[0], fixed_pt[1], fixed_pt[2],
                                True, 0, None, 0):
        raise RuntimeError("could not select the jog's fixed face.")
    # ANGLE IN DEGREES -- see the gotcha above. Do NOT swap in C.deg().
    md.InsertSheetMetalJog(float(angle_deg), C.mm(radius_mm), C.mm(offset_mm),
                           bool(flip), bool(fix_projected_length),
                           int(dim_position), int(bend_position))
    md.ClearSelection2(True)
    md.EditRebuild3()
    box1 = bbox_mm()
    if max(abs(box1[k] - box0[k]) for k in range(3)) < 1e-6:
        raise RuntimeError(
            "the jog did not change the geometry -- is the fixed face the SAME one the "
            "sketch was created on? does the line cross the whole face?")
    return last_feature()


def corner_trim(edges=None, *, radius_mm: float = 5.0, kind: str = "chamfer",
                relief_type: int = 0, relief_mm: float = 5.0) -> str:
    """CORNER TRIM of the FLAT PATTERN: relief at the corners of the flat sheet.

    Requires the part FLATTENED (`flatten(True)`) -- it is the same button that, with the
    part folded, does `break_corner`. Without `edges`, it trims ALL the corners (selects
    every edge of the part). Returns the feature name.

    MEASURED GOTCHA (2026-08-31, from a macro recorded in the UI) -- 12 earlier variants
    returned None, and it was three errors added together:

      - the `InternalCornerFlag` (1st argument) has to be **0**; the old variants passed 1;
      - the selection needs SEVERAL accumulated edges. With a single edge, or with the 6
        boundary edges of ONE face, the method returns None. With all 18 edges of the part
        it trims the 6 corners at once (faces 8 -> 14);
      - the part has to be flattened BY THE VERB. The UI macro flattens with
        `SetBendState(2)`, which through COM returns 0 and does NOT flatten (invariant 3
        again) -- copying the macro literally leaves the part folded.
    """
    md = _md()
    if not is_sheet():
        raise RuntimeError("corner_trim only applies to a sheet metal part.")
    if not is_flat():
        raise RuntimeError(
            "corner_trim requires the part FLATTENED -- call flatten(True) first. "
            "(With the part folded the equivalent command is break_corner.)")
    targets = [C.cast(e, "IEdge") for e in (edges if edges is not None else I.edges())]
    nf0 = len(I.faces())
    md.ClearSelection2(True)
    if not I.select(targets):
        raise RuntimeError("no edge selected for the corner trim.")
    feat = _fm(md).InsertSheetMetalCornerTrim(
        0, CORNER.get(kind, 1), C.mm(radius_mm), int(relief_type), C.mm(relief_mm))
    md.ClearSelection2(True)
    md.EditRebuild3()
    if feat is None or len(I.faces()) == nf0:
        raise RuntimeError(
            "corner trim changed nothing (is the part flattened? is there a corner to "
            "trim?)")
    return last_feature()


def bend_faces(bend: int = 0):
    """The TWO planar faces on the CONCAVE side of a bend -- what the `gusset` rests on.

    They are the faces that form the INNER corner: the flange's inner wall and the base's
    inner face. Found through the bend's SMALLEST-radius cylinder (the outer one has radius
    + thickness), by the planar faces adjacent to it. `bend` indexes the cylinders from
    smallest to largest radius.
    """
    cands = []
    for f in I.faces():
        s = C.cast(f.GetSurface(), "ISurface")
        if not s.IsCylinder():
            continue
        planar, seen = [], set()
        for e in I.edges(f):
            for x in (C.cast(e, "IEdge").GetTwoAdjacentFaces2() or []):
                if x is None:
                    continue
                f2 = C.cast(x, "IFace2")
                if not C.cast(f2.GetSurface(), "ISurface").IsPlane():
                    continue
                key = tuple(round(v, 6) for v in _point_on_face(f2))
                if key in seen:
                    continue
                seen.add(key)
                planar.append(f2)
        # the bend's little RELIEF faces also touch the cylinder (measured: 12.6 mm2
        # against the wall's 5500). We keep the TWO with the largest area, and only if they
        # are perpendicular to each other -- which is what characterizes the corner.
        planar.sort(key=lambda x: x.GetArea(), reverse=True)
        if len(planar) < 2:
            continue
        a, b = planar[0], planar[1]
        na, nb = a.Normal, b.Normal
        if abs(sum(na[k] * nb[k] for k in range(3))) > 0.2:
            continue
        cands.append((s.CylinderParams[6], [a, b]))
    if not cands:
        raise RuntimeError("the part has no bend with two planar support faces.")
    # the SMALLEST-radius cylinder is the INNER one (the outer has radius + thickness), and
    # it is there that the two CONCAVE-side faces live -- the ones the gusset rests on.
    cands.sort(key=lambda t: t[0])
    if bend >= len(cands):
        raise RuntimeError(f"bend {bend} does not exist ({len(cands)} candidates).")
    return cands[bend][1]


def gusset(faces=None, *, bend: int = 0, offset_mm: float = 10.0,
           depth_mm: float = 10.0, width_mm: float = 5.0,
           thickness_mm: float = 0.0, draft: bool = False, draft_deg: float = 5.0,
           use_offset: bool = True, gusset_type: int = 0,
           inner_fillet_mm: float = 0.0, outer_fillet_mm: float = 0.0,
           edge_fillet_mm: float = 0.0) -> str:
    """GUSSET (reinforcing rib) over a bend: stiffens the corner without welding.

    Without `faces`, it rests on bend `bend` (see `bend_faces`). `thickness_mm` = 0 uses
    the sheet thickness. Returns the feature name.

    MEASURED GOTCHA (2026-08-31) -- 19 variants returned None and the cause was NEVER the
    method version nor the arrays: it was the FACE, as with the closed corner.

      - the two support faces are the ones on the bend's CONCAVE side (the flange's inner
        wall and the base's inner face). The old variants took the LARGEST-AREA faces,
        which are the outer ones;
      - with those two faces, mark 0, the 21-argument v1
        (`InsertSheetMetalGussetFeature`) builds. Neither v2/v3 nor `ArrayOfFaces` was
        needed;
      - SW's macro recorder does NOT record this command (the macro comes out with the
        selections and no call). The parameters below were read off the FEATURE the UI
        created, through `ISMGussetFeatureData` -- and NOT `IGussetFeatureData`, which is
        for a welded profile and returns garbage (Thickness = 8.9e-308);
      - `draft_deg` goes to COM in RADIANS (unlike `jog`, which wants degrees), and the
        `BDraft` the UI records is **False**: turning the draft angle on (bool(draft_deg),
        which was the bug in this verb's 1st version) makes the method return None. That is
        why `draft` is a separate parameter, defaulting to False.
    """
    md = _md()
    if not is_sheet():
        raise RuntimeError("gusset only applies to a sheet metal part.")
    support = list(faces) if faces is not None else bend_faces(bend)
    thick = thickness_mm if thickness_mm > 0 else thickness()   # already in mm
    nf0 = len(I.faces())
    md.ClearSelection2(True)
    if I.select([C.cast(f, "IEntity") for f in support]) < 2:
        raise RuntimeError("could not select the gusset's two support faces.")
    feat = _fm(md).InsertSheetMetalGussetFeature(
        bool(use_offset), C.mm(offset_mm), False, 0,
        C.mm(depth_mm), C.mm(depth_mm), False, C.mm(depth_mm), 0.0,
        False, C.mm(width_mm), C.mm(thick), bool(draft), C.deg(draft_deg),
        inner_fillet_mm > 0, C.mm(inner_fillet_mm or 1.0),
        outer_fillet_mm > 0, C.mm(outer_fillet_mm or 1.0), int(gusset_type),
        edge_fillet_mm > 0, C.mm(edge_fillet_mm or 1.0))
    md.ClearSelection2(True)
    md.EditRebuild3()
    if feat is None or len(I.faces()) == nf0:
        raise RuntimeError(
            "gusset refused. Either the support faces are not the ones on the bend's "
            "CONCAVE side (the largest-area ones are the OUTER ones and will not do), or "
            f"the rib does not FIT: offset {offset_mm} + depth {depth_mm} has to fit "
            "within the flange.")
    return last_feature()


def cross_break(face=None, *, angle_deg: float = 1.0, radius_mm: float = 1.0) -> str:
    """Cross break: a COSMETIC stiffening rib on a face (it does not change the solid's
    geometry -- it enters the tree and the flat pattern, not the volume)."""
    md = _md()
    I.select([face or base_face()])
    feat = _fm(md).InsertCrossBreak(C.deg(angle_deg), C.mm(radius_mm))
    md.ClearSelection2(True)
    md.EditRebuild3()
    if feat is None:
        raise RuntimeError("cross break refused by SW.")
    return C.cast(feat, "IFeature").Name


def rip(edges, gap_mm: float = 0.5) -> str:
    """Rip edges of a closed solid -- the step that allows a welded/folded box to be
    flattened. Works on an ordinary solid, BEFORE it becomes sheet metal."""
    md = _md()
    I.select([C.cast(e, "IEdge") for e in edges])
    md.InsertRip(C.mm(gap_mm))
    md.ClearSelection2(True)
    md.EditRebuild3()
    return last_feature()


# ── flat pattern ─────────────────────────────────────────────────────────────
def flatten(on: bool = True) -> bool:
    """Flatten the whole part (True) or fold it back (False). Returns whether it ended up
    flat.

    MEASURED GOTCHA, and one of the expensive ones:
    `IModelDoc2.SetBendState(swSMBendStateFlattened)` -- the method whose very NAME says
    exactly that -- is a NO-OP on SW 2017. It returns 0, which in swSMError_e means "no
    error", and the bounding box does not move a millimeter. The state it reports is no
    proof either: across two measured rounds `GetBendState` answered 1 (Sharps) in one and
    2 (Flattened) in the other, with the geometry intact in both. The path that works is to
    SUPPRESS / UNSUPPRESS the `Flat-Pattern1` feature, which is born suppressed -- and what
    answers "is it flat?" is `is_flat()`, not SW's flag.
    """
    md = _md()
    fp = _feat_by_type("FlatPattern")
    if fp is None:
        raise RuntimeError("part has no Flat-Pattern (not sheet metal?)")
    md.ClearSelection2(True)
    if not _ext(md).SelectByID2(fp.Name, "BODYFEATURE", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError(f"could not select '{fp.Name}'.")
    md.EditUnsuppress2() if on else md.EditSuppress2()
    md.ClearSelection2(True)
    md.EditRebuild3()
    return is_flat()


def is_flat() -> bool:
    """Is the part flattened right now?"""
    fp = _feat_by_type("FlatPattern")
    return fp is not None and not fp.IsSuppressed()


def _select_fixed_and_bends(md, fixed_face, names):
    """Fixed face with mark=1 and each bend with mark=2 -- the selection unfold/fold
    require.

    The fixed face is selected as an ENTITY, not by coordinate: a SelectByID2("FACE",
    x,y,z) at a border point resolves to the neighbouring face, and the fold comes out
    wrong (measured: the part came back 60mm tall instead of 27, folded the wrong way).
    """
    md.ClearSelection2(True)
    if not I.select_marked(fixed_face or base_face(), 1, append=False):
        raise RuntimeError("could not select the fixed face.")
    for n in names:
        _ext(md).SelectByID2(n, "BODYFEATURE", 0, 0, 0, True, 2, None, 0)


def unfold(bend_names=None, fixed_face=None) -> str:
    """Unfold SPECIFIC bends (default: all of them), keeping `fixed_face` put.
    This is what allows a hole to be cut ACROSS a bend: unfold -> cut -> fold.

    The selection goes by MARK: fixed face mark=1, each bend mark=2 -- and the bend has to
    be found as a sub-feature (see `bends`). With the wrong selection SW creates the
    `UnFold1` feature and unfolds nothing, without reporting an error.
    """
    md = _md()
    names = list(bend_names) if bend_names else bends()
    if not names:
        raise RuntimeError("part has no bends to unfold.")
    _select_fixed_and_bends(md, fixed_face, names)
    md.InsertSheetMetalUnfold()
    md.ClearSelection2(True)
    md.EditRebuild3()
    return last_feature()


def fold(bend_names=None, fixed_face=None) -> str:
    """Fold back what `unfold` unfolded. It wants the SAME selection as the unfold (fixed
    face mark=1 + bends mark=2); with an empty selection it creates the feature and folds
    nothing."""
    md = _md()
    names = list(bend_names) if bend_names else bends()
    _select_fixed_and_bends(md, fixed_face, names)
    md.InsertSheetMetalFold()
    md.ClearSelection2(True)
    md.EditRebuild3()
    return last_feature()


# ── fabrication output ───────────────────────────────────────────────────────
def export_flat(path: str, *, remove_bends: bool = False) -> str:
    """Export the FLAT PATTERN to DXF or DWG (the file that goes to the cutting machine).
    `remove_bends` takes the bend lines out of the drawing.

    MEASURED GOTCHA, and the worst of the family: `ExportFlatPatternView` requires the part
    SAVED TO DISK, and the way it fails is UNSTABLE. Sometimes it returns False right away,
    writing nothing and saying nothing; other times it **opens a "Save As" box and waits for
    the user**, stalling automation indefinitely (measured 2026-08-29: two modal windows
    hung for ~20 min until they were cancelled, and only then did the call return False).
    That is why this verb does NOT call the API before checking the path: on a part with no
    file it raises immediately, instead of risking the modal.
    """
    md = _md()
    if not md.GetPathName():
        raise RuntimeError(
            "export_flat requires the part SAVED to disk (ExportFlatPatternView fails "
            "silently on a part with no file). Run sw_parts.save(path) first.")
    folder = os.path.dirname(os.path.abspath(path))
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    opt = FLAT_REMOVE_BENDS if remove_bends else FLAT_KEEP_BENDS
    if not C.active("IPartDoc").ExportFlatPatternView(path, opt):
        raise RuntimeError(f"ExportFlatPatternView failed: {path}")
    return path


def cut_list() -> dict:
    """The sheet's FABRICATION data, resolved: length and width of the flat pattern (the
    material rectangle to buy), thickness, number of bends, cut length, bend radius and
    bend allowance. It comes from the part's cut list folder.

    MEASURED GOTCHA: `CustomPropertyManager.Get` returns the FORMULA
    ('"SW-Bounding Box Length@@@Sheet<1>@part.SLDPRT"'), not the value. What resolves it is
    `Get2`, whose early-binding return is the TUPLE (formula, value). And `Get5`, the
    NEWEST version, returns the resolved value EMPTY -- once again the pattern "the new
    version is the one that does not work", just like InsertSheetMetalBaseFlange2.
    """
    md = _md()
    md.ForceRebuild3(False)          # the cut list only exists after the rebuild
    folders = _cut_list_folders()
    if not folders:
        raise RuntimeError("part has no cut list (not sheet metal?)")
    if len(folders) > 1:
        # Returning the 1st folder would report ONE body as if it were the part -- the kind
        # of silent lie this whole front exists in order not to commit.
        raise RuntimeError(
            f"MULTI-BODY part ({len(folders)} cut lists: "
            f"{[f.Name for f in folders]}) -- use cut_lists() and pick the body.")
    return _folder_props(folders[0])


# ── parameters ───────────────────────────────────────────────────────────────
def thickness() -> float:
    """Sheet thickness in mm.

    Not every sheet metal part has a BASE flange: one born from `lofted_bend` has only the
    `SheetMetal` + `LoftedBend` features, and reading the thickness through the base flange
    would raise "part has no base flange" on a perfectly valid part. So the reading falls
    back to the `SheetMetal` feature, which exists on any sheet metal part.
    """
    bf = _feat_by_type("SMBaseFlange")
    if bf is not None:
        return C.cast(bf.GetDefinition(), "IBaseFlangeFeatureData").Thickness * 1000.0
    smf = _feat_by_type("SheetMetal")
    if smf is None:
        raise RuntimeError("part is not sheet metal.")
    return C.cast(smf.GetDefinition(), "ISheetMetalFeatureData").Thickness * 1000.0


def set_thickness(mm: float) -> float:
    """Change the sheet thickness (the whole part rebuilds). Returns the new thickness."""
    md = _md()
    bf = _feat_by_type("SMBaseFlange")
    d = C.cast(bf.GetDefinition(), "IBaseFlangeFeatureData")
    d.Thickness = C.mm(mm)
    # With the gauge table ON the geometry obeys anyway -- but the model would keep saying
    # "Gauge 5" (5.31 mm) while measuring 4 mm. Setting the override makes the file declare
    # that the thickness was imposed by hand. Measured 2026-08-30.
    if d.UseGaugeTable:
        d.OverrideThickness = True
    if not bf.ModifyDefinition(d, md, None):
        raise RuntimeError("ModifyDefinition refused the thickness.")
    md.EditRebuild3()
    return thickness()


def bend_params() -> dict:
    """The part's GLOBAL bend parameters: {radius_mm, k_factor}.

    READ-ONLY on purpose: `ModifyDefinition` over `ISheetMetalFeatureData` was measured
    returning True and leaving the radius unchanged (`set_thickness`, through the same path
    but on `IBaseFlangeFeatureData`, really does change it and the geometry confirms).
    To change the radius, pass `radius_mm` on the flange/sheet you are creating.
    """
    smf = _feat_by_type("SheetMetal")
    if smf is None:
        raise RuntimeError("part has no SheetMetal feature.")
    d = C.cast(smf.GetDefinition(), "ISheetMetalFeatureData")
    return {"radius_mm": d.BendRadius * 1000.0, "k_factor": d.KFactor}


# ── verbs from the 2nd round (2026-08-29b) ───────────────────────────────────
def _point_on_face(face):
    """An INTERIOR point of the face (to re-select it by coordinate after the IFace2
    pointer stops being valid)."""
    b = face.GetBox()
    center = ((b[0] + b[3]) / 2.0, (b[1] + b[4]) / 2.0, (b[2] + b[5]) / 2.0)
    p = face.GetClosestPointOn(*center)
    return (p[0], p[1], p[2])


def sketched_bend(line=None, *, face=None, sketch_name: str = "",
                  angle_deg: float = 90.0, radius_mm: float = 3.0,
                  flip: bool = False, position: str = "bend_outside") -> str:
    """SKETCHED bend: bends the sheet along a line drawn on it.

    It is the bend that does not come from an edge -- the one that makes a U channel, a
    step or a flange in the middle of the sheet. `line` = ((x1,y1,z1),(x2,y2,z2)) in mm in
    the MODEL system, crossing the face; if you have already drawn the sketch, pass
    `sketch_name` (or leave it empty to use the last one). The side that stays PUT is the
    `face`'s side.

    MEASURED IN HOUSE (2026-08-29), and it contradicts the earlier handoff: the
    `FeatureManager.CreateDefinition(swFmSketchBend)` path returns None -- hence the old
    conclusion that the sketched bend was impossible -- but the DIRECT method
    `InsertSheetMetal3dBend` builds: the sheet bends, the volume is preserved, the bend
    shows up in `bends()` as `SketchBend1` and `flatten()` undoes it like any other.
    TWO lines in the same sketch = TWO bends in one call (the U channel).

      position: 'bend_outside'(default) | 'material_inside' | 'material_outside'.
        'bend_centerline' was MEASURED as a silent no-op -- it is refused here.
    """
    md = _md()
    if position == "bend_centerline":
        raise ValueError(
            "position='bend_centerline' is a MEASURED no-op in InsertSheetMetal3dBend "
            "(returns a feature and does not bend). Use 'bend_outside'/'material_inside'/"
            "'material_outside'.")
    face = face or base_face()
    fixed_pt = _point_on_face(face)
    if line is not None:
        (x1, y1, z1), (x2, y2, z2) = line
        md.ClearSelection2(True)
        C.cast(face, "IEntity").Select4(False, None)
        sm = _sm(md)
        sm.InsertSketch(True)
        m = C.cast(C.cast(sm.ActiveSketch, "ISketch").ModelToSketchTransform,
                   "IMathTransform").ArrayData
        a = _pt_to_sketch(m, (C.mm(x1), C.mm(y1), C.mm(z1)))
        b = _pt_to_sketch(m, (C.mm(x2), C.mm(y2), C.mm(z2)))
        sm.CreateLine(a[0], a[1], 0.0, b[0], b[1], 0.0)
        md.ClearSelection2(True)
        sm.InsertSketch(True)
    name = sketch_name or last_sketch()
    if not name:
        raise RuntimeError("no sketch for the bend: pass `line` or `sketch_name`.")
    md.ClearSelection2(True)
    _select_sketch(md, name)
    # the fixed face is re-selected by the COORDINATE of an INTERIOR point of it: the
    # IFace2 from before the sketch may no longer be valid, and a border point would
    # resolve to the neighbouring face (invariant 7).
    if not _ext(md).SelectByID2("", "FACE", fixed_pt[0], fixed_pt[1], fixed_pt[2],
                                True, 0, None, 0):
        raise RuntimeError("could not select the bend's fixed face.")
    feat = _fm(md).InsertSheetMetal3dBend(
        C.deg(angle_deg), False, C.mm(radius_mm), bool(flip), POS.get(position, 3), None)
    md.ClearSelection2(True)
    md.EditRebuild3()
    if feat is None:
        raise RuntimeError(
            "sketched bend refused (does the line cross the face? is the face part of the "
            "sheet?)")
    return C.cast(feat, "IFeature").Name


def _perpendicular_plane(e2, *, end_idx: int = 0):
    """Name of a reference plane PERPENDICULAR to the edge, through one of its ends.

    Works for ANY straight edge, OBLIQUE included -- which was the case that blocked
    `miter_flange` and the only escape hatch item that was not a sheet metal feature.

    MEASURED GOTCHA (2026-08-31, from a macro recorded in the UI) -- it knocks down the
    operational half of invariant 31: `InsertRefPlane` with TWO references only builds when
    each reference comes in with a DIFFERENT MARK -- the EDGE at mark 0 and the VERTEX at
    mark 1. The 11 old variants used the SAME mark for both, which is why they were all
    no-ops or produced a plane merely parallel to the reference one. What still holds from
    invariant 31 is that, with the wrong mark, the angle is ignored.

    The two constraint codes are the ones the UI RECORDS (2 for the edge, 4 for the
    vertex). They stay as measured values: the `swRefPlaneReferenceConstraints_e` names we
    looked up do not match them, and inventing a name would be worse than the constant.
    """
    md = _md()
    v = e2.GetStartVertex() if end_idx == 0 else e2.GetEndVertex()
    if v is None:
        raise RuntimeError("edge has no vertex (closed?) -- pass plane_name.")
    n0 = _count_of_type("RefPlane")
    md.ClearSelection2(True)
    if not I.select_marked(e2, REFPLANE_MARK_EDGE, append=False):
        raise RuntimeError("could not select the edge for the perpendicular plane.")
    if not I.select_marked(C.cast(v, "IVertex"), REFPLANE_MARK_VERTEX, append=True):
        raise RuntimeError("could not select the vertex for the perpendicular plane.")
    _fm(md).InsertRefPlane(REFPLANE_EDGE, 0, REFPLANE_VERTEX, 0, 0, 0)
    md.ClearSelection2(True)
    md.EditRebuild3()
    if _count_of_type("RefPlane") <= n0:
        raise RuntimeError(
            "the perpendicular plane was not created (is the edge straight? does it have a "
            "vertex?)")
    return _last_of_type("RefPlane").Name


def miter_flange(edge, length_mm: float, *, face=None, plane_name: str = "",
                 radius_mm: float = 3.0, gap_mm: float = 0.5,
                 position: str = "material_inside") -> str:
    """MITER flange: the flange whose profile you control, coming off the edge.

    Difference from `edge_flange`: there the profile is the rectangle SW asks for; here the
    profile is a line on a plane PERPENDICULAR to the edge, and the flange length is that
    line's length (MEASURED: a 10 mm profile -> a 10 mm flange; 30 -> 30).

    MEASURED IN HOUSE (2026-08-29), knocking down the earlier verdict of "returns False":
    what was missing was the profile TOUCHING the tip of the edge. The 1st round drew the
    line from the model origin, and the sheet's top edge is at z=thickness -- two
    millimeters of distance is enough for SW to refuse silently.

    MEASURED LIMIT: selecting TWO edges does not propagate -- only one flange comes out.
    One call per edge.
    """
    md = _md()
    face = face or base_face()
    e2 = C.cast(edge, "IEdge")
    p = e2.GetCurveParams2()
    tip = p[0:3]
    nx, ny, nz = face.Normal
    target = (tip[0] - nx * C.mm(length_mm),
              tip[1] - ny * C.mm(length_mm),
              tip[2] - nz * C.mm(length_mm))
    plane = plane_name or _perpendicular_plane(e2)
    md.ClearSelection2(True)
    C.select_plane(md, plane)
    sm = _sm(md)
    sm.InsertSketch(True)
    m = C.cast(C.cast(sm.ActiveSketch, "ISketch").ModelToSketchTransform,
               "IMathTransform").ArrayData
    a, b = _pt_to_sketch(m, tip), _pt_to_sketch(m, target)
    sm.CreateLine(a[0], a[1], 0.0, b[0], b[1], 0.0)
    md.ClearSelection2(True)
    sm.InsertSketch(True)
    sk_name = last_sketch()
    md.ClearSelection2(True)
    _select_sketch(md, sk_name)
    C.cast(e2, "IEntity").Select4(True, None)
    feat = _fm(md).InsertSheetMetalMiterFlange(
        True, C.mm(radius_mm), C.mm(gap_mm), True, True, 0.5, 0.0, 0.0,
        RELIEF["rect"], False, POS.get(position, 1), 0.0, 0.0, None)
    md.ClearSelection2(True)
    md.EditRebuild3()
    if feat is None:
        raise RuntimeError(
            "miter flange refused (does the profile touch the tip of the edge? is the "
            "plane perpendicular to it?)")
    return C.cast(feat, "IFeature").Name


def cut(sketch_name: str = "", *, depth_mm: float = 0.0, through_all: bool = True,
        reverse: bool = False, auto_direction: bool = True) -> str:
    """Cut the sheet with a sketch (hole, slot, cutout). Returns the feature.

    It exists as a verb because of TWO gotchas that only show up in sheet metal:
      1. `sw_parts.extrude` with no `sketch_name` takes `FeatureByPositionReverse(0)`,
         which on a sheet metal part is ALWAYS `Flat-Pattern1` (invariant 9) -- here the
         default is `last_sketch()`, which ignores the flat pattern.
      2. the direction: a sketch on the top face with "through all" in the default
         direction cuts AWAY from the material and SW refuses. With `auto_direction`
         (default) the verb repeats the call in the opposite direction before giving up.

    To cut ACROSS a bend, the pairing is `unfold()` -> `cut()` -> `fold()`
    (MEASURED end to end: the cut survives the fold).
    """
    name = sketch_name or last_sketch()
    if not name:
        raise RuntimeError("no sketch to cut with.")
    try:
        return P.extrude(depth_mm, "cut", through_all=through_all,
                         reverse=reverse, sketch_name=name)
    except RuntimeError:
        if not auto_direction:
            raise
        return P.extrude(depth_mm, "cut", through_all=through_all,
                         reverse=not reverse, sketch_name=name)


def bend_info():
    """One row per BEND, with what the bench needs: angle, radius, direction, order,
    K-factor and allowance. Complements `cut_list()` (which speaks for the whole sheet).

    READ-ONLY on purpose: writing to `IOneBendFeatureData` was MEASURED to be a lie --
    `ModifyDefinition` returns True and the geometry does not change. To change one bend's
    radius, touch the PARENT feature (`set_flange(..., radius_mm=)`) or the global radius
    (`set_bend_radius`), both of which were measured really changing it.
    """
    out = []
    for name in bends():
        feat, d = _bend_feat_data(name)
        if d is None:
            continue
        out.append({"name": name,
                    "angle_deg": round(d.BendAngle * 180.0 / 3.141592653589793, 3),
                    "radius_mm": round(d.BendRadius * 1000.0, 4),
                    "direction": d.BendDirection,
                    "down": bool(d.BendDown),
                    "order": d.BendOrder,
                    "k_factor": d.KFactor,
                    "allowance": d.BendAllowance,
                    "allowance_type": d.BendAllowanceType})
    return out


def _bend_feat_data(name):
    """(feature, IOneBendFeatureData) of a bend by name -- it is a SUB-feature."""
    raw = _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        for sf in _subfeatures(f):
            if sf.Name == name:
                return sf, C.cast(sf.GetDefinition(), "IOneBendFeatureData")
        raw = f.GetNextFeature()
    return None, None


def set_bend_radius(mm: float) -> float:
    """Change the part's GLOBAL bend radius (every bend that uses the default).

    MEASURED GOTCHA, and it knocks down the "read-only" this project had on record:
    writing `BendRadius` on `ISheetMetalFeatureData` and calling `ModifyDefinition` returns
    True and changes NOTHING. Only after `SetOverrideDefaultParameter(1)` does the radius
    take -- and the proof is geometric: the flat length of the same part goes from 83.28 to
    81.14 mm. (With the parameter at 0 it still has no effect.)
    """
    md = _md()
    smf = _feat_by_type("SheetMetal")
    if smf is None:
        raise RuntimeError("part has no SheetMetal feature.")
    d = C.cast(smf.GetDefinition(), "ISheetMetalFeatureData")
    d.SetOverrideDefaultParameter(1)
    d.BendRadius = C.mm(mm)
    smf.ModifyDefinition(d, md, None)          # returns True even when it ignores you
    md.EditRebuild3()
    new_r = bend_params()["radius_mm"]
    if abs(new_r - mm) > 1e-3:
        raise RuntimeError(f"SW kept the radius at {new_r:.3f} mm (asked for {mm}).")
    return new_r


def _last_of_type(type_name):
    """LAST feature in the tree with that GetTypeName2 (`_feat_by_type` returns the first;
    to edit 'the flange I just created' the one you want is the last)."""
    found, raw = None, _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() == type_name:
            found = f
        raw = f.GetNextFeature()
    return found


def set_flange(name: str = "", *, angle_deg: float = None,
               radius_mm: float = None, k_factor: float = None) -> dict:
    """Edit a flange that already exists (angle, bend radius and/or K-FACTOR). Returns the
    new state.

    `name` = the flange feature's name (default: the last one in the tree). MEASURED: this
    way the edit really takes (the bounding box changes, and the bend starts reporting the
    new radius) -- unlike writing to the bend's sub-feature, which lies.
    The flange LENGTH is not included: it came from the profile rectangle, and writing
    `OffsetDistance` was measured to have no effect.

    `k_factor` is the only measured path that really CHANGES K -- see `set_k_factor`.
    """
    md = _md()
    # the flange's type in the tree is "EdgeFlange" (not "SMEdgeFlange", which is the base
    # flange's)
    ef = _feat_by_name(name) if name else _last_of_type("EdgeFlange")
    if ef is None:
        raise RuntimeError("could not find the flange feature (pass the name).")
    d = C.cast(ef.GetDefinition(), "IEdgeFlangeFeatureData")
    if angle_deg is not None:
        d.BendAngle = C.deg(angle_deg)
    if radius_mm is not None:
        d.UseDefaultBendRadius = False
        d.BendRadius = C.mm(radius_mm)
    if k_factor is not None:
        d.UseDefaultBendAllowance = False       # without this K silently reverts to default
        cba = C.cast(d.GetCustomBendAllowance(), "ICustomBendAllowance")
        cba.Type = CBA_KFACTOR
        cba.KFactor = float(k_factor)
        d.SetCustomBendAllowance(cba)
    if not ef.ModifyDefinition(d, md, None):
        raise RuntimeError("ModifyDefinition refused the flange.")
    md.EditRebuild3()
    ef2 = _feat_by_name(ef.Name)
    d2 = C.cast(ef2.GetDefinition(), "IEdgeFlangeFeatureData")
    cba2 = d2.GetCustomBendAllowance()
    return {"name": ef2.Name,
            "angle_deg": round(d2.BendAngle * 180.0 / 3.141592653589793, 3),
            "radius_mm": round(d2.BendRadius * 1000.0, 4),
            "k_factor": round(C.cast(cba2, "ICustomBendAllowance").KFactor, 4)
                        if cba2 is not None else None,
            "k_default": bool(d2.UseDefaultBendAllowance)}


# The bend features that ACCEPT their own allowance, measured one by one on 2026-08-31
# (round f) with a re-read AND geometric proof on the flat pattern. The SKETCHED bend
# (`SM3dBend`) is OUT by MEASUREMENT, not by oversight: there `ModifyDefinition` returns
# False and the re-read reverts to the default (type 2, empty file).
BENDS_WITH_ALLOWANCE = {
    "EdgeFlange": "IEdgeFlangeFeatureData",
    "SMMiteredFlange": "IMiterFlangeFeatureData",
    "Hem": "IHemFeatureData",
    "Jog": "IJogFeatureData",
}
# ...but the TABLE's reach is SMALLER than the K-factor's, and the difference is the JOG:
# there K obeys (104.05 / 109.08 mm for K=0.1 / 0.9) and the table does NOT --
# `ModifyDefinition` returns False, the re-read reverts to type 2 and the flat pattern does
# not move. Measured 2026-08-31 on the same part, with the same file that moves the other
# three. That is why the two lists are separate instead of one.
NO_BEND_TABLE = {"Jog"}


def _feats_with_allowance():
    """[(name, interface, type)] of each bend that accepts its own allowance, in tree
    order. The TYPE travels along because the TABLE's reach is smaller than the
    K-factor's."""
    out, raw = [], _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        type_name = f.GetTypeName2()
        iface = BENDS_WITH_ALLOWANCE.get(type_name)
        if iface:
            out.append((f.Name, iface, type_name))
        raw = f.GetNextFeature()
    return out


def _tree_types():
    """The tree's GetTypeName2 values -- only so the error message can say what EXISTS."""
    out, raw = [], _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        out.append(f.GetTypeName2())
        raw = f.GetNextFeature()
    return out


def _write_allowance(name, iface, *, k=None, table_path=None):
    """Write ONE bend's allowance and return the RE-READ allowance (the return is no
    proof)."""
    md = _md()
    f = _feat_by_name(name)
    d = C.cast(f.GetDefinition(), iface)
    d.UseDefaultBendAllowance = False       # without this the allowance silently reverts
    cba = C.cast(d.GetCustomBendAllowance(), "ICustomBendAllowance")
    if table_path is not None:
        cba.Type, cba.BendTableFile = CBA_BEND_TABLE, table_path
    else:
        cba.Type, cba.KFactor = CBA_KFACTOR, float(k)
    d.SetCustomBendAllowance(cba)
    if not f.ModifyDefinition(d, md, None):
        raise RuntimeError(f"ModifyDefinition refused the allowance of bend '{name}'.")
    md.EditRebuild3()
    d2 = C.cast(_feat_by_name(name).GetDefinition(), iface)
    return C.cast(d2.GetCustomBendAllowance(), "ICustomBendAllowance")


def _default_allowance(name, iface):
    """Return ONE bend to the document's default allowance -- the way back when the table
    does not suit this sheet and the flat pattern starts erroring out."""
    md = _md()
    f = _feat_by_name(name)
    d = C.cast(f.GetDefinition(), iface)
    d.UseDefaultBendAllowance = True
    f.ModifyDefinition(d, md, None)
    md.EditRebuild3()


def _flat_pattern_error() -> int:
    """`Flat-Pattern1`'s error code (0 = no error). An allowance the sheet cannot take does
    NOT raise: it breaks the FLAT PATTERN, and the part stays folded."""
    fp = _feat_by_type("FlatPattern")
    return int(fp.GetErrorCode()) if fp is not None else 0


def set_k_factor(k: float) -> dict:
    """K-FACTOR of ALL the part's reachable bends. Returns {bends, k_factor}.

    MEASURED GOTCHA (2026-08-30) -- here the RADIUS rule is inverted. The GLOBAL radius
    changes through the `SheetMetal` feature with `SetOverrideDefaultParameter(1)`
    (invariant 16); the K-factor through the SAME path returns True and changes NOTHING,
    neither via `ISheetMetalFeatureData.KFactor` nor via `SetCustomBendAllowance` on the
    global feature, with override 0/1/2/3 -- 8 combinations, the flat pattern frozen at
    83.2832 mm. What yields is the BEND. Geometric proof on the 100x60x2 sheet with a 25
    flange: K=0.1 -> 82.0265 mm; K=0.5 -> 83.2832 mm; K=0.9 -> 84.5398 mm.

    REACH, measured bend by bend on 2026-08-31 (round f) -- and it is LARGER than the
    earlier handoff said: edge flange, MITER flange, HEM and JOG all obey, each with its
    own geometric proof. The "mitered" half of the old caveat was false. What is NOT
    reached is the SKETCHED bend (`SM3dBend`): there `ModifyDefinition` returns False and
    the re-read reverts to the default.
    """
    feats = _feats_with_allowance()
    if not feats:
        raise RuntimeError(
            "part has no bend that accepts its own allowance. Reached: edge flange, miter "
            "flange, hem and jog; the sketched bend is NOT (measured). Types in the tree: "
            f"{sorted(set(_tree_types()) & set(BENDS_WITH_ALLOWANCE) | set())}")
    for name, iface, _ in feats:
        _write_allowance(name, iface, k=k)
    return {"bends": len(feats), "k_factor": float(k),
            "names": [n for n, _, _ in feats]}


def set_bend_table(path: str = "") -> dict:
    """Attach the BEND TABLE (`.btl`) to the part's bends -- the allowance now comes from
    the REAL MATERIAL's table instead of the K-factor. Without `path`, it only reports what
    is attached.

    It is `gauge_table`'s sibling: that one says which thicknesses and radii exist, this
    one says how much the sheet STRETCHES for each combination of thickness, radius and
    angle. Together they are what ties the flat pattern to the customer's material.

    ITEM 8 OF THE ESCAPE HATCH -- it fell on 2026-08-31 (round f), and the cause was the
    PATH, not the feature. The method that gave the item its name,
    `IModelDoc2.InsertBendTableOpen`, is still out: it was measured stalling automation in
    a modal (invariant 20). What works is the same target feature that had already solved
    the K-factor -- the BEND -- through `ICustomBendAllowance` with
    `Type = swBendAllowanceBendTable(1)` + `BendTableFile`. Through the DOCUMENT
    (`ISheetMetalFeatureData.BendTableFile`, with `SetOverrideDefaultParameter` 0/1/2/3)
    there are 5 combinations that return True, re-read empty and do not move the flat
    pattern -- the usual lie.

    GEOMETRIC PROOF, and it is LINEAR, which is what separates "it sticks" from "it obeys":
    with four CONSTANT-allowance `.btl` files written for the measurement, the flat pattern
    of the same 100x60x2 sheet with a 25 flange gave 78.0 / 80.0 / 82.0 / 86.0 mm for
    allowances of 1 / 3 / 5 / 9 mm -- exactly `77 + allowance`. SW's own `sample.btl`, which
    tabulates 1 mm at that thickness, lands on the same 78.0 mm.

    REACH, measured bend by bend: edge flange, MITER flange and HEM accept the table; the
    JOG does NOT -- there `ModifyDefinition` returns False and the re-read reverts to the
    default, even though the SAME jog accepts a K-factor. And the SKETCHED bend accepts
    neither. The ones left out are named in `not_reached`, never in silence.

    FORMAT: it has to be `.btl`. The `.xls` files in SW's table folder were measured and do
    NOT work here (6 files): they save without complaining and `Flat-Pattern1` starts
    giving error 51, leaving the part folded. That is why this verb CHECKS the flat pattern
    after writing and undoes it if it broke -- instead of leaving the part in a state that
    would only show up the next time somebody tried to flatten it.
    """
    feats = _feats_with_allowance()
    targets = [f for f in feats if f[2] not in NO_BEND_TABLE]
    left_out = [n for n, _, tp in feats if tp in NO_BEND_TABLE]
    if path:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        if not targets:
            raise RuntimeError(
                "part has no bend that accepts a bend table. Reached: edge flange, miter "
                "flange and hem; the JOG and the sketched bend do NOT (measured). "
                f"Bends on this part: {[n for n, _, _ in feats] or 'none'}. "
                "The K-factor (set_k_factor) reaches the jog as well.")
        for name, iface, _ in targets:
            _write_allowance(name, iface, table_path=path)
        _md().ForceRebuild3(False)
        err = _flat_pattern_error()
        if err:
            for name, iface, _ in targets:
                _default_allowance(name, iface)
            raise RuntimeError(
                f"the table '{os.path.basename(path)}' broke the flat pattern "
                f"(Flat-Pattern error {err}) -- the bends were returned to the default "
                f"allowance. Use a .btl that covers this thickness ({thickness():.2f} mm) "
                "and this radius; the .xls files in SW's table folder do not work here.")
    out = []
    for name, iface, _ in feats:
        d = C.cast(_feat_by_name(name).GetDefinition(), iface)
        cba = C.cast(d.GetCustomBendAllowance(), "ICustomBendAllowance")
        out.append({"bend": name, "allowance_type": int(cba.Type),
                    "table": cba.BendTableFile or "",
                    "k_factor": round(cba.KFactor, 4),
                    "default": bool(d.UseDefaultBendAllowance)})
    # `not_reached` comes out ALWAYS, even empty: a part with a jog has a bend that stays on
    # the default allowance, and omitting that would be the part claiming it uses the whole
    # table when only part of it does
    return {"bends": out, "table": path or "", "not_reached": left_out}


def _feat_by_name(name):
    raw = _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.Name == name:
            return f
        raw = f.GetNextFeature()
    return None


def flat_options(*, fixed_face=None, merge: bool = None, simplify: bool = None,
                 corner_treatment: bool = None) -> dict:
    """Read (and optionally change) how the FLAT PATTERN is generated -- what ends up in
    the DXF.

      fixed_face       -> the face that stays put (defines the plane's orientation)
      merge            -> merges coplanar faces (fewer lines in the DXF)
      simplify         -> simplifies the bends
      corner_treatment -> automatic corner treatment

    Called with no arguments at all, it only reports. Returns the state after the change.
    """
    md = _md()
    fp = _feat_by_type("FlatPattern")
    if fp is None:
        raise RuntimeError("part has no Flat-Pattern (not sheet metal?)")
    d = C.cast(fp.GetDefinition(), "IFlatPatternFeatureData")
    changed = False
    if fixed_face is not None:
        d.AccessSelections(md, None)
        d.FixedFace2 = fixed_face
        changed = True
    for attr, val in (("MergeFace", merge), ("SimplifyBends", simplify),
                      ("CornerTreatment", corner_treatment)):
        if val is not None:
            setattr(d, attr, bool(val))
            changed = True
    if changed:
        if not fp.ModifyDefinition(d, md, None):
            raise RuntimeError("ModifyDefinition refused the flat pattern.")
        md.EditRebuild3()
        d = C.cast(_feat_by_type("FlatPattern").GetDefinition(),
                   "IFlatPatternFeatureData")
    return {"merge": bool(d.MergeFace), "simplify": bool(d.SimplifyBends),
            "corner_treatment": bool(d.CornerTreatment),
            "corner_radius_mm": d.BreakCornerRadius * 1000.0}


def gauge_table(path: str = "") -> dict:
    """Read (with no argument) or ATTACH (with `path`) the base flange's GAUGE table.

    It is what ties the model to the customer's real material: with the table attached, the
    valid thicknesses and radii start coming from it. SW's own sample tables live in
    `<SOLIDWORKS>\\lang\\<language>\\Sheet Metal Gauge Tables`.

    SIBLING VERB NOT IMPLEMENTED, on purpose: the BEND table (`InsertBendTableOpen`) was
    MEASURED stalling automation -- it opens a modal and never comes back (same pattern as
    invariant 8). Stick to the gauge table.
    """
    md = _md()
    bf = _feat_by_type("SMBaseFlange")
    if bf is None:
        raise RuntimeError("part has no base flange.")
    d = C.cast(bf.GetDefinition(), "IBaseFlangeFeatureData")
    if path:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        d.UseGaugeTable = True
        d.GaugeTablePath = path
        if not bf.ModifyDefinition(d, md, None):
            raise RuntimeError("ModifyDefinition refused the gauge table.")
        md.EditRebuild3()
        d = C.cast(_feat_by_type("SMBaseFlange").GetDefinition(),
                   "IBaseFlangeFeatureData")
    names = tuple(d.GetTableThicknesses() or ()) if d.UseGaugeTable else ()
    return {"active": bool(d.UseGaugeTable), "path": d.GaugeTablePath,
            "thicknesses": d.GetTableThicknessesCount(),
            "thickness_mm": d.TableThickness * 1000.0,
            "radius_mm": d.TableRadius * 1000.0,
            "gauge": d.ThicknessTableName if d.UseGaugeTable else "",
            "gauges": names,
            "override_thickness": bool(d.OverrideThickness)}


def gauge_radii(gauge: str = ""):
    """Bend radii the TABLE allows for a gauge (mm). With no argument, those of the
    selected gauge. `GetTableRadiiCount`/`GetTableRadii` require the gauge NAME as an
    argument -- called without it they raise 'Type mismatch'."""
    bf = _feat_by_type("SMBaseFlange")
    if bf is None:
        raise RuntimeError("part has no base flange.")
    d = C.cast(bf.GetDefinition(), "IBaseFlangeFeatureData")
    if not d.UseGaugeTable:
        raise RuntimeError("part has no gauge table attached -- use gauge_table(path).")
    name = gauge or d.ThicknessTableName
    return [r * 1000.0 for r in (d.GetTableRadii(name) or ())]


def set_gauge(gauge: str) -> float:
    """PICK a gauge from the attached table (e.g. 'Gauge 3'). Returns the resulting
    GEOMETRIC thickness, in mm.

    MEASURED GOTCHA (2026-08-30): the selector is `ThicknessTableName`, a NAME -- and not
    `TableThickness`, which only REPORTS the current gauge's thickness and writing to it
    selects nothing. The names come from `GetTableThicknesses()` ('Gauge 5', 'Gauge 4',
    'Gauge 3' in SW's sample table), they are not numbers. Geometric proof: Gauge 3 leaves
    the sheet at 6.0731 mm and Gauge 5 at 5.3137 mm, measured on the bounding box.
    """
    md = _md()
    bf = _feat_by_type("SMBaseFlange")
    if bf is None:
        raise RuntimeError("part has no base flange.")
    d = C.cast(bf.GetDefinition(), "IBaseFlangeFeatureData")
    if not d.UseGaugeTable:
        raise RuntimeError("part has no gauge table attached -- use gauge_table(path).")
    names = tuple(d.GetTableThicknesses() or ())
    if gauge not in names:
        raise ValueError(f"gauge '{gauge}' is not in the table. Options: {list(names)}")
    d.ThicknessTableName = gauge
    d.OverrideThickness = False        # the TABLE is now in charge
    if not bf.ModifyDefinition(d, md, None):
        raise RuntimeError("ModifyDefinition refused the gauge.")
    md.EditRebuild3()
    return thickness()


def _sketches(n: int = 2):
    """Names of the last `n` sketches (ProfileFeature) in the tree, in tree order."""
    names, raw = [], _md().FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() == "ProfileFeature":
            names.append(f.Name)
        raw = f.GetNextFeature()
    return names[-n:]


def _select_segments(md, names, mark: int = 1):
    """Select ALL the segments of the sketches `names` with the same mark.

    `ISketchSegment` does NOT cast to `IEntity` (invariant 18) -- the segment has its own
    `Select4(append, data)`, and the mark goes through `SelectData`.
    """
    selmgr = C.cast(md.SelectionManager, "ISelectionMgr")
    md.ClearSelection2(True)
    first = True
    for name in names:
        f = _feat_by_name(name)
        if f is None:
            raise RuntimeError(f"sketch '{name}' is not in the tree.")
        sk = C.cast(f.GetSpecificFeature2(), "ISketch")
        for seg in (sk.GetSketchSegments() or []):
            sd = selmgr.CreateSelectData()
            sd.Mark = mark
            if not C.cast(seg, "ISketchSegment").Select4(not first, sd):
                raise RuntimeError(f"could not select a segment of '{name}'.")
            first = False
    if first:
        raise RuntimeError("the sketches have no segments at all.")


def lofted_bend(sketch_a: str = "", sketch_b: str = "", thickness_mm: float = 2.0,
                *, side: str = "inside") -> str:
    """TRANSITION sheet between two OPEN profiles (lofted bend) -- the funnel/adapter.

    `sketch_a`/`sketch_b`: the names of the two profile sketches (default: the last two in
    the tree). The profiles have to be OPEN and have the SAME number of segments.
    `side`: 'inside' (the thickness grows into the profile) or 'outside'.

    MEASURED GOTCHA (2026-08-30) -- this item left the escape hatch with no new API, just by
    changing THE SELECTION: with the two sketches selected as "SKETCH" (which is what the
    previous round did, with marks 0/1/4) `InsertSheetMetalLoftedBend` returns None and
    creates no body. Selecting the SEGMENTS (`ISketchSegment.Select4`) with **mark=1** it
    builds: the tree gains `SheetMetal` + `LoftedBend` + `FlatPattern` and the body comes
    out at the requested thickness (2 mm -> 2.1602e-05 m3; 3 mm -> 3.2883e-05). With the
    segments at mark=0 it is still None -- it is the mark, not the entity type.

    MEASURED LIMIT: the resulting part has NO base flange, so `set_thickness`,
    `gauge_table` and `set_bend_radius` (which work on the base flange) do not reach it --
    only `thickness()`, which falls back to the `SheetMetal` feature. And `flatten(True)`
    marks `is_flat()` but does NOT change the solid here; the flat pattern exists anyway and
    `export_flat` generates the DXF normally (24.6 KB in the measured case).
    """
    if side not in ("inside", "outside"):
        raise ValueError("side must be 'inside' or 'outside'.")
    md = _md()
    names = [n for n in (sketch_a, sketch_b) if n] or _sketches(2)
    if len(names) != 2:
        raise RuntimeError("lofted_bend needs TWO profile sketches.")
    _select_segments(md, names, mark=1)
    feat = _fm(md).InsertSheetMetalLoftedBend(1 if side == "inside" else 2,
                                              C.mm(thickness_mm))
    md.ClearSelection2(True)
    md.EditRebuild3()
    if feat is None:
        raise RuntimeError(
            "lofted bend refused (are the profiles OPEN? same number of segments?)")
    return C.cast(feat, "IFeature").Name


# ── Escape hatch: MEASURED as non-functional through pywin32 on SW 2017 ──────
# These did not become verbs because they do not work, not because time ran out. Each one
# was tried with alternative selections and marks (solidworks/tests/probes/probe_sheetmetal.py
# carries the return value of every attempt; solidworks/docs/SHEET_METAL.md summarizes it).
#
# UPDATED 2026-08-31 (round e). The CONSTRUCTION research queue is DONE: every mapped sheet
# metal MODELLING feature has a verb. Already out of this list: the sketched bend and the
# miter flange (round b), the LOFTED BEND and the K-FACTOR (round c), CONVERT TO SHEET
# METAL and CLOSED CORNER (round d) and, in round (e), the last four -- JOG, CORNER TRIM,
# GUSSET and the PLANE PERPENDICULAR to an edge, which together accounted for 60 variants
# measured against. In all eight the old verdict was about the PATH, the SELECTION, the
# UNIT or the TARGET -- never about the feature.
#
# The causes, because their shape is this front's real asset (none was a new argument):
#
#   jog            18 variants. The ANGLE goes in DEGREES, not radians -- the only measured
#                  exception to the project's convention. With C.deg(90) it is a silent
#                  no-op. DimPos/BendPos change the RESULT, never the whether-it-works.
#                  Second gotcha: the point that re-selects the fixed face must not land ON
#                  TOP of the line -- there SW rotates half the sheet 90 degrees, no error.
#   corner trim    12 variants. Three errors added together: InternalCornerFlag has to be 0
#                  (it was 1); the selection needs SEVERAL edges (a single one returns
#                  None); and the part has to be flattened by the verb -- the
#                  SetBendState(2) the UI records does not flatten through COM.
#   gusset         19 variants. It was the FACE: the two that FORM the bend's corner, not
#                  the largest-area ones. The 21-arg v1 always sufficed; BDraft has to be
#                  False. And the MACRO RECORDER IS BLIND to this command -- the parameters
#                  came from the feature the UI created, through ISMGussetFeatureData (the
#                  cast through IGussetFeatureData returns garbage WITHOUT raising).
#   perp. plane    11 variants. InsertRefPlane with two references requires a DIFFERENT
#   to an edge     MARK per reference: edge at 0, vertex at 1. With the wrong mark it is a
#                  no-op or gives a merely parallel plane -- which is where the old verdict
#                  of "it ignores the angle" came from. And it is what unblocked
#                  miter_flange on an OBLIQUE edge.
#
# WHAT STILL DOES NOT WORK, and they are two items on the DRAWING side (neither prevents
# modelling or fabricating):
#
#   BEND table     IModelDoc2.InsertBendTableOpen STALLS automation: it opens a modal
#                  ("Open Package Contents") and never returns. Use the GAUGE table
#                  (gauge_table + set_gauge), which works.
#   cut list note  IView.InsertCutListPropertyNote raises 'The server threw an exception'
#
# And a limit that is not an escape hatch but a data source that does not exist on the part:
#
#   bend line      IOneBendFeatureData.FlatPatternSketchSegments returns ZERO segments,
#   from the part  folded or flattened. It exists in the DRAWING: sw_drawing.bend_lines.
#
# The method that brought all eight down was always the same: sweep ENTITY x MARK x ORDER x
# TARGET-FEATURE x VERSION x UNIT and check the GEOMETRY -- in sheet metal what is missing
# is almost never the argument. And it is the lesson that has been paid for six times: an
# "exhausted" only counts if the axes were crossed WITH EACH OTHER, and not merely varied
# one at a time.
