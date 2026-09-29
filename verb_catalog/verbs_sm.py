"""
verbs_sm.py -- SHEET METAL verbs compiled into bundles.

Port of `solidworks/sw_sheetmetal.py`. What stays here is the whole asset of the front:
five live measurement rounds' worth of which method version builds and which is a silent
no-op, which unit each argument wants, which mark each selection needs, which interface
answers the truth, and -- above all -- WHAT COUNTS AS PROOF, because in this domain the
return value is not one.

The four that a reader has to carry over from `solidworks/docs/SHEET_METAL.md`, because they run
against every other domain's intuition:

  1. The NEWEST version is the one that does not work, three times over.
     `InsertSheetMetalBaseFlange2` (19 args) is a silent no-op; the 16-arg v1 builds.
     `CustomPropertyManager.Get5` resolves to empty; `Get2` resolves. `SetBendState`
     reports "no error" and flattens nothing; suppressing `Flat-Pattern1` flattens.
     The exception that proves it: the CONVERT wants v2 -- the 7-arg v1 is the no-op
     there. Version is a MEASUREMENT, never a rule.
  2. The unit is not universal. `InsertSheetMetalJog` wants the angle in DEGREES,
     inverting the whole project's convention; with radians it is a silent no-op. The
     gusset's draft, right beside it, wants radians.
  3. The FACE is half of every answer. The closed corner wants the flange's SIDE face
     (not the wall), the gusset the two faces on the bend's CONCAVE side (not the
     largest-area ones), the convert the INNER fixed face (`base_face` returns the outer
     one on an L). None of these was ever a missing argument.
  4. The verdict is GEOMETRIC. Half these methods return None (or True) whether or not
     they built anything, and `ModifyDefinition` returns True while ignoring the write.
     So the guards here assert on `sm_state`/`sm_compare` -- the bounding box, the face
     count, the volume, the tree -- and not on what COM said.

The executor holds only the loops (a bundle cannot iterate) and pure algebra. Which
interface, which attribute, which mark, which argument in which position and every unit
conversion is emitted from here.
"""

from __future__ import annotations

from .bundles import BundleBuilder as B
from .bundles import deg, mm
from .verbs_part import TPL_PART, VerbError, _req

# ── constants (from the generated makepy constants module, not from the docs) ─
BLIND = 0                                            # swEndConditions_e
# swSheetMetalReliefTypes_e
RELIEF = {"auto": 0, "rect": 1, "tear": 2, "obround": 3, "none": 4}
# swInsertEdgeFlangeXxx (bitmask of the edge flange's BooleanOptions)
EF_DEF_RADIUS, EF_DEF_RELIEF = 1, 128
# swFlangePositionType_e
POS = {"material_inside": 1, "material_outside": 2, "bend_outside": 3,
       "bend_centerline": 4}
SHARP_OUTER = 1                                      # swFlangeDimType_e
HEM = {"open": 0, "closed": 1, "teardrop": 2, "rolled": 3, "double": 4}
HEM_POS = {"inside": 0, "outside": 1}
CORNER = {"fillet": 0, "chamfer": 1}                 # break corner / corner trim
# InsertRefPlane with TWO references: the constraint codes and the marks the UI records
# for 'perpendicular to an edge, coincident with a vertex'. A DIFFERENT mark per
# reference is what pulls the method out of its no-op.
REFPLANE_EDGE, REFPLANE_VERTEX = 2, 4
REFPLANE_MARK_EDGE, REFPLANE_MARK_VERTEX = 0, 1
FLAT_KEEP_BENDS, FLAT_REMOVE_BENDS = 0, 1            # swExportFlatPatternOption_e
# swBendAllowanceTypes_e: 2 = K-factor, 1 = the allowance comes from a .btl table
CBA_KFACTOR, CBA_BEND_TABLE = 2, 1
# unfold/fold: the fixed face at one mark, each bend at the other
MARK_FIXED, MARK_BEND = 1, 2
MARK_CLOSED_CORNER = 1        # the mark the UI records; 0 also works, 2 and 4 do not
MARK_SEGMENT = 1              # lofted bend: the SEGMENTS, at mark 1 (as "SKETCH": None)
MARK_GUSSET = 0

# GetTypeName2 of each feature this domain reads. They are addresses, not labels: the
# whole domain is unreachable without them, and `FeatureByPositionReverse(0)` -- the way
# every other domain finds "the feature I just made" -- is ALWAYS `Flat-Pattern1` here.
T_SHEET, T_FLAT, T_BASE = "SheetMetal", "FlatPattern", "SMBaseFlange"
T_SKETCH, T_BEND, T_EDGE_FLANGE = "ProfileFeature", "OneBend", "EdgeFlange"
T_CUTLIST, T_CORNER, T_CONVERT, T_REFPLANE = ("CutListFolder", "CornerFeat",
                                              "SolidToSheetMetal", "RefPlane")

# The bend features that accept an allowance of their OWN, measured one by one with a
# re-read AND geometric proof on the flat pattern. The sketched bend (`SM3dBend`) is out
# by MEASUREMENT: there `ModifyDefinition` returns False and the re-read reverts.
BENDS_WITH_ALLOWANCE = {
    "EdgeFlange": "IEdgeFlangeFeatureData",
    "SMMiteredFlange": "IMiterFlangeFeatureData",
    "Hem": "IHemFeatureData",
    "Jog": "IJogFeatureData",
}
# ...but the TABLE's reach is SMALLER than the K-factor's, and the difference is the
# JOG: there K obeys and the table does not (ModifyDefinition False, re-read reverted,
# flat pattern unmoved). Two lists, not one, because the difference is measured.
NO_BEND_TABLE = {"Jog"}

_NOT_SHEET = ("the active part is not sheet metal. Start it with sheet.new_sheet / "
              "sheet.base_flange, or turn an existing solid into one with "
              "sheet.convert_to_sheet.")


def _sheet_guard(b: B, verb: str) -> None:
    """Refuse EARLY, and say which of the three ways in applies."""
    ok = b.helper("sm_state", key="has_type", type=T_SHEET)
    b.guard(ok, "truthy", f"{verb}: {_NOT_SHEET}")


def _last_sketch(b: B) -> str:
    """The name of the LAST sketch, ignoring the flat pattern.

    In a sheet metal part `FeatureByPositionReverse(0)` -- which every other domain uses
    for "the thing I just made" -- ALWAYS returns `Flat-Pattern1`: it sits rolled at the
    end of the tree. A verb that trusted it would lie on every sheet metal part, and the
    lie is silent, because the name does exist and the chaining appears to work."""
    names = b.helper("sm_names", type=T_SKETCH)
    sk = b.index(names, -1)
    b.guard(sk, "nonempty", "the part has no sketch to use (draw one first).")
    return sk


def _last_feature(b: B) -> str:
    """The name of the last CONSTRUCTION feature -- the flat pattern excluded, for the
    same reason as `_last_sketch`. Half the methods here return None even when they
    build, so this is how those verbs answer what they made."""
    names = b.helper("sm_names", exclude=T_FLAT)
    return b.index(names, -1)


def _select_sketch(b: B, name: str) -> None:
    b.call("doc", "ClearSelection2", True)
    ok = b.call("ext", "SelectByID2", name, "SKETCH", 0, 0, 0, False, 0, None, 0)
    b.guard(ok, "truthy", "could not select the sketch")


def _thickness_ref(b: B, scale: float) -> str:
    """The sheet thickness read at RUN TIME (`scale`: 1000 for mm, 1 for meters).

    Not every sheet metal part has a BASE flange -- one born from a lofted bend has only
    `SheetMetal` + `LoftedBend` -- so the reading falls back to the feature that exists
    on ANY sheet metal part."""
    got = b.helper(
        "sm_modify",
        alternatives=[{"type": T_BASE, "interface": "IBaseFlangeFeatureData"},
                      {"type": T_SHEET, "interface": "ISheetMetalFeatureData"}],
        read={"t": ["Thickness", scale]},
        missing_msg=_NOT_SHEET)
    return b.index(got, "t")


def _face_ref(b: B, p: dict) -> str:
    """The face the verb works from: the one given, or the LARGEST-AREA planar one.

    'The highest face' does NOT work for chaining flanges: after the first 20 mm flange
    the largest Z is the flange's own top, and the next flange is born on top of the
    first (the box came out 42 mm tall instead of 22)."""
    face = p.get("face")
    if face:
        return face
    ref = b.helper("sm_base_face", body=p.get("body"))
    b.guard(ref, "not_null", "the part has no planar face (is there a solid?)")
    return ref


# ── document / construction ──────────────────────────────────────────────────
def _base_flange_ops(b: B, p: dict, sketch: str) -> str:
    """The base flange itself, once a closed sketch is selected.

    `InsertSheetMetalBaseFlange2` (19 args, the NEWEST version -- the one intuition picks)
    is a SILENT no-op on SW 2017: it returns None and creates no body at all, in every
    argument combination measured. The 16-arg v1 builds AND returns the Feature."""
    _select_sketch(b, sketch)
    feat = b.call("fm", "InsertSheetMetalBaseFlange",
                  mm(_req(p, "thickness_mm")), bool(p.get("reverse", False)),
                  mm(p.get("radius_mm", 3.0)),
                  mm(10), 0.0, False, BLIND, BLIND, 0, None,
                  True, RELIEF.get(str(p.get("relief", "rect")), 1),
                  0.0, 0.0, float(p.get("relief_ratio", 0.5)), True,
                  cast="IFeature")
    b.guard(feat, "not_null",
            "the base flange created nothing. The sketch has to be CLOSED, and the part "
            "must not already be sheet metal.")
    b.call("doc", "ClearSelection2", True)
    bodies = b.helper("sm_state", key="body_count")
    b.guard(bodies, "gte", "the base flange ran and left no solid body "
                           "(is the sketch closed?)", value=1)
    return b.get(feat, "Name")


def base_flange(p: dict) -> dict:
    """Turn a CLOSED SKETCH into the base flange (the part's first solid)."""
    b = B("sheet.base_flange")
    sketch = str(p.get("sketch_name") or "") or _last_sketch(b)
    return b.build(_base_flange_ops(b, p, sketch), p)


def new_sheet(p: dict) -> dict:
    """NEW part + a w x h rectangle on `plane` + the base flange -- the way in."""
    b = B("sheet.new_sheet")
    tpl = b.call("app", "GetUserPreferenceStringValue", TPL_PART)
    b.guard(tpl, "nonempty", "no default part template configured in SolidWorks")
    b.call("app", "NewDocument", tpl, 0, 0, 0)
    real = b.helper("resolve_plane", plane=str(p.get("plane", "Front")))
    ok = b.call("ext", "SelectByID2", real, "PLANE", 0, 0, 0, False, 0, None, 0)
    b.guard(ok, "truthy", f"could not select plane {p.get('plane', 'Front')!r}")
    b.call("sm", "InsertSketch", True)
    b.call("sm", "CreateCornerRectangle", 0.0, 0.0, 0.0,
           mm(_req(p, "w_mm")), mm(_req(p, "h_mm")), 0.0)
    b.call("doc", "ClearSelection2", True)
    b.call("sm", "InsertSketch", True)
    # read HERE, and not only inside `_base_flange_ops`, so they show up in the verb's
    # schema: the base flange's parameters are `new_sheet`'s too, and a parameter absent
    # from the schema is a parameter the model does not know it can send
    q = dict(p)
    q["thickness_mm"] = float(p.get("thickness_mm", 2.0))
    q["radius_mm"] = float(p.get("radius_mm", 3.0))
    q["reverse"] = bool(p.get("reverse", False))
    q["relief"] = str(p.get("relief", "rect"))
    q["relief_ratio"] = float(p.get("relief_ratio", 0.5))
    return b.build(_base_flange_ops(b, q, _last_sketch(b)), p)


def convert_to_sheet(p: dict) -> dict:
    """Turn an ordinary SOLID (an L, a U, a shelled box) into a SHEET METAL part.

    With no arguments it works it out: the bend edges are the CONCAVE sharp corners and
    the fixed face is the LARGER of the two adjacent to the first of them.

    FOUR things have to be right at once, and the earlier rounds never crossed the first
    two: (1) the method is `InsertConvertToSheetMetal2` -- the 7-arg v1 is a no-op as
    soon as a 2nd entity is selected, and it was v1 that every variant with an edge used;
    (2) a bend EDGE has to be selected -- with the fixed face alone the method marks the
    part as sheet metal and DESTROYS everything but that face's wall; (3) the fixed face
    has to be the CONCAVE one, and the largest-area face of an L is the OUTER one;
    (4) the edges go in as OBJECTS -- by coordinate the 2nd one is silently refused.
    The method returns False even when it builds, so the verdict is the bounding box."""
    b = B("sheet.convert_to_sheet")
    already = b.helper("sm_state", key="has_type", type=T_SHEET)
    b.guard(already, "equals", "the active part is ALREADY sheet metal.", value=False)
    box0 = b.helper("sm_state", key="bbox_mm")

    edges, fixed = p.get("bend_edges"), p.get("fixed_face")
    if edges is None or fixed is None:
        found = b.helper("sm_sharp_bend_edges", min_mm=float(p.get("min_mm", 1.0)),
                         fixed="larger", body=p.get("body"))
        auto_edges = b.index(found, "edges")
        b.guard(auto_edges, "nonempty",
                "no CONCAVE sharp corner found -- pass bend_edges explicitly. A solid "
                "with no inner corner has nothing to bend.")
        edges = edges if edges is not None else auto_edges
        if fixed is None:
            fixed = b.index(found, "fixed_face")
            b.guard(fixed, "not_null",
                    "could not work out the fixed face -- pass fixed_face (it is the "
                    "CONCAVE face, the inner one).")

    b.helper("select", entities=[fixed, edges])
    b.call("fm", "InsertConvertToSheetMetal2",
           mm(p.get("thickness_mm", 2.0)), False, False,
           mm(p.get("radius_mm", 3.0)), mm(p.get("gap_mm", 2.0)),
           RELIEF.get(str(p.get("relief", "auto")), 0),
           float(p.get("relief_ratio", 0.5)), 0, 0.5, bool(p.get("keep_body", False)))
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")

    made = b.helper("sm_state", key="has_type", type=T_CONVERT)
    b.guard(made, "truthy",
            "the convert created no sheet metal. Is the fixed face the CONCAVE (inner) "
            "one? Is there a sharp corner? -- sheet.sharp_bend_edges lists the "
            "candidates.")
    same = b.helper("sm_compare", before=box0, key="bbox_mm", mode="same",
                    tol=float(p.get("tol_mm", 0.15)))
    b.guard(same, "truthy",
            "the convert ran but CHANGED the geometry -- typically the fixed face was "
            "the OUTER one (only its wall survives) or a bend edge was missing.")
    feat = b.helper("sm_feature", type=T_CONVERT, which="last")
    return b.build(b.get(feat, "Name"), p)


# ── flanges ───────────────────────────────────────────────────────────────────
def _edge_flange_ops(b: B, p: dict, edge: str, face: str) -> str:
    """One edge flange. TWO measured things, neither of them in the method's signature.

    `InsertSketchForEdgeFlange` creates an EMPTY sketch: it is not the distance parameter
    that defines the flange, it is the CLOSED RECTANGLE drawn in that sketch -- draw
    nothing and SW still hands back an `Edge-Flange1`, with no error and no geometry. And
    the rectangle has to sit on the side of the plane OPPOSITE the material, whose sign
    comes from the FACE's outward normal (to bend downward, pass the sheet's BOTTOM
    face). Both live in the `sm_flange_sketch` primitive, which gets the numbers here."""
    angle = float(p.get("angle_deg", 90.0))
    sk = b.helper("sm_flange_sketch", edge=edge, face=face,
                  length=mm(_req(p, "length_mm")), margin=mm(p.get("margin_mm", 0.0)),
                  angle=deg(angle))
    feat = b.call("fm", "InsertSheetMetalEdgeFlange",
                  edge, sk, EF_DEF_RADIUS | EF_DEF_RELIEF, deg(angle),
                  mm(p.get("radius_mm", 3.0)),
                  POS.get(str(p.get("position", "material_inside")), 1),
                  0.0, RELIEF["rect"], 0.5, 0.0, 0.0, SHARP_OUTER, None,
                  cast="IFeature")
    b.guard(feat, "not_null",
            "the edge flange was refused by SW. Either the profile fell on the material "
            "side (pass the OPPOSITE face) or that edge already has a bend -- "
            "sheet.free_edges lists the ones still free.")
    b.call("doc", "EditRebuild3")
    return b.get(feat, "Name")


def edge_flange(p: dict) -> dict:
    """Flange bent up from an EDGE of the sheet (the edge comes from sheet.free_edges)."""
    b = B("sheet.edge_flange")
    return b.build(_edge_flange_ops(b, p, _req(p, "edge"), _face_ref(b, p)), p)


def box_flanges(p: dict) -> dict:
    """One flange per FREE edge of the base flange -- this is how a sheet becomes a BOX.

    The face and the edges are RE-FETCHED for each flange on purpose: a stored IEdge dies
    on the next rebuild, and the base face changes identity with every feature."""
    sides = int(p.get("sides", 4))
    if not 1 <= sides <= 12:
        raise VerbError(f"sides={sides} out of range (1..12)")
    b = B("sheet.box_flanges")
    made = []
    for i in range(sides):
        face = b.helper("sm_base_face", body=p.get("body"))
        b.guard(face, "not_null", "the part has no planar face (is there a solid?)")
        free = b.helper("sm_free_edges", face=face,
                        min_mm=float(p.get("min_mm", 10.0)))
        edge = b.index(free, 0)
        b.guard(edge, "not_null",
                f"side {i + 1} of {sides} has no free edge left. Call it again with "
                f"sides={i} (that is how many this face takes), or use sheet.edge_flange "
                f"one edge at a time.")
        made.append(_edge_flange_ops(b, p, edge, face))
    return b.build(made, p)


def miter_flange(p: dict) -> dict:
    """MITER flange: the flange whose PROFILE you control, coming off the edge.

    Unlike `edge_flange`, where the profile is the rectangle SW asks for, here it is a
    line on a plane PERPENDICULAR to the edge, and the flange length is that line's
    length. What used to make this "return False" was the profile not TOUCHING the tip of
    the edge -- two millimetres of gap and SW refuses in silence.

    On an OBLIQUE edge the perpendicular plane is built here, and it is the one place
    `InsertRefPlane` with two references works: a DIFFERENT MARK per reference (the edge
    at 0, the vertex at 1). With the same mark for both it is a no-op, or it gives a
    plane merely parallel to the reference one. Selecting TWO edges does not propagate --
    one call per edge."""
    b = B("sheet.miter_flange")
    edge = _req(p, "edge")
    face = _face_ref(b, p)
    plane = str(p.get("plane_name") or "")
    if not plane:
        vertex = b.helper("sm_edge_vertex", edge=edge, end=int(p.get("end", 0)))
        b.guard(vertex, "not_null",
                "that edge has no vertex (is it closed?) -- pass plane_name with a plane "
                "already perpendicular to it.")
        n0 = b.helper("sm_state", key="type_count", type=T_REFPLANE)
        b.helper("select", entities=[edge, vertex],
                 marks=[REFPLANE_MARK_EDGE, REFPLANE_MARK_VERTEX])
        b.call("fm", "InsertRefPlane", REFPLANE_EDGE, 0, REFPLANE_VERTEX, 0, 0, 0)
        b.call("doc", "ClearSelection2", True)
        b.call("doc", "EditRebuild3")
        grew = b.helper("sm_compare", before=n0, key="type_count", type=T_REFPLANE,
                        mode="grew")
        b.guard(grew, "truthy",
                "the plane perpendicular to the edge was not created (is the edge "
                "straight? does it have a vertex?)")
        made = b.helper("sm_feature", type=T_REFPLANE, which="last")
        plane = b.get(made, "Name")

    sk = b.helper("sm_miter_profile", edge=edge, face=face,
                  length=mm(_req(p, "length_mm")), plane=plane)
    b.guard(sk, "nonempty", "the miter profile sketch was not created.")
    _select_sketch(b, sk)
    b.helper("select", entities=[edge], clear=False)
    feat = b.call("fm", "InsertSheetMetalMiterFlange",
                  True, mm(p.get("radius_mm", 3.0)), mm(p.get("gap_mm", 0.5)),
                  True, True, 0.5, 0.0, 0.0, RELIEF["rect"], False,
                  POS.get(str(p.get("position", "material_inside")), 1), 0.0, 0.0, None,
                  cast="IFeature")
    b.guard(feat, "not_null",
            "the miter flange was refused. The profile has to TOUCH the tip of the edge "
            "and the plane has to be perpendicular to it.")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(b.get(feat, "Name"), p)


def _line_m(p: dict) -> list:
    """`line` = [[x1,y1,z1],[x2,y2,z2]] in mm in the MODEL -> meters."""
    line = _req(p, "line")
    if (not isinstance(line, (list, tuple)) or len(line) != 2
            or any(not isinstance(pt, (list, tuple)) or len(pt) != 3 for pt in line)):
        raise VerbError(
            "`line` is TWO points of THREE coordinates each, in mm, in the MODEL system: "
            "[[x1,y1,z1],[x2,y2,z2]]. The line has to cross the whole face.")
    return [[mm(v) for v in pt] for pt in line]


def sketched_bend(p: dict) -> dict:
    """SKETCHED bend: bends the sheet along a line drawn ON it -- the bend that does not
    come from an edge (a U channel, a step, a flange in the middle of the sheet).

    `line` in mm in the MODEL system; the side that stays PUT is the `face`'s side. The
    `CreateDefinition(swFmSketchBend)` path returns None -- which is where the old
    verdict of "impossible" came from -- but `InsertSheetMetal3dBend` builds.
    `position='bend_centerline'` is refused here: it was MEASURED returning a feature and
    bending nothing."""
    position = str(p.get("position", "bend_outside"))
    if position == "bend_centerline":
        raise VerbError(
            "position='bend_centerline' is a MEASURED no-op in InsertSheetMetal3dBend "
            "(it returns a feature and does not bend). Use 'bend_outside', "
            "'material_inside' or 'material_outside'.")
    b = B("sheet.sketched_bend")
    _sheet_guard(b, "sheet.sketched_bend")
    face = _face_ref(b, p)
    # the point is taken BEFORE the sketch exists, and the face is re-selected by
    # COORDINATE afterwards: the IFace2 from before the sketch may no longer be valid,
    # and a border point would resolve to the NEIGHBOURING face
    point = b.helper("sm_face_point", face=face)
    if p.get("line") is not None:
        sk = b.helper("sm_sketch_lines", face=face, lines=[_line_m(p)])
        b.guard(sk, "nonempty", "the bend line sketch was not created.")
    else:
        sk = str(p.get("sketch_name") or "") or _last_sketch(b)

    _select_sketch(b, sk)
    ok = b.call("ext", "SelectByID2", "", "FACE",
                b.index(point, 0), b.index(point, 1), b.index(point, 2),
                True, 0, None, 0)
    b.guard(ok, "truthy", "could not select the bend's fixed face.")
    feat = b.call("fm", "InsertSheetMetal3dBend",
                  deg(float(p.get("angle_deg", 90.0))), False,
                  mm(p.get("radius_mm", 3.0)), bool(p.get("flip", False)),
                  POS.get(position, 3), None, cast="IFeature")
    b.guard(feat, "not_null",
            "the sketched bend was refused. Does the line cross the whole face? Is the "
            "face part of the sheet?")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(b.get(feat, "Name"), p)


def jog(p: dict) -> dict:
    """JOG (step): two bends in one go, offsetting half the sheet -- the Z step.

    TWO measured gotchas, and the first one inverts the whole project's convention:
    **`InsertSheetMetalJog` wants the angle in DEGREES**. With radians it is a silent
    no-op (18 variants were all that one failure). And the point that re-selects the
    fixed face must NOT land ON TOP of the line: there SW cannot tell which half to hold
    and rotates half the sheet 90 degrees instead of stepping it, with no error at all --
    which is why the point is pushed to one SIDE of the line (`fixed_at` overrides it)."""
    b = B("sheet.jog")
    _sheet_guard(b, "sheet.jog")
    face = _face_ref(b, p)
    if p.get("fixed_at") is not None:
        at = p["fixed_at"]
        if not isinstance(at, (list, tuple)) or len(at) != 3:
            raise VerbError("`fixed_at` is a point [x,y,z] in mm on the side that stays put.")
        point = [mm(v) for v in at]
    elif p.get("line") is not None:
        point = b.helper("sm_face_point", face=face, line=_line_m(p),
                         fraction=float(p.get("fraction", 0.25)))
    else:
        point = b.helper("sm_face_point", face=face)

    if p.get("line") is not None:
        sketch = b.helper("sm_sketch_lines", face=face, lines=[_line_m(p)])
        b.guard(sketch, "nonempty", "the jog line sketch was not created.")
    else:
        sketch = str(p.get("sketch_name") or "") or _last_sketch(b)

    box0 = b.helper("sm_state", key="bbox_mm")
    b.call("doc", "ClearSelection2", True)
    seg = b.format("{}@{}", str(p.get("segment", "Line1")), sketch)
    ok = b.call("ext", "SelectByID2", seg, "EXTSKETCHSEGMENT", 0, 0, 0, False, 0, None, 0)
    b.guard(ok, "truthy",
            "could not select the jog's line segment (does the sketch have a line? is "
            "`segment` its name -- 'Line1' by default?)")
    ok2 = b.call("ext", "SelectByID2", "", "FACE",
                 point[0] if isinstance(point, list) else b.index(point, 0),
                 point[1] if isinstance(point, list) else b.index(point, 1),
                 point[2] if isinstance(point, list) else b.index(point, 2),
                 True, 0, None, 0)
    b.guard(ok2, "truthy", "could not select the jog's fixed face.")
    # ANGLE IN DEGREES -- see the docstring. Do NOT wrap it in deg().
    b.call("doc", "InsertSheetMetalJog",
           float(p.get("angle_deg", 90.0)), mm(p.get("radius_mm", 3.0)),
           mm(p.get("offset_mm", 10.0)), bool(p.get("flip", True)),
           bool(p.get("fix_projected_length", True)),
           int(p.get("dim_position", 1)), int(p.get("bend_position", 0)))
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    moved = b.helper("sm_compare", before=box0, key="bbox_mm", mode="changed")
    b.guard(moved, "truthy",
            "the jog changed no geometry. Is the fixed face the SAME one the sketch was "
            "created on? Does the line cross the whole face?")
    return b.build(_last_feature(b), p)


def hem(p: dict) -> dict:
    """Hem on an edge: closes the sharp cut and stiffens the sheet.
    kind: 'closed' | 'open' | 'teardrop' | 'rolled' | 'double'."""
    b = B("sheet.hem")
    b.helper("select", entities=[_req(p, "edge")])
    feat = b.call("fm", "InsertSheetMetalHem2",
                  HEM.get(str(p.get("kind", "closed")), 1),
                  HEM_POS.get(str(p.get("position", "inside")), 0),
                  bool(p.get("reverse", False)),
                  mm(p.get("length_mm", 5.0)), mm(p.get("gap_mm", 0.0)),
                  deg(float(p.get("angle_deg", 0.0))), mm(p.get("radius_mm", 1.0)),
                  0.0, None, True, RELIEF["rect"], 1, True, 0.5, 0.0, 0.0)
    b.guard(feat, "truthy", "the hem was refused by SW (is that a free edge of the sheet?)")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(_last_feature(b), p)


def break_corner(p: dict) -> dict:
    """Break corner (round or chamfer) on the selected faces/edges, with the part FOLDED.
    kind: 'fillet' (radius) | 'chamfer' (distance)."""
    b = B("sheet.break_corner")
    b.helper("select", entities=_req(p, "entities"))
    b.call("doc", "InsertSheetMetalBreakCorner",
           CORNER.get(str(p.get("kind", "fillet")), 0), mm(_req(p, "radius_mm")))
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(_last_feature(b), p)


def corner_trim(p: dict) -> dict:
    """CORNER TRIM of the FLAT PATTERN: relief at the corners of the flat sheet.

    It is the same button that, with the part FOLDED, is `sheet.break_corner` -- so it
    requires `sheet.flatten(True)` first. Three things had to be right at once:
    `InternalCornerFlag` has to be 0 (every earlier variant passed 1); the selection needs
    SEVERAL accumulated edges (with one, or with the 6 boundary edges of a single face,
    the method returns None); and the part has to be flattened by the VERB -- the
    `SetBendState(2)` the UI macro records does not flatten through COM."""
    b = B("sheet.corner_trim")
    _sheet_guard(b, "sheet.corner_trim")
    flat = b.helper("sm_state", key="unsuppressed", type=T_FLAT)
    b.guard(flat, "truthy",
            "sheet.corner_trim needs the part FLATTENED -- call sheet.flatten first. "
            "With the part folded the equivalent command is sheet.break_corner.")
    edges = p.get("edges")
    if edges is None:
        edges = b.helper("edges", body=p.get("body"))
        b.guard(edges, "nonempty", "the part has no edge to trim.")
    n = b.helper("select", entities=[edges])
    b.guard(n, "gte", "no edge was selected for the corner trim.", value=2)
    feat = b.call("fm", "InsertSheetMetalCornerTrim",
                  0, CORNER.get(str(p.get("kind", "chamfer")), 1),
                  mm(p.get("radius_mm", 5.0)), int(p.get("relief_type", 0)),
                  mm(p.get("relief_mm", 5.0)), cast="IFeature")
    b.guard(feat, "not_null",
            "the corner trim changed nothing (is there a corner left to trim? a SINGLE "
            "edge is always refused -- it wants several).")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(_last_feature(b), p)


def closed_corner(p: dict) -> dict:
    """Close the gap between two neighbouring flanges. With no argument, the `corner`-th
    open corner.

    `InsertSheetMetalClosedCorner` HAS no arguments -- it is 100% selection, and the 11
    earlier variants got the FACE wrong, not the mark: what works is ONE face, the
    flange's SIDE face (the little one looking into the corner gap), at a corner where
    TWO flanges actually meet. The two big flange walls do nothing, with any pair of
    marks. The method returns nothing even when it builds, and a corner already closed
    keeps its side faces, so the candidates are tried until the VOLUME grows."""
    b = B("sheet.closed_corner")
    _sheet_guard(b, "sheet.closed_corner")
    if p.get("face"):
        faces = [p["face"]]
    else:
        thick = _thickness_ref(b, 1000.0)
        found = b.helper("sm_open_corners", thickness_mm=thick,
                         max_gap_mm=float(p.get("max_gap_mm", 0.0)),
                         from_index=int(p.get("corner", 0)), body=p.get("body"))
        faces = b.index(found, "faces")
        b.guard(faces, "nonempty",
                "no OPEN corner found. Does the part have two NEIGHBOURING flanges? "
                "sheet.open_corners lists the candidates.")
    name = b.helper("sm_try_closed_corner", faces=faces, mark=MARK_CLOSED_CORNER,
                    type=T_CORNER)
    b.guard(name, "nonempty",
            "the closed corner closed nothing. The face has to be the SIDE face of a "
            "flange at a corner where ANOTHER flange meets it (the flange's big wall "
            "will not do), and that corner must not already be closed.")
    return b.build(name, p)


def gusset(p: dict) -> dict:
    """GUSSET (reinforcing rib) over a bend: stiffens the corner without welding.

    19 variants returned None and the cause was never the method version nor the arrays:
    it was the FACE. The two supports are the ones on the bend's CONCAVE side (the
    flange's inner wall and the base's inner face) -- the largest-area faces are the
    OUTER ones and will not do. With those two, mark 0, the 21-argument v1 builds.
    `draft` defaults to False because the `BDraft` the UI records is False and turning it
    on makes the method return None; the draft ANGLE goes in radians (unlike the jog)."""
    b = B("sheet.gusset")
    _sheet_guard(b, "sheet.gusset")
    faces = p.get("faces") or b.helper("sm_bend_faces", bend=int(p.get("bend", 0)),
                                       body=p.get("body"))
    thick_mm = float(p.get("thickness_mm", 0) or 0)
    thick = mm(thick_mm) if thick_mm > 0 else _thickness_ref(b, 1.0)
    depth = mm(float(p.get("depth_mm") or 10.0))
    inner = float(p.get("inner_fillet_mm", 0) or 0)
    outer = float(p.get("outer_fillet_mm", 0) or 0)
    edge_f = float(p.get("edge_fillet_mm", 0) or 0)

    faces0 = b.helper("sm_state", key="face_count")
    n = b.helper("select", entities=[faces], marks=[MARK_GUSSET])
    b.guard(n, "gte", "could not select the gusset's two support faces.", value=2)
    feat = b.call("fm", "InsertSheetMetalGussetFeature",
                  bool(p.get("use_offset", True)), mm(p.get("offset_mm", 10.0)),
                  False, 0,
                  depth, depth, False, depth, 0.0,
                  False, mm(p.get("width_mm", 5.0)), thick,
                  bool(p.get("draft", False)), deg(float(p.get("draft_deg", 5.0))),
                  inner > 0, mm(inner or 1.0), outer > 0, mm(outer or 1.0),
                  int(p.get("gusset_type", 0)), edge_f > 0, mm(edge_f or 1.0),
                  cast="IFeature")
    b.guard(feat, "not_null",
            "the gusset was refused. Either the support faces are not the ones on the "
            "bend's CONCAVE side (sheet.bend_faces returns those), or the rib does not "
            "FIT: offset + depth has to stay within the flange.")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    grew = b.helper("sm_compare", before=faces0, key="face_count", mode="grew")
    b.guard(grew, "truthy",
            "the gusset returned a feature and added no face -- it did not build.")
    return b.build(_last_feature(b), p)


def cross_break(p: dict) -> dict:
    """Cross break: a COSMETIC stiffening rib on a face. It enters the tree and the flat
    pattern, not the volume."""
    b = B("sheet.cross_break")
    b.helper("select", entities=[_face_ref(b, p)])
    feat = b.call("fm", "InsertCrossBreak", deg(float(p.get("angle_deg", 1.0))),
                  mm(p.get("radius_mm", 1.0)), cast="IFeature")
    b.guard(feat, "not_null", "the cross break was refused by SW.")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(b.get(feat, "Name"), p)


def rip(p: dict) -> dict:
    """Rip edges of a CLOSED solid -- the step that lets a welded/folded box be
    flattened. It runs on an ordinary solid, BEFORE it becomes sheet metal."""
    b = B("sheet.rip")
    b.helper("select", entities=_req(p, "edges"))
    b.call("doc", "InsertRip", mm(p.get("gap_mm", 0.5)))
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(_last_feature(b), p)


def lofted_bend(p: dict) -> dict:
    """TRANSITION sheet between two OPEN profiles -- the funnel/adapter.

    The profiles must be OPEN and have the SAME number of segments. What used to make
    this "impossible" was the SELECTION: with the two sketches selected as "SKETCH"
    `InsertSheetMetalLoftedBend` returns None, at any mark; selecting the SEGMENTS at
    mark 1 it builds. The resulting part has NO base flange, so set_thickness,
    gauge_table and set_bend_radius do not reach it -- only `sheet.thickness`, which
    falls back to the SheetMetal feature."""
    side = str(p.get("side", "inside"))
    if side not in ("inside", "outside"):
        raise VerbError("side must be 'inside' or 'outside'.")
    b = B("sheet.lofted_bend")
    a_name, b_name = str(p.get("sketch_a") or ""), str(p.get("sketch_b") or "")
    if a_name and b_name:
        sketches = [a_name, b_name]
    else:
        names = b.helper("sm_names", type=T_SKETCH)
        first = b.index(names, -2)
        second = b.index(names, -1)
        b.guard(first, "nonempty",
                "lofted_bend needs TWO profile sketches -- pass sketch_a and sketch_b, "
                "or leave the two profiles as the last sketches in the tree.")
        sketches = [first, second]
    b.helper("sm_select_segments", sketches=sketches, mark=MARK_SEGMENT)
    feat = b.call("fm", "InsertSheetMetalLoftedBend",
                  1 if side == "inside" else 2, mm(p.get("thickness_mm", 2.0)),
                  cast="IFeature")
    b.guard(feat, "not_null",
            "the lofted bend was refused. Are both profiles OPEN, with the SAME number "
            "of segments?")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(b.get(feat, "Name"), p)


def cut(p: dict) -> dict:
    """Cut the sheet with a sketch (hole, slot, cutout).

    It exists as its own verb because of two things that only bite in sheet metal:
    `part.cut` with no `sketch_name` takes `FeatureByPositionReverse(0)`, which here is
    ALWAYS `Flat-Pattern1`; and the DIRECTION -- a sketch on the top face cutting "through
    all" the default way goes AWAY from the material and SW refuses. To cut ACROSS a bend
    the pairing is sheet.unfold -> sheet.cut -> sheet.fold (the cut survives the fold)."""
    through_all = bool(p.get("through_all", True))
    depth = 0.0 if through_all else mm(_req(p, "depth_mm"))
    b = B("sheet.cut")
    name = str(p.get("sketch_name") or "") or _last_sketch(b)
    _select_sketch(b, name)
    feat = b.call("fm", "FeatureCut4",
                  True, False, bool(p.get("reverse", False)),
                  1 if through_all else BLIND, 0, depth, 0.01,
                  False, False, False, False, deg(0), deg(0),
                  False, False, False, False, False, True, True,
                  False, False, False, 0, 0.0, False, False, cast="IFeature")
    b.guard(feat, "not_null",
            "the cut failed (SW returned Nothing). In sheet metal this is almost always "
            "the DIRECTION: the sketch is on one face and the default direction goes away "
            "from the material -> repeat the call with reverse=True.")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(feat, "Name"), p)


# ── flat pattern ──────────────────────────────────────────────────────────────
def flatten(p: dict) -> dict:
    """Flatten the whole part (on=True) or fold it back. Returns whether it ended flat.

    `SetBendState(swSMBendStateFlattened)` -- the method whose very NAME says exactly
    this -- is a NO-OP on SW 2017: it returns 0, which means "no error", and the bounding
    box does not move a millimetre. `GetBendState` is no proof either (it answered 1 in
    one round and 2 in another, with the geometry intact in both). What works is to
    unsuppress `Flat-Pattern1`, which is born suppressed."""
    on = bool(p.get("on", True))
    b = B("sheet.flatten")
    fp = b.helper("sm_feature", type=T_FLAT)
    b.guard(fp, "not_null", f"sheet.flatten: {_NOT_SHEET}")
    name = b.get(fp, "Name")
    b.call("doc", "ClearSelection2", True)
    ok = b.call("ext", "SelectByID2", name, "BODYFEATURE", 0, 0, 0, False, 0, None, 0)
    b.guard(ok, "truthy", "could not select the flat pattern feature.")
    b.call("doc", "EditUnsuppress2" if on else "EditSuppress2")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(b.helper("sm_state", key="unsuppressed", type=T_FLAT), p)


def _unfold_ops(b: B, p: dict, method: str) -> str:
    names = p.get("bends")
    if names is None:
        names = b.helper("sm_names", type=T_BEND, nested=True)
        b.guard(names, "nonempty",
                "the part has no bend to unfold (a bend is a SUB-feature of the flange; "
                "sheet.bends lists them).")
    n = b.helper("sm_select_bends", fixed_face=p.get("fixed_face"), bends=names,
                 mark_fixed=MARK_FIXED, mark_bend=MARK_BEND)
    b.guard(n, "gte", "no bend was selected -- with an empty selection SW creates the "
                      "feature and unfolds nothing.", value=1)
    b.call("doc", method)
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return _last_feature(b)


def unfold(p: dict) -> dict:
    """Unfold SPECIFIC bends (default: all), keeping `fixed_face` put -- what lets a hole
    be cut ACROSS a bend (unfold -> cut -> fold).

    The selection goes by MARK: the fixed face at 1, each bend at 2, and the bend has to
    be found as a SUB-feature. With the wrong selection SW creates `UnFold1` and unfolds
    nothing, reporting no error."""
    b = B("sheet.unfold")
    return b.build(_unfold_ops(b, p, "InsertSheetMetalUnfold"), p)


def fold(p: dict) -> dict:
    """Fold back what `unfold` unfolded. It wants the SAME selection as the unfold."""
    b = B("sheet.fold")
    return b.build(_unfold_ops(b, p, "InsertSheetMetalFold"), p)


def flat_options(p: dict) -> dict:
    """Read (and optionally change) how the FLAT PATTERN is generated -- what ends up in
    the DXF: the fixed face, merging coplanar faces, simplifying bends, corner treatment.
    Called with no arguments it only reports."""
    b = B("sheet.flat_options")
    sets, keys = {}, ("merge", "simplify", "corner_treatment")
    attrs = {"merge": "MergeFace", "simplify": "SimplifyBends",
             "corner_treatment": "CornerTreatment"}
    for key in keys:
        if p.get(key) is not None:
            sets[attrs[key]] = bool(p[key])
    face = p.get("fixed_face")
    if face is not None:
        sets["FixedFace2"] = face
    got = b.helper("sm_modify", type=T_FLAT, interface="IFlatPatternFeatureData",
                   access_selections=face is not None, set=sets, modify=bool(sets),
                   require_modify=bool(sets),
                   refused_msg="ModifyDefinition refused the flat pattern.",
                   missing_msg=f"sheet.flat_options: {_NOT_SHEET}",
                   read={"merge": "MergeFace", "simplify": "SimplifyBends",
                         "corner_treatment": "CornerTreatment",
                         "corner_radius_mm": ["BreakCornerRadius", 1000.0]})
    return b.build(got, p)


# ── fabrication ──────────────────────────────────────────────────────────────
def export_flat(p: dict) -> dict:
    """Export the FLAT PATTERN to DXF or DWG -- the file that goes to the cutting machine.
    `remove_bends` leaves the bend lines out.

    `ExportFlatPatternView` requires the part SAVED to disk, and the way it fails is
    UNSTABLE: sometimes it returns False writing nothing, sometimes it opens a "Save As"
    box and waits for a human, stalling automation indefinitely (two modal windows hung
    for ~20 min in the measured round). So the path is checked BEFORE the API is called
    at all."""
    b = B("sheet.export_flat")
    path = str(_req(p, "path"))
    return b.build(b.helper("sm_export_flat", path=path,
                            option=(FLAT_REMOVE_BENDS if p.get("remove_bends")
                                    else FLAT_KEEP_BENDS)), p)


def cut_list(p: dict) -> dict:
    """The sheet's FABRICATION data, resolved: the flat pattern's length and width (the
    material rectangle to buy), thickness, number of bends, cut length, bend radius and
    allowance.

    `CustomPropertyManager.Get` hands back the FORMULA, not the value; `Get2` resolves it
    and `Get5`, the newest, resolves it EMPTY. On a MULTI-BODY part this refuses instead
    of reporting one body as if it were the part -- use sheet.cut_lists."""
    b = B("sheet.cut_list")
    got = b.helper("sm_cut_lists", type=T_CUTLIST, single=True,
                   multi_msg="MULTI-BODY part ({n} cut lists: {folders}) -- call "
                             "sheet.cut_lists and pick the body.")
    b.guard(got, "nonempty", f"sheet.cut_list: the part has no cut list. {_NOT_SHEET}")
    return b.build(b.index(got, 0), p)


def cut_lists(p: dict) -> dict:
    """The cut list of EACH body: a sheet metal part with N bodies has N folders, each
    with ITS OWN material rectangle."""
    b = B("sheet.cut_lists")
    got = b.helper("sm_cut_lists", type=T_CUTLIST)
    b.guard(got, "nonempty", f"sheet.cut_lists: the part has no cut list. {_NOT_SHEET}")
    return b.build({"$ref": got.lstrip("$")}, p)


# ── inspection (the "eyes") ──────────────────────────────────────────────────
def info(p: dict) -> dict:
    """The sheet metal part's state in ONE round trip: is it sheet metal, is it flat, the
    thickness, the bends, the bounding box, the last sketch and the last feature.

    The last two matter more here than anywhere else: `FeatureByPositionReverse(0)` is
    ALWAYS `Flat-Pattern1` on a sheet metal part, so "the sketch I just drew" has to be
    asked for by name."""
    b = B("sheet.info")
    is_sheet = b.helper("sm_state", key="has_type", type=T_SHEET)
    flat = b.helper("sm_state", key="unsuppressed", type=T_FLAT)
    bends = b.helper("sm_names", type=T_BEND, nested=True)
    sketches = b.helper("sm_names", type=T_SKETCH)
    feats = b.helper("sm_names", exclude=T_FLAT)
    box = b.helper("sm_state", key="bbox_mm")
    bodies = b.helper("sm_state", key="body_count")
    return b.build({"is_sheet": is_sheet, "is_flat": flat,
                    "bends": {"$ref": bends.lstrip("$")},
                    "bodies": bodies,
                    "last_sketch": b.index(sketches, -1),
                    "last_feature": b.index(feats, -1),
                    "bbox_mm": {"$ref": box.lstrip("$")}}, p)


def bends(p: dict) -> dict:
    """The names of the part's BENDS -- the input to unfold/fold.

    A bend does NOT live at the top of the tree: `EdgeBend1` is a SUB-feature of
    `Edge-Flange1`, so a sweep that only walks GetNextFeature never finds it."""
    b = B("sheet.bends")
    return b.build({"$ref": b.helper("sm_names", type=T_BEND,
                                     nested=True).lstrip("$")}, p)


def bend_info(p: dict) -> dict:
    """One row per BEND, with what the bench needs: angle, radius, direction, order,
    K-factor and allowance. READ-ONLY on purpose: writing to `IOneBendFeatureData` was
    measured to be a lie -- `ModifyDefinition` returns True and the geometry does not
    move. To change one bend, touch the PARENT feature (sheet.set_flange) or the global
    radius (sheet.set_bend_radius)."""
    b = B("sheet.bend_info")
    got = b.helper("sm_bend_info", type=T_BEND, interface="IOneBendFeatureData",
                   read={"angle_deg": ["BendAngle", 57.29577951308232],
                         "radius_mm": ["BendRadius", 1000.0],
                         "direction": "BendDirection", "down": "BendDown",
                         "order": "BendOrder", "k_factor": "KFactor",
                         "allowance": "BendAllowance",
                         "allowance_type": "BendAllowanceType"})
    return b.build({"$ref": got.lstrip("$")}, p)


def base_face(p: dict) -> dict:
    """The planar face of LARGEST AREA (a handle). On a sheet it is the base flange's
    face; on an ordinary L-shaped SOLID it is the OUTER one -- which is exactly the face
    `convert_to_sheet` refuses."""
    b = B("sheet.base_face")
    ref = b.helper("sm_base_face", body=p.get("body"))
    b.guard(ref, "not_null", "the part has no planar face (is there a solid?)")
    return b.build(B.handle(ref), p)


def free_edges(p: dict) -> dict:
    """The face's edges that do NOT yet have a bend, longest first (handles). Chain
    flanges with this instead of counting indices: the edge just used excludes itself
    from the next round, because it starts touching the bend's cylinder."""
    b = B("sheet.free_edges")
    ref = b.helper("sm_free_edges", face=p.get("face"),
                   min_mm=float(p.get("min_mm", 1.0)), body=p.get("body"))
    return b.build(B.handle(ref), p)


def sharp_bend_edges(p: dict) -> dict:
    """The CONCAVE sharp-corner edges of an ordinary solid -- the bend candidates for
    `convert_to_sheet`, plus the fixed face it would use. Concave = material on the INSIDE
    of the dihedral; the convex ones (the same profile's outer corner) do not work."""
    b = B("sheet.sharp_bend_edges")
    got = b.helper("sm_sharp_bend_edges", min_mm=float(p.get("min_mm", 1.0)),
                   fixed="larger", body=p.get("body"))
    return b.build({"edges": {"$handle": b.index(got, "edges").lstrip("$")},
                    "fixed_face": {"$handle": b.index(got, "fixed_face").lstrip("$")}}, p)


def open_corners(p: dict) -> dict:
    """Corner CANDIDATES: pairs of flange SIDE faces facing each other across a gap --
    what `closed_corner` closes. They are CANDIDATES, not "corners still open": closing
    one does not take it off the list, because the two side faces survive."""
    b = B("sheet.open_corners")
    thick = _thickness_ref(b, 1000.0)
    got = b.helper("sm_open_corners", thickness_mm=thick,
                   max_gap_mm=float(p.get("max_gap_mm", 0.0)), body=p.get("body"))
    return b.build({"a": {"$handle": b.index(got, "a").lstrip("$")},
                    "b": {"$handle": b.index(got, "b").lstrip("$")},
                    "gap_mm": {"$ref": b.index(got, "gap_mm").lstrip("$")}}, p)


def bend_faces(p: dict) -> dict:
    """The TWO planar faces forming a bend's CONCAVE corner (handles) -- a gusset's
    support. Found through the bend's SMALLEST-radius cylinder: the outer one is radius +
    thickness, and the faces around IT are the ones the gusset must not rest on."""
    b = B("sheet.bend_faces")
    ref = b.helper("sm_bend_faces", bend=int(p.get("bend", 0)), body=p.get("body"))
    return b.build(B.handle(ref), p)


def bbox_mm(p: dict) -> dict:
    """The bounding box in mm (dx, dy, dz) over ALL solid bodies -- how you tell, with no
    human eye, whether the part is bent (tall) or flat (thin)."""
    b = B("sheet.bbox_mm")
    return b.build({"$ref": b.helper("sm_state", key="bbox_mm").lstrip("$")}, p)


def sheet_bodies(p: dict) -> dict:
    """One record per solid BODY: {name, sheet, box_mm}. Everything else in this domain
    assumes ONE body -- this is what lets the caller find out that it is not the case
    before believing a number (`bodies()[0]` is NOT the first body created)."""
    b = B("sheet.sheet_bodies")
    return b.build({"$ref": b.helper("sm_sheet_bodies").lstrip("$")}, p)


def thickness(p: dict) -> dict:
    """The sheet thickness in mm. It falls back from the base flange to the SheetMetal
    feature, because a part born from a lofted bend has no base flange."""
    b = B("sheet.thickness")
    return b.build(_thickness_ref(b, 1000.0), p)


def bend_params(p: dict) -> dict:
    """The part's GLOBAL bend parameters: {radius_mm, k_factor}. READ-only through this
    path -- `ModifyDefinition` over `ISheetMetalFeatureData` returns True and leaves the
    radius alone; sheet.set_bend_radius is the path that really moves it."""
    b = B("sheet.bend_params")
    got = b.helper("sm_modify", type=T_SHEET, interface="ISheetMetalFeatureData",
                   missing_msg=f"sheet.bend_params: {_NOT_SHEET}",
                   read={"radius_mm": ["BendRadius", 1000.0], "k_factor": "KFactor"})
    return b.build(got, p)


# ── parametric ───────────────────────────────────────────────────────────────
def set_thickness(p: dict) -> dict:
    """Change the sheet thickness (the whole part rebuilds). Returns the new thickness.

    With a gauge table attached the geometry obeys anyway, but the model would keep
    saying "Gauge 5" while measuring something else -- so a hand-typed thickness declares
    itself an OVERRIDE."""
    value = float(_req(p, "thickness_mm"))
    b = B("sheet.set_thickness")
    b.helper("sm_modify", type=T_BASE, interface="IBaseFlangeFeatureData",
             set={"Thickness": mm(value)},
             set_if={"UseGaugeTable": {"OverrideThickness": True}},
             modify=True, require_modify=True,
             refused_msg="ModifyDefinition refused the thickness.",
             missing_msg="the part has no BASE flange -- set_thickness works through it. "
                         "A part born from sheet.lofted_bend has none.")
    now = _thickness_ref(b, 1000.0)
    b.guard(now, "near", f"SW kept a thickness other than the {value} mm asked for.",
            value=value, tol=1e-3)
    return b.build(now, p)


def set_bend_radius(p: dict) -> dict:
    """Change the part's GLOBAL bend radius (every bend on the default).

    Writing `BendRadius` and calling `ModifyDefinition` returns True and changes NOTHING.
    Only after `SetOverrideDefaultParameter(1)` does the radius take -- and the proof is
    geometric (the same part's flat length went from 83.28 to 81.14 mm). With the
    parameter at 0 it still has no effect."""
    value = float(_req(p, "radius_mm"))
    b = B("sheet.set_bend_radius")
    got = b.helper("sm_modify", type=T_SHEET, interface="ISheetMetalFeatureData",
                   pre_calls=[["SetOverrideDefaultParameter", 1]],
                   set={"BendRadius": mm(value)}, modify=True,
                   missing_msg=f"sheet.set_bend_radius: {_NOT_SHEET}",
                   read={"radius_mm": ["BendRadius", 1000.0]})
    now = b.index(got, "radius_mm")
    b.guard(now, "near",
            f"SW kept a bend radius other than the {value} mm asked for (ModifyDefinition "
            f"returns True even when it ignores the write -- the reading is the proof).",
            value=value, tol=1e-3)
    return b.build(now, p)


def set_flange(p: dict) -> dict:
    """Edit a flange that already EXISTS: angle, bend radius and/or K-FACTOR.

    `name` defaults to the last flange in the tree. This is the path where the edit
    really takes (the bounding box moves and the bend starts reporting the new radius),
    unlike writing to the bend sub-feature, which lies. The flange LENGTH is not here: it
    came from the profile rectangle, and writing `OffsetDistance` has no effect."""
    b = B("sheet.set_flange")
    sets, allowance = {}, None
    if p.get("angle_deg") is not None:
        sets["BendAngle"] = deg(float(p["angle_deg"]))
    if p.get("radius_mm") is not None:
        sets["UseDefaultBendRadius"] = False
        sets["BendRadius"] = mm(float(p["radius_mm"]))
    if p.get("k_factor") is not None:
        # without UseDefaultBendAllowance=False the K silently reverts to the default
        sets["UseDefaultBendAllowance"] = False
        allowance = {"Type": CBA_KFACTOR, "KFactor": float(p["k_factor"])}
    if not sets:
        raise VerbError("nothing to change: pass angle_deg, radius_mm and/or k_factor.")
    name = str(p.get("name") or "")
    got = b.helper("sm_modify",
                   name=name, type="" if name else T_EDGE_FLANGE, which="last",
                   interface="IEdgeFlangeFeatureData",
                   set=sets, allowance=allowance, modify=True, require_modify=True,
                   refused_msg="ModifyDefinition refused the flange.",
                   missing_msg="could not find the flange feature -- pass `name` (the "
                               "tree type of an edge flange is 'EdgeFlange').",
                   read={"angle_deg": ["BendAngle", 57.29577951308232],
                         "radius_mm": ["BendRadius", 1000.0],
                         "k_default": "UseDefaultBendAllowance"},
                   read_allowance={"k_factor": "KFactor"})
    return b.build(got, p)


def set_k_factor(p: dict) -> dict:
    """K-FACTOR of every bend the part reaches.

    Here the RADIUS rule is INVERTED: the global radius changes through the SheetMetal
    feature with SetOverrideDefaultParameter, and the K-factor through that same path
    returns True and changes NOTHING (8 combinations measured, the flat pattern frozen).
    What yields is the BEND. Geometric proof on a 100x60x2 sheet with a 25 flange:
    K=0.1 -> 82.03 mm, K=0.5 -> 83.28, K=0.9 -> 84.54.

    REACH: edge flange, MITER flange, hem and jog all obey. The SKETCHED bend does not --
    there ModifyDefinition returns False and the re-read reverts to the default."""
    k = float(_req(p, "k_factor"))
    b = B("sheet.set_k_factor")
    got = b.helper("sm_allowance", interfaces=BENDS_WITH_ALLOWANCE,
                   write={"Type": CBA_KFACTOR, "KFactor": k})
    names = b.index(got, "names")
    b.guard(names, "nonempty",
            "the part has no bend that accepts its own allowance. Reached: edge flange, "
            "miter flange, hem and jog; the SKETCHED bend is not (measured).")
    return b.build({"k_factor": k, "bends": {"$ref": names.lstrip("$")}}, p)


def set_bend_table(p: dict) -> dict:
    """Attach the BEND TABLE (`.btl`) to the part's bends -- the allowance now comes from
    the REAL MATERIAL's table instead of the K-factor. With no `path`, it only reports.

    `IModelDoc2.InsertBendTableOpen`, the method that gives the item its name, is OUT: it
    stalls automation on a modal. What works is the same target that solved the K-factor
    -- the BEND -- through `ICustomBendAllowance` with Type=1 + BendTableFile. Through the
    document there are 5 combinations that return True, re-read empty and move nothing.

    REACH: edge flange, miter flange and hem accept the table; the JOG does NOT, even
    though the same jog accepts a K-factor -- it comes back in `not_reached`, never in
    silence. FORMAT: it has to be `.btl`; the `.xls` files in SW's own table folder save
    without complaining and then break the flat pattern, so the flat pattern is checked
    after writing and the bends are put back on the default if it broke."""
    path = str(p.get("path", "") or "")
    b = B("sheet.set_bend_table")
    got = b.helper(
        "sm_allowance", interfaces=BENDS_WITH_ALLOWANCE, skip_types=sorted(NO_BEND_TABLE),
        write=({"Type": CBA_BEND_TABLE, "BendTableFile": path} if path else None),
        require_file=path or None, check_error_type=(T_FLAT if path else ""),
        error_msg=("the table broke the flat pattern (Flat-Pattern error {err}) -- the "
                   "bends were put back on the default allowance. Use a .btl that covers "
                   "this thickness and this radius; the .xls files in SolidWorks' table "
                   "folder do not work here."))
    if path:
        names = b.index(got, "names")
        b.guard(names, "nonempty",
                "the part has no bend that accepts a bend TABLE. Reached: edge flange, "
                "miter flange and hem; the jog and the sketched bend are not (measured) "
                "-- sheet.set_k_factor does reach the jog.")
    return b.build(got, p)


def gauge_table(p: dict) -> dict:
    """Read (no argument) or ATTACH (with `path`) the base flange's GAUGE table -- what
    ties the model to the user's real material: the valid thicknesses and radii start
    coming from it. SW's own samples live under
    `<SOLIDWORKS>\\lang\\<language>\\Sheet Metal Gauge Tables`."""
    path = str(p.get("path", "") or "")
    b = B("sheet.gauge_table")
    got = b.helper("sm_modify", type=T_BASE, interface="IBaseFlangeFeatureData",
                   set=({"UseGaugeTable": True, "GaugeTablePath": path} if path else {}),
                   modify=bool(path), require_modify=bool(path),
                   refused_msg="ModifyDefinition refused the gauge table.",
                   missing_msg="the part has no BASE flange -- the gauge table lives on "
                               "it. A part born from sheet.lofted_bend has none.",
                   read={"active": "UseGaugeTable", "path": "GaugeTablePath",
                         "thickness_mm": ["TableThickness", 1000.0],
                         "radius_mm": ["TableRadius", 1000.0],
                         "gauge": "ThicknessTableName",
                         "override_thickness": "OverrideThickness"},
                   read_calls={"thicknesses": ["GetTableThicknessesCount", []],
                               "gauges": ["GetTableThicknesses", []]})
    return b.build(got, p)


def gauge_radii(p: dict) -> dict:
    """The bend radii the TABLE allows for a gauge, in mm (default: the selected one).

    `GetTableRadii` requires the gauge NAME as an argument -- called without it, it raises
    'Type mismatch'."""
    b = B("sheet.gauge_radii")
    state = b.helper("sm_modify", type=T_BASE, interface="IBaseFlangeFeatureData",
                     missing_msg="the part has no base flange.",
                     read={"active": "UseGaugeTable", "gauge": "ThicknessTableName"})
    b.guard(b.index(state, "active"), "truthy",
            "the part has no gauge table attached -- call sheet.gauge_table with a path.")
    gauge = str(p.get("gauge", "") or "") or b.index(state, "gauge")
    got = b.helper("sm_modify", type=T_BASE, interface="IBaseFlangeFeatureData",
                   read_calls={"radii_mm": ["GetTableRadii", [gauge], 1000.0]})
    return b.build(b.index(got, "radii_mm"), p)


def set_gauge(p: dict) -> dict:
    """PICK a gauge from the attached table (e.g. 'Gauge 3'). Returns the resulting
    GEOMETRIC thickness in mm.

    The selector is `ThicknessTableName`, a NAME -- not `TableThickness`, which only
    REPORTS the current gauge's thickness and selects nothing when written to. The names
    come from the table ('Gauge 5', 'Gauge 4', 'Gauge 3' in SW's sample), they are not
    numbers."""
    gauge = str(_req(p, "gauge"))
    b = B("sheet.set_gauge")
    state = b.helper("sm_modify", type=T_BASE, interface="IBaseFlangeFeatureData",
                     missing_msg="the part has no base flange.",
                     read={"active": "UseGaugeTable"},
                     read_calls={"gauges": ["GetTableThicknesses", []]})
    b.guard(b.index(state, "active"), "truthy",
            "the part has no gauge table attached -- call sheet.gauge_table with a path.")
    b.guard(b.index(state, "gauges"), "contains",
            f"gauge '{gauge}' is not in the attached table (sheet.gauge_table lists the "
            f"ones that are).", value=gauge)
    b.helper("sm_modify", type=T_BASE, interface="IBaseFlangeFeatureData",
             set={"ThicknessTableName": gauge, "OverrideThickness": False},
             modify=True, require_modify=True,
             refused_msg="ModifyDefinition refused the gauge.")
    return b.build(_thickness_ref(b, 1000.0), p)


VERBS = {
    # construction
    "sheet.new_sheet": new_sheet, "sheet.base_flange": base_flange,
    "sheet.convert_to_sheet": convert_to_sheet,
    "sheet.edge_flange": edge_flange, "sheet.box_flanges": box_flanges,
    "sheet.miter_flange": miter_flange, "sheet.sketched_bend": sketched_bend,
    "sheet.jog": jog, "sheet.hem": hem, "sheet.break_corner": break_corner,
    "sheet.corner_trim": corner_trim, "sheet.closed_corner": closed_corner,
    "sheet.gusset": gusset, "sheet.cross_break": cross_break, "sheet.rip": rip,
    "sheet.lofted_bend": lofted_bend, "sheet.cut": cut,
    # flat pattern
    "sheet.flatten": flatten, "sheet.unfold": unfold, "sheet.fold": fold,
    "sheet.flat_options": flat_options,
    # fabrication
    "sheet.export_flat": export_flat, "sheet.cut_list": cut_list,
    "sheet.cut_lists": cut_lists,
    # inspection
    "sheet.info": info, "sheet.bends": bends, "sheet.bend_info": bend_info,
    "sheet.base_face": base_face, "sheet.free_edges": free_edges,
    "sheet.sharp_bend_edges": sharp_bend_edges, "sheet.open_corners": open_corners,
    "sheet.bend_faces": bend_faces, "sheet.bbox_mm": bbox_mm,
    "sheet.sheet_bodies": sheet_bodies, "sheet.thickness": thickness,
    "sheet.bend_params": bend_params,
    # parametric
    "sheet.set_thickness": set_thickness, "sheet.set_bend_radius": set_bend_radius,
    "sheet.set_flange": set_flange, "sheet.set_k_factor": set_k_factor,
    "sheet.set_bend_table": set_bend_table, "sheet.gauge_table": gauge_table,
    "sheet.gauge_radii": gauge_radii, "sheet.set_gauge": set_gauge,
}
