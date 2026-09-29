"""
verbs_part.py -- PART verbs compiled into bundles.

Port of `solidworks/sw_parts.py` + `sw_sketch.py` + `sw_inspect.py`. Each function
EMITS the COM operations instead of running them. What stays here is the asset: the
call order, the 23/27 arguments, the selection MARKS (direction=1, seed=4, mirror=2),
the guards that turn "returned Nothing" into a readable error.
"""

from __future__ import annotations

from math import radians, tan

from .bundles import BundleBuilder as B
from .bundles import deg, mm

# ── constantes (swEndConditions_e, swFeatureFilletType_e, ...) ────────────────
TPL_PART = 8
DOC_PART = 1
BLIND, THROUGH_ALL = 0, 1
SAVE_SILENT = 1
FILLET_SIMPLE, FILLET_PROPAGATE, FILLET_UNIFORM = 0, 1, 2
CHAMFER_ANGLE_DIST, CHAMFER_TANGENT_PROP = 1, 4
REFPLANE_DISTANCE = 8
DELETE_ABSORBED = 1          # swDelete_Absorbed: takes the sketch along with the feature
BODY_ADD, BODY_CUT, BODY_INTERSECT = 15903, 15902, 15901
DRAFT_NEUTRAL_PLANE = 0
REFPT = {"face_center": 4, "center_edge": 3, "intersection": 6, "sketch_point": 7}
TOL = {"none": 0, "basic": 1, "bilateral": 2, "limit": 3, "symmetric": 4,
       "fit": 8}
# swConstraintType_e -- the REAL values (validated in solidworks/sw_sketch.py).
# Do not invent them: the enum in the docs does not match what SW 2017 accepts.
RELATIONS = {
    "horizontal": 4, "vertical": 5, "tangent": 6, "parallel": 7,
    "perpendicular": 8, "coincident": 9, "concentric": 10, "symmetric": 11,
    "colinear": 27, "fixed": 17,
}
SKETCH_SHAPES = ("rect", "rectangle", "circle", "line", "centerline", "arc",
                 "point", "polygon", "slot", "spline")


class VerbError(ValueError):
    """An invalid parameter -- becomes a 400, not a 500."""


def _no_face(verb: str, face: str) -> str:
    """A 'face not found' message that TEACHES the way out of the hole.

    Measured 2026-08-15 on a composite part: asked for a stepped shaft, the model
    called `part_cylinder(face='top')` SEVENTEEN times in a row and always got
    "could not find the face 'top' (does the part already have a solid?)". The part DID
    did not exist was a face pointing at +Y, because a cylinder created by
    `part_cylinder` grows along +Z and ITS top face is called 'front' in our
    vocabulary. The old message sent the model to investigate the wrong thing, and it
    never got anywhere. This one states the rule and the next step (the HANDOFF lesson:
    every guard says what to do, not what broke).
    """
    return (f"{verb}: no planar face points to the '{face}' side. A face is named by "
            f"the GLOBAL AXIS, not by your idea of 'up': "
            f"front/back = +Z/-Z, top/bottom = +Y/-Y, right/left = +X/-X. "
            f"CAREFUL: part_cylinder and part_block grow along +Z, so the TOP face of "
            f"such a cylinder is 'front', not 'top'. "
            f"Call inspect_faces_info to see which faces actually exist and in which "
            f"coordinate range they sit.")


def _req(p: dict, key: str):
    if key not in p or p[key] is None:
        raise VerbError(f"parametro required ausente: '{key}'")
    return p[key]


def _ref(value):
    """Accepts a handle ('@h3') or a literal value coming from the caller."""
    return value


# ── documento ─────────────────────────────────────────────────────────────────
def new_part(p: dict) -> dict:
    b = B("part.new_part")
    tpl = b.call("app", "GetUserPreferenceStringValue", TPL_PART)
    b.guard(tpl, "nonempty", "no default part template configured in SolidWorks")
    b.call("app", "NewDocument", tpl, 0, 0, 0)
    return b.build(b.call("doc", "GetTitle"), p)


def open_part(p: dict) -> dict:
    """Open an existing part. Returns the title.

    All SIX arguments are mandatory, including the two out-params (Errors,
    Warnings): measured 2026-08-18 (SW 2017 rev 25.3), `OpenDoc6(path, kind, options,
    raises "Type mismatch" even under early binding -- the generated wrapper does NOT
    treat Errors/Warnings as pure output. With all six, the call returns the tuple
    (doc, errors, warnings). The verb was never exercised by the suites, which BUILD
    instead of opening; it showed up while photographing the saved goldens.
    """
    b = B("part.open_part")
    b.call("app", "OpenDoc6", str(_req(p, "path")), DOC_PART, 1, "", 0, 0)
    return b.build(b.call("doc", "GetTitle"), p)


def save(p: dict) -> dict:
    path = str(p.get("path", "") or "")
    b = B("part.save")
    if path:
        # SaveAs fails if the target is already open (or held by an open assembly)
        b.helper("close_other_doc", path=path)
        ok = b.call("doc", "SaveAs", path)
        b.guard(ok, "truthy",
                f"SaveAs failed: {path} (file open in another window? folder missing?)")
        return b.build(path, p)
    b.call("doc", "Save3", SAVE_SILENT)
    return b.build(b.call("doc", "GetPathName"), p)


def export(p: dict) -> dict:
    """Format inferred from the extension (.step/.x_t/.stl/.iges)."""
    path = str(_req(p, "path"))
    b = B("part.export")
    b.helper("close_other_doc", path=path)
    ok = b.call("doc", "SaveAs", path)
    b.guard(ok, "truthy", f"export failed: {path}")
    return b.build(path, p)


def rebuild(p: dict) -> dict:
    b = B("part.rebuild")
    return b.build(b.call("doc", "EditRebuild3"), p)


# ── sketch ────────────────────────────────────────────────────────────────────
def _open_sketch(b: B, p: dict) -> tuple[int, int]:
    """Open a sketch on a plane, on a face handle, or on a face by NAME.

    Returns `(su, sv)`, the sign of each axis of that face frame -- whoever draws
    afterwards MUST apply it (see `_uv`). With no named face it returns (1, 1).

    The name ('top', 'left'...) exists because it was the missing path: to put a boss
    ON TOP of the part you have to sketch ON the top face, and until now `face` only
    accepted a handle -- which the model does not have without inspecting first.
    Measured: it called `sketch_begin(face='top')`, nothing useful happened, and it
    fell back to the default 'Top' plane (which passes through the ORIGIN, not through
    the top of the part) -- and the material came out in the wrong place or detached.

    `face_point` breaks the tie when more than one face has that orientation.
    """
    face = p.get("face")
    # `.get` WITH a fallback on purpose: `face` accepts a name OR a handle, so testing
    # `face in _FACES` would make the schema generator declare a closed enum and FORBID
    # `@h3` -- with the description still saying "accepts a handle". It is the same trap
    # the material table already caused; the rule is in `schemas.py`: with a fallback and
    # normalization it becomes a suggestion, without a fallback the domain is closed (it
    orient = _FACES.get(str(face).lower(), ()) if isinstance(face, str) else ()
    if orient:
        axis, maior, _par, su, sv = orient
        alvo = b.helper("find_face_at", axis=axis, want_max=maior,
                        point_mm=p.get("face_point"), body=p.get("body"))
        b.guard(alvo, "not_null", _no_face("sketch", str(face)))
        b.call("doc", "ClearSelection2", True)
        b.helper("select", entities=[alvo])
        b.call("sm", "InsertSketch", True)
        return su, sv
    if face:
        b.call("doc", "ClearSelection2", True)
        b.helper("select", entities=[face])
    else:
        real = b.helper("resolve_plane", plane=str(p.get("plane", "Front")))
        ok = b.call("ext", "SelectByID2", real, "PLANE", 0, 0, 0, False, 0, None, 0)
        b.guard(ok, "truthy", f"could not select plane {p.get('plane')!r}")
    b.call("sm", "InsertSketch", True)
    return 1, 1


def _uv(p: dict, su: int, sv: int) -> dict:
    """A copy of `p` with the coordinates taken into the FACE's frame.

    A sketch opened on a face does NOT use the global system: the axis pair and their
    ORIENTATION change with the face. `hole_on` and `boss_on` already converted
    internally; `sketch`, `hole`, `block` and `cylinder` did NOT -- they selected the
    face and drew with global coordinates.

    It is the SAME defect the `_FACES` comment below describes: "asking for (10,10) on
    the 'top' face drew the circle at z=-10 -- OUTSIDE the material -- and the cut
    failed. Only `front` worked, by accident". The fix had been applied to two of the
    six verbs that open a sketch, and the other four were left behind (found
    2026-08-16, while investigating why the model only got stacking right on `front`).

    With no named face, `su/sv` are 1 and nothing changes.
    """
    if (su, sv) == (1, 1):
        return p
    q = dict(p)
    for cu, cv in (("x", "y"), ("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
        if cu in q:
            q[cu] = su * float(q[cu])
        if cv in q:
            q[cv] = sv * float(q[cv])
    for key in ("points", "positions"):
        if isinstance(q.get(key), list):
            for i, point in enumerate(q[key]):
                # A point with 3 values blew up with `ValueError: too many values to
                # unpack` right here, untreated -> a BARE HTTP 500 for the model.
                # The mistake is always the same: it repeats the coordinate of the FACE
                # ITSELF, which is precisely the one the pair does not carry. Measured
                # sibling of this path (`boss_on`'s `at`).
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise VerbError(
                        f"`{key}`[{i}] has {len(point) if isinstance(point, (list, tuple)) else 1} "
                        f"value(s). On a face each point is a PAIR of millimeters in "
                        f"THAT face's plane, measured from the model origin -- two "
                        f"numbers, not three: the third coordinate is the face itself.")
            q[key] = [[su * float(a), sv * float(c)] for a, c in q[key]]
    return q


def _draw(b: B, shape: str, p: dict) -> str:
    """Emit the shape creation; returns the ref of the segment(s).

    EVERY shape comes out CAST to the concrete interface. It is not cosmetic: the
    object the executor stores is the one that will be selected later, and re-casting
    gives a 'Type mismatch'. Since the return becomes a handle used in the NEXT 
    bundle, casting here is the only chance: after that the object is already stored.
    Without this, `sketch_dimension` over a rectangle side was impossible.
    """
    s = shape.lower()
    if s in ("rect", "rectangle"):
        return b.call("sm", "CreateCornerRectangle",
                      mm(_req(p, "x1")), mm(_req(p, "y1")), 0.0,
                      mm(_req(p, "x2")), mm(_req(p, "y2")), 0.0,
                      cast="ISketchSegment")
    if s == "circle":
        return b.call("sm", "CreateCircleByRadius",
                      mm(p.get("cx", 0.0)), mm(p.get("cy", 0.0)), 0.0, mm(_req(p, "r")),
                      cast="ISketchSegment")
    if s == "line":
        return b.call("sm", "CreateLine", mm(_req(p, "x1")), mm(_req(p, "y1")), 0.0,
                      mm(_req(p, "x2")), mm(_req(p, "y2")), 0.0, cast="ISketchSegment")
    if s == "centerline":
        return b.call("sm", "CreateCenterLine", mm(_req(p, "x1")), mm(_req(p, "y1")), 0.0,
                      mm(_req(p, "x2")), mm(_req(p, "y2")), 0.0, cast="ISketchSegment")
    if s == "arc":
        return b.call("sm", "CreateTangentArc", mm(_req(p, "x1")), mm(_req(p, "y1")), 0.0,
                      mm(_req(p, "x2")), mm(_req(p, "y2")), 0.0,
                      int(p.get("arc_type", 0)), cast="ISketchSegment")
    if s == "point":
        return b.call("sm", "CreatePoint", mm(_req(p, "x")), mm(_req(p, "y")), 0.0,
                      cast="ISketchPoint")
    if s == "polygon":
        cx, cy, r = p.get("cx", 0.0), p.get("cy", 0.0), _req(p, "r")
        return b.call("sm", "CreatePolygon", mm(cx), mm(cy), 0.0, mm(cx + r), mm(cy), 0.0,
                      int(p.get("sides", 6)), bool(p.get("inscribed", True)),
                      cast="ISketchSegment")
    if s == "slot":
        return b.call("sm", "CreateSketchSlot", 0, 0, mm(_req(p, "width")),
                      mm(_req(p, "x1")), mm(_req(p, "y1")), 0.0,
                      mm(_req(p, "x2")), mm(_req(p, "y2")), 0.0, 0, 0, 0, 0, False,
                      cast="ISketchSlot")
    if s == "spline":
        pts = _req(p, "points")
        flat = []
        for xy in pts:
            flat += [mm(xy[0]), mm(xy[1]), 0.0]
        return b.helper("spline", points=flat)
    raise VerbError(f"shape '{shape}' not supported ({', '.join(SKETCH_SHAPES)})")


def sketch(p: dict) -> dict:
    """A complete sketch: opens on the plane/face, draws ONE shape and closes.
    For several shapes in the same sketch, use sketch_begin/sketch_add/sketch_end."""
    shape = str(_req(p, "shape"))
    b = B("part.sketch")
    su, sv = _open_sketch(b, p)
    _draw(b, shape, _uv(p, su, sv))
    b.call("doc", "ClearSelection2", True)
    b.call("sm", "InsertSketch", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    b.guard(feat, "not_null", "the sketch did not show up in the tree")
    return b.build(b.get(feat, "Name"), p)


def sketch_begin(p: dict) -> dict:
    """Open a sketch and LEAVE IT OPEN (for several entities/relations/dimensions).

    KNOWN LIMIT: with a named `face`, the following `sketch_add` draws in the face
    RAW frame -- the `_uv` conversion does not reach here, because `sketch_add` comes
    in another bundle and the frame does not cross. To draw ONE shape on a face,
    prefer `part_sketch` (which converts), or the coarse verbs
    """
    b = B("part.sketch_begin")
    _open_sketch(b, p)
    return b.build(str(p.get("plane") or "face"), p)


def sketch_add(p: dict) -> dict:
    """Draw an entity in the OPEN sketch. Returns a handle (for a relation/dimension)."""
    shape = str(_req(p, "shape"))
    b = B("part.sketch_add")
    ref = _draw(b, shape, p)
    return b.build(B.handle(ref), p)


def sketch_end(p: dict) -> dict:
    b = B("part.sketch_end")
    b.call("doc", "ClearSelection2", True)
    b.call("sm", "InsertSketch", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    b.guard(feat, "not_null", "the sketch did not show up in the tree")
    return b.build(b.get(feat, "Name"), p)


def sketch_relation(p: dict) -> dict:
    """Geometric relation between entities of the ACTIVE sketch (handles from sketch_add)."""
    kind = str(_req(p, "kind")).lower()
    if kind not in RELATIONS:
        raise VerbError(f"relacao '{kind}' invalid. Opcoes: {sorted(RELATIONS)}")
    ents = _req(p, "entities")
    b = B("part.sketch_relation")
    ok = b.helper("relation", entities=ents, kind=RELATIONS[kind])
    b.guard(ok, "truthy", f"AddRelation({kind}) failed")
    return b.build(kind, p)


def sketch_dimension(p: dict) -> dict:
    """A parametric dimension on the ACTIVE sketch. `name` gives a STABLE name the loop drives.
    GOTCHA: Select4 on the CONCRETE interface (ISketchSegment/ISketchPoint)."""
    ents = _req(p, "entities")
    at = _req(p, "at")
    orient = str(p.get("orient", "aligned")).lower()
    placer = {"aligned": "AddDimension2", "horizontal": "AddHorizontalDimension2",
              "vertical": "AddVerticalDimension2"}.get(orient)
    if placer is None:
        raise VerbError(f"orient '{orient}' invalid (aligned|horizontal|vertical)")

    b = B("part.sketch_dimension")
    b.helper("select_sketch_entities", entities=ents)
    disp = b.call("doc", placer, mm(at[0]), mm(at[1]), 0.0, cast="IDisplayDimension")
    b.guard(disp, "not_null",
            "no dimension created (entities not selected / sketch closed?)")
    dim = b.call(disp, "GetDimension2", 0, cast="IDimension")
    b.call("doc", "ClearSelection2", True)
    if p.get("name"):
        b.set(dim, "Name", str(p["name"]))
    return b.build(b.get(dim, "FullName"), p)


# ── Solids ───────────────────────────────────────────────────────────────────
def _select_sketch(b: B, p: dict, mark: int = 0, append: bool = False) -> str:
    name = p.get("sketch_name") or ""
    if name:
        ref = str(name)
    else:
        feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
        b.guard(feat, "not_null", "there is no feature in the tree to use")
        ref = b.get(feat, "Name")
    if not append:
        b.call("doc", "ClearSelection2", True)
    sel = b.call("ext", "SelectByID2", ref, "SKETCH", 0, 0, 0, append, mark, None, 0)
    b.guard(sel, "truthy", "did not select the sketch")
    return ref


def extrude(p: dict) -> dict:
    kind = str(p.get("kind", "boss")).lower()
    if kind not in ("boss", "cut"):
        raise VerbError(f"kind '{kind}' invalid (boss|cut)")
    through_all = bool(p.get("through_all", False))
    depth = 0.0 if through_all else mm(_req(p, "depth_mm"))
    reverse = bool(p.get("reverse", False))
    merge = bool(p.get("merge", True))
    thin = float(p.get("thin_mm", 0) or 0)
    t1 = THROUGH_ALL if through_all else BLIND

    b = B("part.extrude")
    _select_sketch(b, p)
    if kind == "boss" and thin > 0:
        created = b.call("fm", "FeatureExtrusionThin2",
                         True, False, reverse, t1, 0, depth, 0.0,
                         False, False, False, False, deg(0), deg(0),
                         False, False, False, False, merge,
                         mm(thin), 0.0, 0.0, False, False, False, 0.0,
                         True, True, 0, 0.0, False, cast="IFeature")
    elif kind == "boss":
        created = b.call("fm", "FeatureExtrusion2",
                         True, False, reverse, t1, 0, depth, 0.01,
                         False, False, False, False, deg(1), deg(1),
                         False, False, False, False, merge, True, True, 0, 0, False,
                         cast="IFeature")
    else:
        created = b.call("fm", "FeatureCut4",
                         True, False, reverse, t1, 0, depth, 0.01,
                         False, False, False, False, deg(0), deg(0),
                         False, False, False, False, False, True, True,
                         False, False, False, 0, 0.0, False, False, cast="IFeature")
    b.guard(created, "not_null",
            f"extrude {kind} failed (SW returned Nothing)"
            + (". If it is a cut from a plane, the direction may be flipped -> "
               "repeat it with reverse=True." if kind == "cut" else ""))
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def revolve(p: dict) -> dict:
    """A closed profile + ONE centerline (the axis) in the same sketch."""
    kind = str(p.get("kind", "boss")).lower()
    angle = float(p.get("angle_deg", 360.0))
    b = B("part.revolve")
    _select_sketch(b, p)
    created = b.call("fm", "FeatureRevolve2",
                     True, True, False, kind == "cut", bool(p.get("reverse", False)),
                     False, 0, 0, deg(angle), 0.0, False, False, 0.0, 0.0,
                     0, 0.0, 0.0, True, True, True, cast="IFeature")
    b.guard(created, "not_null", f"revolve {kind} failed (invalid profile/axis?)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def sweep(p: dict) -> dict:
    """A profile (mark 1) along a path (mark 4)."""
    profile = str(_req(p, "profile_sketch"))
    path = str(_req(p, "path_sketch"))
    thin = float(p.get("thin_mm", 0) or 0)
    b = B("part.sweep")
    b.call("doc", "ClearSelection2", True)
    ok = b.call("ext", "SelectByID2", profile, "SKETCH", 0, 0, 0, False, 1, None, 0)
    b.guard(ok, "truthy", f"sweep: did not select the profile '{profile}'")
    b.call("ext", "SelectByID2", path, "SKETCH", 0, 0, 0, True, 4, None, 0)
    created = b.call("fm", "InsertProtrusionSwept3",
                     False, False, 0, False, False, 0, 0, thin > 0, mm(thin), 0.0, 0, 0,
                     bool(p.get("merge", True)), True, True, 0.0, True, cast="IFeature")
    b.guard(created, "not_null",
            "sweep failed (invalid or disconnected profile/path)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def loft(p: dict) -> dict:
    """Loft between 2+ profiles (all with mark 1, in the given order)."""
    sketches = _req(p, "profile_sketches")
    if len(sketches) < 2:
        raise VerbError("loft requer 2+ perfis")
    b = B("part.loft")
    b.call("doc", "ClearSelection2", True)
    for i, sk in enumerate(sketches):
        ok = b.call("ext", "SelectByID2", str(sk), "SKETCH", 0, 0, 0, i > 0, 1, None, 0)
        b.guard(ok, "truthy", f"loft: did not select the profile '{sk}'")
    created = b.call("fm", "InsertProtrusionBlend2",
                     bool(p.get("closed", False)), False, False, 0.1, 0, 0, 0.0, 0.0,
                     False, False, False, 0.0, 0.0, 0, bool(p.get("merge", True)),
                     True, True, 0, cast="IFeature")
    b.guard(created, "not_null", "loft failed (profiles not loftable / invalid order)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def fillet(p: dict) -> dict:
    b = B("part.fillet")
    edges = p.get("edges")
    if edges:
        b.helper("select", entities=edges)
    created = b.call("fm", "FeatureFillet3",
                     FILLET_PROPAGATE | FILLET_UNIFORM, mm(_req(p, "radius_mm")), 0, 0,
                     FILLET_SIMPLE, 0, 0, None, None, None, None, None, None, None,
                     cast="IFeature")
    b.guard(created, "not_null", "fillet failed (invalid edges / radius too large?)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def chamfer(p: dict) -> dict:
    b = B("part.chamfer")
    edges = p.get("edges")
    if edges:
        b.helper("select", entities=edges)
    created = b.call("fm", "InsertFeatureChamfer",
                     CHAMFER_TANGENT_PROP, CHAMFER_ANGLE_DIST, mm(_req(p, "dist_mm")),
                     deg(p.get("angle_deg", 45.0)), 0.0, 0.0, 0.0, 0.0, cast="IFeature")
    b.guard(created, "not_null", "chamfer failed (returned None)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


# ── Holes ─────────────────────────────────────────────────────────────────────
def hole(p: dict) -> dict:
    """Simple hole(s): circles at the positions + ONE cut. For a standard fastener
    (HoleWizard) use the escape hatch -- see recipe `part-hole`."""
    positions = _req(p, "positions")
    d = float(_req(p, "diameter_mm"))
    through_all = bool(p.get("through_all", True))
    b = B("part.hole")
    su, sv = _open_sketch(b, p)
    for (x, y) in _uv({"positions": positions}, su, sv)["positions"]:
        b.call("sm", "CreateCircleByRadius", mm(x), mm(y), 0.0, mm(d / 2.0))
    b.call("doc", "ClearSelection2", True)
    b.call("sm", "InsertSketch", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    name = b.get(feat, "Name")
    b.call("doc", "ClearSelection2", True)
    sel = b.call("ext", "SelectByID2", name, "SKETCH", 0, 0, 0, False, 0, None, 0)
    b.guard(sel, "truthy", "hole: did not select the holes sketch")
    depth = 0.0 if through_all else mm(p.get("depth_mm", 0))
    created = b.call("fm", "FeatureCut4",
                     True, False, bool(p.get("reverse", False)),
                     THROUGH_ALL if through_all else BLIND, 0, depth, 0.01,
                     False, False, False, False, deg(0), deg(0),
                     False, False, False, False, False, True, True,
                     False, False, False, 0, 0.0, False, False, cast="IFeature")
    b.guard(created, "not_null",
            "hole: the cut failed. Most common cause: the cut went to the WRONG SIDE of "
            "the plane (the direction depends on how the part was extruded) -> repeat "
            "with reverse=True. If it persists, the circles are outside the body.")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def countersink_cone(p: dict) -> dict:
    """The countersink cone: a cut with a draft angle. Call it AFTER the through hole."""
    d_sink = float(_req(p, "d_sink_mm"))
    angle = float(p.get("angle_deg", 90.0))
    half = radians(angle / 2.0)
    depth = (d_sink / 2.0) / tan(half) if half > 0 else d_sink
    b = B("part.countersink_cone")
    _select_sketch(b, p)
    created = b.call("fm", "FeatureCut4",
                     True, False, False, BLIND, 0, mm(depth), 0.01,
                     True, False, True, False, half, 0.0,
                     False, False, False, False, False, True, True,
                     False, False, False, 0, 0.0, False, False, cast="IFeature")
    b.guard(created, "not_null", "countersink: cone (FeatureCut4 c/ draft) failed")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


# ── Patterns ───────────────────────────────────────────────────────────────────
# axis name -> index, so `pattern_linear` accepts a direction without a handle
_EIXO_POR_NOME = {"x": 0, "y": 1, "z": 2}


def pattern_linear(p: dict) -> dict:
    """Repeat a feature in a line; `direction_edge` accepts the AXIS by name (x, y, z) or an edge handle.

    MARKS: direction=1, semente=4.

    The axis name came in on 2026-08-18, for the same reason as `shell` and
    `axis_from_face`: the direction could only come as a handle from
    `inspect_find_edge`, and a handle is what this project takes out of the model's
    hands. With the name, the compiler finds the straight edge parallel to that axis --
    which is what `find_edge` always did.
    The edge found may point to the NEGATIVE side of the axis; then the pattern is
    born outside the material and SolidWorks creates the feature with no instances at
    all. That is what `reverse=True` fixes, and the reason is measured in the
    `linear_pattern` case of the suite.
    """
    b = B("part.pattern_linear")
    b.call("doc", "ClearSelection2", True)
    direction = _req(p, "direction_edge")
    # TWO schema-generator traps here, both measured on 2026-08-18:
    #   * `.get` WITHOUT a fallback becomes a CLOSED enum -- and an enum here would
    #     forbid the handle the description promises. Hence the explicit `, None`: with a
    #     fallback, the options become a suggestion in the description (the same reason
    #     as `_FACES.get(..., ())`).
    #   * reading the parameter again with `p.get("direction_edge")` -- even just to
    #     build the error message -- marks it as OPTIONAL, and it is mandatory. That is
    axis_name = str(direction).lower() if isinstance(direction, str) else ""
    axis = _EIXO_POR_NOME.get(axis_name, None)
    if axis is not None:
        direction = b.helper("find_edge", axis=axis, body=p.get("body"))
        b.guard(direction, "not_null",
                f"pattern_linear: the part has no STRAIGHT edge parallel to axis "
                f"{axis_name.upper()}. Pick another axis, or pass the handle of an "
                f"edge coming from inspect_edges_info.")
    b.helper("select", entities=[direction], marks=[1])
    b.call("ext", "SelectByID2", str(_req(p, "seed_feature")), "BODYFEATURE",
           0, 0, 0, True, 4, None, 0)
    created = b.call("fm", "FeatureLinearPattern5",
                     int(_req(p, "count")), mm(_req(p, "spacing_mm")), 1, 0.0,
                     bool(p.get("reverse", False)), False, "", "",
                     False, False, False, False, True, True, False, False,
                     False, False, 0.0, 0.0, False, False, cast="IFeature")
    b.guard(created, "not_null", "pattern_linear failed (returned None)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def pattern_circular(p: dict) -> dict:
    """MARKS: axis=1, seed=4. `axis` = an entity handle OR an axis name."""
    axis = _req(p, "axis")
    b = B("part.pattern_circular")
    b.call("doc", "ClearSelection2", True)
    if isinstance(axis, str) and not axis.startswith("@"):
        b.call("ext", "SelectByID2", axis, "AXIS", 0, 0, 0, False, 1, None, 0)
    else:
        b.helper("select", entities=[axis], marks=[1])
    b.call("ext", "SelectByID2", str(_req(p, "seed_feature")), "BODYFEATURE",
           0, 0, 0, True, 4, None, 0)
    created = b.call("fm", "FeatureCircularPattern5",
                     int(_req(p, "count")), deg(p.get("total_angle_deg", 360.0)),
                     bool(p.get("reverse", False)), "", False,
                     bool(p.get("equal", True)), False, False, False, False,
                     0, 0.0, "", False, cast="IFeature")
    b.guard(created, "not_null", "pattern_circular failed (returned None)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def pattern_mirror(p: dict) -> dict:
    """MARKS: mirror plane=2, feature=1 (inverted relative to the other patterns)."""
    b = B("part.pattern_mirror")
    b.call("doc", "ClearSelection2", True)
    real = b.helper("resolve_plane", plane=str(_req(p, "mirror_plane")))
    b.call("ext", "SelectByID2", real, "PLANE", 0, 0, 0, False, 2, None, 0)
    b.call("ext", "SelectByID2", str(_req(p, "seed_feature")), "BODYFEATURE",
           0, 0, 0, True, 1, None, 0)
    b.call("fm", "InsertMirrorFeature2", False, False, True, False, 0)
    b.call("doc", "ClearSelection2", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    b.guard(feat, "not_null", "pattern_mirror failed")
    return b.build(b.get(feat, "Name"), p)


# -- datums for mates ----------------------------------------
def reference_plane(p: dict) -> dict:
    """InsertRefPlane returns a TUPLE, not a Feature -> take it from the tree."""
    b = B("part.reference_plane")
    real = b.helper("resolve_plane", plane=str(_req(p, "from_plane")))
    ok = b.call("ext", "SelectByID2", real, "PLANE", 0, 0, 0, False, 0, None, 0)
    b.guard(ok, "truthy", "did not select the source plane")
    b.call("fm", "InsertRefPlane", REFPLANE_DISTANCE, mm(_req(p, "offset_mm")),
           0, 0.0, 0, 0.0)
    b.call("doc", "ClearSelection2", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    # GetTypeName2 is a METHOD (not a property) -- cast to IFeature, it must be called
    kind = b.call(feat, "GetTypeName2")
    b.guard(kind, "equals", "reference_plane failed (most recent feature is not a RefPlane)",
            value="RefPlane")
    if p.get("name"):
        b.set(feat, "Name", str(p["name"]))
    return b.build(b.get(feat, "Name"), p)


def axis_from_face(p: dict) -> dict:
    """An axis from a CYLINDRICAL face -- `face` is OPTIONAL (without it the verb finds the cylindrical face on its own); it gives a robust NAMED reference for a mate.

    `face` is OPTIONAL since 2026-08-18: without it the verb finds the part's
    cylindrical face on its own (`radius_mm` breaks the tie when there is more than
    one). It used to accept only a handle, and a handle is what this project takes
    out of the model's hands -- to create a pulley's axis it had to chain
    `inspect_find_face` first, know the result is a handle, and pass it along. It is
    the same fix as elsewhere: the geometric search belongs to the compiler, not to the
    model.

    Create the axis BEFORE the holes: after them there are several cylindrical
    faces and the automatic choice may land on a hole (use `radius_mm` if you need to).
    """
    b = B("part.axis_from_face")
    b.call("doc", "ClearSelection2", True)
    alvo = p.get("face")
    if not alvo:
        alvo = b.helper("find_face", kind="cylinder", radius_mm=p.get("radius_mm"),
                        body=p.get("body"))
        b.guard(alvo, "not_null",
                "axis_from_face: the part has no cylindrical face"
                + (f" of radius {p['radius_mm']} mm" if p.get("radius_mm") else "")
                + ". An axis is only born from a cylindrical face (a cylinder, a hole, a "
                  "round boss). If the part is prismatic, use part_reference_axis (the "
                  "intersecao de dois planos).")
    b.helper("select", entities=[alvo])
    ok = b.call("doc", "InsertAxis")
    b.guard(ok, "truthy", "InsertAxis failed (is the selected face cylindrical?)")
    b.call("doc", "ClearSelection2", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    if p.get("name"):
        b.set(feat, "Name", str(p["name"]))
    return b.build(b.get(feat, "Name"), p)


def reference_axis(p: dict) -> dict:
    """An axis at the INTERSECTION of two planes."""
    b = B("part.reference_axis")
    b.call("doc", "ClearSelection2", True)
    a = b.helper("resolve_plane", plane=str(p.get("plane_a", "Front")))
    c = b.helper("resolve_plane", plane=str(p.get("plane_b", "Right")))
    b.call("ext", "SelectByID2", a, "PLANE", 0, 0, 0, False, 0, None, 0)
    b.call("ext", "SelectByID2", c, "PLANE", 0, 0, 0, True, 0, None, 0)
    ok = b.call("doc", "InsertAxis2", True)
    b.guard(ok, "truthy", "reference_axis failed (InsertAxis2=False)")
    b.call("doc", "ClearSelection2", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    if p.get("name"):
        b.set(feat, "Name", str(p["name"]))
    return b.build(b.get(feat, "Name"), p)


def reference_point(p: dict) -> dict:
    kind = str(p.get("kind", "face_center"))
    if kind not in REFPT:
        raise VerbError(f"kind '{kind}' invalid ({sorted(REFPT)})")
    b = B("part.reference_point")
    b.helper("select", entities=_req(p, "entities"))
    b.call("fm", "InsertReferencePoint", REFPT[kind], 0, 0.0, 1)
    b.call("doc", "ClearSelection2", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    kind = b.call(feat, "GetTypeName2")
    b.guard(kind, "equals", f"reference_point '{kind}' failed (invalid selection?)",
            value="RefPoint")
    if p.get("name"):
        b.set(feat, "Name", str(p["name"]))
    return b.build(b.get(feat, "Name"), p)


def reference_csys(p: dict) -> dict:
    b = B("part.reference_csys")
    ents = [_req(p, "origin")]
    for key in ("x_entity", "y_entity"):
        if p.get(key):
            ents.append(p[key])
    b.call("doc", "ClearSelection2", True)
    b.helper("select", entities=ents)
    created = b.call("fm", "InsertCoordinateSystem", False, False, False, cast="IFeature")
    b.guard(created, "not_null", "reference_csys failed (valid origin/axes?)")
    b.call("doc", "ClearSelection2", True)
    if p.get("name"):
        b.set(created, "Name", str(p["name"]))
    return b.build(b.get(created, "Name"), p)


# -- body / wall operations --------------------------------------------------
def shell(p: dict) -> dict:
    """SHELL: turns the solid into a thin-walled HOLLOW BOX, leaving OPEN the face that `remove_faces` names (top, bottom, front, back, left, right) or a handle.

    The first line of this docstring is the ONLY one that reaches the model
    (`_describe` takes only the first line), so the face names go IN it: accepting a
    face name and telling nobody would be the ADDRESS defect all over again.
    face name and telling nobody would be the ADDRESS defect of section 7 all over again.

    `remove_faces` accepts the face NAME (top, bottom, front, back, left, right)
    'right') or a handle -- since 2026-08-18. It used to be handle only, and that is
    why the only way to ask for "a shell with the top face open" was to chain
    `inspect_find_face_at` and carry the result, which is exactly the handle juggling
    this project takes out of the model's hands. The name resolves through the SAME
    helper as `hole_on`/`boss_on`, so both surfaces speak the same language.
    """
    faces = _req(p, "remove_faces")
    if isinstance(faces, str):
        # the model sends "@h7" (a single one) instead of ["@h7"]; the helper ITERATES,
        # so the string would become a sequence of characters -- the same error that
        # `delete_body` "passing" without deleting anything (measured 2026-08).
        faces = [faces]
    b = B("part.shell")
    resolvidas = []
    for f in faces:
        # `.get` WITH a fallback: `remove_faces` accepts a name OR a handle, and an
        # `in _FACES` here would make the schema generator declare a closed enum and
        # FORBID the handle (the section 3 trap of the handoff, nine times so far).
        orient = _FACES.get(str(f).lower(), ()) if isinstance(f, str) else ()
        if not orient:
            resolvidas.append(f)
            continue
        axis, maior, _par, _su, _sv = orient
        alvo = b.helper("find_face_at", axis=axis, want_max=maior,
                        point_mm=p.get("face_point"), body=p.get("body"))
        b.guard(alvo, "not_null", _no_face("shell", str(f)))
        resolvidas.append(alvo)
    b.helper("select", entities=resolvidas)
    b.call("doc", "InsertFeatureShell", mm(_req(p, "thickness_mm")),
           bool(p.get("outward", False)))
    b.call("doc", "ClearSelection2", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    b.guard(feat, "not_null", "shell failed")
    return b.build(b.get(feat, "Name"), p)


def scale(p: dict) -> dict:
    factor = float(_req(p, "factor"))
    about = {"centroid": 0, "origin": 1}.get(str(p.get("about", "centroid")), 0)
    b = B("part.scale")
    b.call("doc", "InsertScale", factor, factor, factor, True, about)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    b.guard(feat, "not_null", "scale failed")
    return b.build(b.get(feat, "Name"), p)


def combine(p: dict) -> dict:
    """Boolean of bodies. SW 2017 convention: add/common pass main=Nothing and ALL the
    bodies as tools; subtract passes main=what stays and tools=what is removed."""
    op = {"add": BODY_ADD, "subtract": BODY_CUT, "common": BODY_INTERSECT}.get(
        str(p.get("operation", "add")))
    if op is None:
        raise VerbError("operation invalid (add|subtract|common)")
    b = B("part.combine")
    if str(p.get("operation", "add")) == "subtract":
        main = _req(p, "main_body")
        tools = _req(p, "tool_bodies")
        ok = b.helper("combine", operation=op, main=main, tools=tools)
    else:
        all_bodies = b.helper("bodies")
        ok = b.helper("combine", operation=op, main=None, tools=all_bodies)
    b.guard(ok, "truthy",
            "combine failed (disjoint bodies, no intersection, or the part is not "
            "create bodies with extrude(merge=False))")
    b.call("doc", "ClearSelection2", True)
    feat = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    return b.build(b.get(feat, "Name"), p)


def delete_body(p: dict) -> dict:
    b = B("part.delete_body")
    b.helper("select_bodies", bodies=_req(p, "bodies"))
    created = b.call("fm", "InsertDeleteBody", cast="IFeature")
    b.guard(created, "not_null", "delete_body failed (no body selected?)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def delete_feature(p: dict) -> dict:
    """Delete ONE feature by name, taking the absorbed sketch with it.

    It exists for recovery after a failed operation: when a step is rejected, whatever it
    created has to go before the next attempt. Without this, "try again" finds the
    part already modified and no attempt can succeed -- measured 2026-08-17, a
    chamfer rejected (correctly, on the wrong side) cost 41 calls, all answering "the
    volume did not go down", because the chamfer was still there. Rejecting correctly
    without a way back costs as much as not rejecting at all.

    `delete_body` will not do: it deletes a BODY, and a fillet/chamfer/cut creates no
    body at all -- it alters what already exists.
    """
    name = str(_req(p, "name"))
    b = B("part.delete_feature")
    b.call("doc", "ClearSelection2", True)
    sel = b.call("ext", "SelectByID2", name, "BODYFEATURE", 0, 0, 0, False, 0, None, 0)
    b.guard(sel, "truthy",
            f"delete_feature: could not find the feature '{name}' in the tree. Use the "
            f"EXACT name SolidWorks gave it (Cut-Extrude1, Fillet2), which "
            f"inspect_list_features shows -- not a description of what it does.")
    # `DeleteSelection2(swDelete_Absorbed)` takes the sketch along; `EditDelete` would
    # the orphan Sketch in the tree, and it would reappear on the next feature read.
    ok = b.call("ext", "DeleteSelection2", DELETE_ABSORBED)
    b.guard(ok, "truthy",
            f"delete_feature: SolidWorks refused to delete '{name}' (another feature "
            f"depends on it? delete from the NEWEST to the oldest)")
    b.call("doc", "ClearSelection2", True)
    return b.build(name, p)


def draft(p: dict) -> dict:
    """MARKS: neutro=1, faces a inclinar=2.

    BOTH SELECTIONS ARE CHECKED BEFORE `InsertMultiFaceDraft`, and that is not
    fussiness: without the neutral plane selected SolidWorks has no draft direction and
    opens the modal box "The draft direction is not defined" -- which BLOCKS the whole
    COM until somebody clicks it. Measured 2026-08-19, in the `solid/draft` case of the
    `suite_verbs`: the round sat there for 11 minutes, and what stalled was neither
    SolidWorks nor the model, it was this verb accepting a `neutral` that resolved to
    nothing.
    It is the SAME shape as the 'Modify' box gotcha that `sw_com.connect()` already
    turns off (`swInputDimValOnCreate`): a verb that, given a bad argument, stops the
    automation instead of returning an error. A verb may fail; no verb may open a modal.
    """
    neutral = _req(p, "neutral")
    faces = _req(p, "faces")
    b = B("part.draft")
    b.call("doc", "ClearSelection2", True)
    if isinstance(neutral, str) and not neutral.startswith("@"):
        real = b.helper("resolve_plane", plane=neutral)
        sel = b.call("ext", "SelectByID2", real, "PLANE", 0, 0, 0, False, 1, None, 0)
        b.guard(sel, "truthy",
                f"draft: the neutral plane '{neutral}' was not selected. The neutral is "
                f"where the taper is MEASURED from: pass a plane from the tree "
                f"(Front/Top/Right or its real name) or the handle of a planar FACE of "
                f"the part. Without a neutral, SolidWorks has no draft direction.")
    else:
        n = b.helper("select", entities=[neutral], marks=[1])
        b.guard(n, "gte", value=1, msg=(
            "draft: the neutral that was passed selected nothing. It has to be the "
            "handle of a planar FACE (inspect_faces_info gives them) or a plane name."))
    nf = b.helper("select", entities=faces, marks=[2], clear=False)
    b.guard(nf, "gte", value=1, msg=(
        "draft: none of the faces to taper was selected. `faces` is a list of face "
        "handles (inspect_faces_info), and it cannot include the neutral face."))
    created = b.call("fm", "InsertMultiFaceDraft", deg(_req(p, "angle_deg")),
                     bool(p.get("reverse", False)), False, DRAFT_NEUTRAL_PLANE,
                     False, False, cast="IFeature")
    b.guard(created, "not_null", "draft failed (check the neutral/faces/direction)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


# ── parametric ───────────────────────────────────────────────────────────────
def get_dimension(p: dict) -> dict:
    name = str(_req(p, "name"))
    b = B("part.get_dimension")
    dim = b.call("doc", "Parameter", name, cast="IDimension")
    b.guard(dim, "not_null",
            f"dimension '{name}' does not exist. Feature dimensions "
            f"(D1@Boss-Extrude1) always exist; sketch ones only if they were ADDED")
    return b.build({"$ref": b.get(dim, "SystemValue").lstrip("$"), "scale": 1000.0}, p)


def set_dimension(p: dict) -> dict:
    name = str(_req(p, "name"))
    value = float(_req(p, "value_mm"))
    b = B("part.set_dimension")
    dim = b.call("doc", "Parameter", name, cast="IDimension")
    b.guard(dim, "not_null", f"dimension '{name}' does not exist")
    b.set(dim, "SystemValue", mm(value))
    if p.get("rebuild", True):
        b.call("doc", "EditRebuild3")
    return b.build(value, p)


def set_dimensions(p: dict) -> dict:
    """Batch: sets SEVERAL dimensions and rebuilds ONCE (the optimization loop)."""
    deltas = _req(p, "deltas")
    if not isinstance(deltas, dict) or not deltas:
        raise VerbError("deltas must be a non-empty dict {name: value_mm}")
    b = B("part.set_dimensions")
    for name, val in deltas.items():
        dim = b.call("doc", "Parameter", str(name), cast="IDimension")
        b.guard(dim, "not_null", f"dimension '{name}' does not exist")
        b.set(dim, "SystemValue", mm(float(val)))
    if p.get("rebuild", True):
        b.call("doc", "EditRebuild3")
    return b.build(deltas, p)


def set_tolerance(p: dict) -> dict:
    """GOTCHA: IDimensionTolerance.SetValues (SetValues2 returns False and does NOT apply)."""
    name = str(_req(p, "dim_name"))
    kind = str(p.get("kind", "bilateral")).lower()
    b = B("part.set_tolerance")
    dim = b.call("doc", "Parameter", name, cast="IDimension")
    b.guard(dim, "not_null", f"dimension '{name}' does not exist (dimension it first)")
    tol = b.get(dim, "Tolerance", cast="IDimensionTolerance")
    if kind == "bilateral":
        b.set(tol, "Type", TOL["bilateral"])
        b.call(tol, "SetValues", mm(p.get("lower_mm", 0.0)), mm(p.get("upper_mm", 0.0)))
    elif kind == "symmetric":
        up = abs(float(p.get("upper_mm", 0.0)))
        b.set(tol, "Type", TOL["symmetric"])
        b.call(tol, "SetValues", mm(-up), mm(up))
    elif kind == "fit":
        hole_fit = str(p.get("hole_fit", ""))
        shaft_fit = str(p.get("shaft_fit", ""))
        if not (hole_fit or shaft_fit):
            raise VerbError("kind=fit requires hole_fit and/or shaft_fit (e.g. H7)")
        b.set(tol, "Type", TOL["fit"])
        b.call(tol, "SetFitValues", hole_fit, shaft_fit)
    elif kind == "basic":
        b.set(tol, "Type", TOL["basic"])
    else:
        raise VerbError(f"kind '{kind}' invalid (bilateral|symmetric|fit|basic)")
    if p.get("rebuild", True):
        b.call("doc", "EditRebuild3")
    return b.build(name, p)


def add_equation(p: dict) -> dict:
    """An embedded equation. ALWAYS use the 2-arg Add at index = the current count
    (Add2/Add3 break the marshaling and return -1 on SW 2017)."""
    eq = str(_req(p, "equation"))
    b = B("part.add_equation")
    em = b.call("doc", "GetEquationMgr", cast="IEquationMgr")
    b.guard(em, "not_null", "GetEquationMgr returned Nothing")
    count = b.call(em, "GetCount")
    idx = b.call(em, "Add", count, eq)
    b.guard(idx, "gte",
            f"add_equation failed (ret=-1): '{eq}'. Causes: the left-hand dimension "
            f"does not exist, is already driven by another equation, or bad syntax",
            value=0)
    if p.get("rebuild", True):
        b.call(em, "EvaluateAll")
        b.call("doc", "EditRebuild3")
    return b.build(idx, p)


def equations(p: dict) -> dict:
    b = B("part.equations")
    return b.build(b.helper("equations"), p)


# ── metadata and material ──────────────────────────────────────────────────────
def set_property(p: dict) -> dict:
    """Feeds the drawing titleblock via $PRPSHEET. swCustomInfoText=30, option 1=overwrite."""
    b = B("part.set_property")
    cpm = b.call("ext", "CustomPropertyManager", str(p.get("config", "")),
                 cast="ICustomPropertyManager")
    b.guard(cpm, "not_null", "CustomPropertyManager returned Nothing")
    b.call(cpm, "Add3", str(_req(p, "name")), 30, str(_req(p, "value")), 1)
    return b.build(str(p["name"]), p)


def get_property(p: dict) -> dict:
    """SW 2017 GOTCHA: no Get6; Get5 returns (status, val, resolved, wasResolved) and
    for plain text `resolved` comes back EMPTY -> the compiler returns both."""
    b = B("part.get_property")
    cpm = b.call("ext", "CustomPropertyManager", str(p.get("config", "")),
                 cast="ICustomPropertyManager")
    res = b.call(cpm, "Get5", str(_req(p, "name")), False)
    return b.build(res, p)


# The name SolidWorks accepts != the name a person (or an LLM) writes. An unknown name
# is SILENTLY IGNORED by SetMaterialPropertyName2 -- it does not raise, it returns
# nothing, and the part keeps the default density of 1000 kg/m3. Measured: asking for
# "Aluminum 1060" left the mass 2.7x too low with no warning at all.
MATERIAIS = {
    # aluminum
    "aluminum 1060": "1060 Alloy", "aluminium 1060": "1060 Alloy",
    "aluminio 1060": "1060 Alloy", "al 1060": "1060 Alloy",
    "aluminum 6061": "6061 Alloy", "aluminio 6061": "6061 Alloy",
    "al 6061": "6061 Alloy", "aluminum 7075": "7075 Alloy",
    "aluminio 7075": "7075 Alloy", "aluminio": "1060 Alloy",
    "aluminum": "1060 Alloy", "aluminium": "1060 Alloy",
    # steels
    "aco 1020": "AISI 1020", "steel 1020": "AISI 1020",
    "aco 1045": "AISI 1045", "steel 1045": "AISI 1045",
    "aco inox": "AISI 304", "inox": "AISI 304", "stainless": "AISI 304",
    "aco inox 304": "AISI 304", "aco inox 316": "AISI 316",
    "aco carbono": "Plain Carbon Steel", "carbon steel": "Plain Carbon Steel",
    "aco": "Plain Carbon Steel", "steel": "Plain Carbon Steel",
    "aco liga": "Alloy Steel", "alloy steel": "Alloy Steel",
    # other metals
    "latao": "Brass", "brass": "Brass", "cobre": "Copper", "copper": "Copper",
    "bronze": "Bronze", "titanio": "Titanium", "titanium": "Titanium",
    "abs": "ABS", "nylon": "Nylon 6/10", "pvc": "PVC Rigid",
    "acrilico": "Acrylic (Medium-high impact)", "acrylic": "Acrylic (Medium-high impact)",
}


def set_material(p: dict) -> dict:
    """The body material. An unknown name is IGNORED by SW -- which is why we return
    what REALLY got applied, never what was asked for."""
    pedido = str(_req(p, "name")).strip()
    name = MATERIAIS.get(pedido.lower(), pedido)
    config = str(p.get("config", ""))
    b = B("part.set_material")
    b.call("part", "SetMaterialPropertyName2", config,
           str(p.get("database", "SOLIDWORKS Materials")), name)
    # READ IT BACK: it is the only way to know whether it took. Returning the requested
    # name (as before) made the verb always report success, and the mass came out wrong
    aplicado = b.call("part", "GetMaterialPropertyName2", config)
    return b.build({"pedido": pedido, "enviado": name, "applied": aplicado,
                    "warning": ("if applied comes back empty, the name does not exist "
                                "in the SolidWorks database and the part was left "
                              "kg/m3) -- tente o name exato, ex.: '1060 Alloy', "
                              "'AISI 1020', 'Plain Carbon Steel'")}, p)


def _extrusao(b: B, profundidade_mm: float, reverse: bool, merge: bool):
    """A blind boss with FeatureExtrusion2 23 arguments, like the `extrude` verb."""
    return b.call("fm", "FeatureExtrusion2",
                  True, False, reverse, BLIND, 0, mm(profundidade_mm), 0.01,
                  False, False, False, False, deg(1), deg(1),
                  False, False, False, False, merge, True, True, 0, 0, False,
                  cast="IFeature")


# -- COARSE verbs: one intent = one call -------------------------------------
# They exist because the suite showed the cost of making the model orchestrate. "A block
# 30 mm arredondado" custava sketch_begin + sketch_add + sketch_end + extrude +
# inspect_bodies + inspect_edges + fillet, and the HANDLE chaining was where the
# model got wrong most often (it invented `@h1`).
#
# The complexity fits here because the bundle resolves references inside arguments:
# `helper("edges")` returns the list and `helper("select", entities=<list>)` consumes it,
# all in one ATOMIC bundle -- with no need for multi-round verbs
# (a known debt), and with no orchestration on the caller.
#
# They do not replace the fine-grained verbs: whoever needs control still has it. It was
# measured that exposing more tools does not get in the way.
def block(p: dict) -> dict:
    """A rectangular block in ONE call: width_mm=X, height_mm=Y, depth_mm=Z.

    THE AXIS MAP IS WHAT THE MODELS GET WRONG, so it goes in the title: on the Front
    plane (the default), `width_mm` is the measurement in X, `height_mm` the one in Y
    and `depth_mm` the extrusion depth, which is Z. Measured 2026-08-18: asking for
    "20 mm in X, 30 in Y and 40 in Z" became a 40x30x20 block on `qwen3-coder:30b`
    (it lost the whole case on that), and the description at the time said
    just "in millimeters" -- the vocabulary existed, the address did not.

    Equivale a sketch_begin + sketch_add + sketch_end + extrude."""
    w = float(_req(p, "width_mm"))
    h = float(_req(p, "height_mm"))
    d = float(_req(p, "depth_mm"))
    x0, y0 = float(p.get("x", 0.0)), float(p.get("y", 0.0))
    b = B("part.block")
    su, sv = _open_sketch(b, p)
    # BOTH corners converted, not just the origin: with sv=-1, `y0 + h` has to become
    # `-(y0 + h)`, and not `-y0 + h` -- the rectangle would come out on the other side.
    u0, v0 = su * x0, sv * y0
    u1, v1 = su * (x0 + w), sv * (y0 + h)
    b.call("sm", "CreateCornerRectangle", mm(u0), mm(v0), 0.0,
           mm(u1), mm(v1), 0.0)
    b.call("doc", "ClearSelection2", True)
    b.call("sm", "InsertSketch", True)
    sk = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    b.guard(sk, "not_null", "block: the sketch did not show up in the tree")
    name = b.get(sk, "Name")
    b.call("doc", "ClearSelection2", True)
    sel = b.call("ext", "SelectByID2", name, "SKETCH", 0, 0, 0, False, 0, None, 0)
    b.guard(sel, "truthy", "block: did not select the sketch")
    feat = _extrusao(b, d, bool(p.get("reverse", False)), bool(p.get("merge", True)))
    b.guard(feat, "not_null",
            "block: the extrusion failed (zero depth? sketch left open?)")
    return b.build(b.get(feat, "Name"), p)


def cylinder(p: dict) -> dict:
    """A cylinder in ONE call: sketches the circle and extrudes it."""
    r = float(_req(p, "diameter_mm")) / 2.0
    alt = float(_req(p, "height_mm"))
    b = B("part.cylinder")
    su, sv = _open_sketch(b, p)
    q = _uv(p, su, sv)
    cx, cy = float(q.get("cx", 0.0)), float(q.get("cy", 0.0))
    b.call("sm", "CreateCircleByRadius", mm(cx), mm(cy), 0.0, mm(r))
    b.call("doc", "ClearSelection2", True)
    b.call("sm", "InsertSketch", True)
    sk = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    b.guard(sk, "not_null", "cylinder: the sketch did not show up in the tree")
    name = b.get(sk, "Name")
    b.call("doc", "ClearSelection2", True)
    sel = b.call("ext", "SelectByID2", name, "SKETCH", 0, 0, 0, False, 0, None, 0)
    b.guard(sel, "truthy", "cylinder: did not select the sketch")
    feat = _extrusao(b, alt, bool(p.get("reverse", False)), bool(p.get("merge", True)))
    b.guard(feat, "not_null", "cylinder: the extrusion failed")
    return b.build(b.get(feat, "Name"), p)


def fillet_all(p: dict) -> dict:
    """Rounds ALL the body edges -- without the model listing a single edge."""
    b = B("part.fillet_all")
    b.call("doc", "ClearSelection2", True)
    arestas = b.helper("edges", body=p.get("body"))
    n = b.helper("select", entities=arestas)
    b.guard(n, "truthy", "fillet_all: no edge selected (does the part have a body?)")
    created = b.call("fm", "FeatureFillet3",
                     FILLET_PROPAGATE | FILLET_UNIFORM, mm(_req(p, "radius_mm")), 0, 0,
                     FILLET_SIMPLE, 0, 0, None, None, None, None, None, None, None,
                     cast="IFeature")
    b.guard(created, "not_null",
            f"fillet_all: SolidWorks refused a radius of {_req(p, 'radius_mm')} mm on "
            f"ALL the edges at once. Usually ONE edge cannot take the radius -- "
            f"typically the rim of a hole that has already been chamfered or rounded, "
            f"and not a sharp corner. What to do, IN THIS ORDER: "
            f"(1) KEEP the {_req(p, 'radius_mm')} mm radius and reduce the SET: "
            f"part_fillet_on(radius_mm={_req(p, 'radius_mm')}, only='straight') takes the "
            f"sharp corners, only=curved the hole rims, and face= limits it to one side; "
            f"(2) call inspect_edges_info, look at `length_mm`, and round only the "
            f"edges that fit, with part_fillet(radius_mm, edges=[...]); "
            f"(3) only if the request really is the WHOLE part, a smaller radius -- and "
            f"then SAY in your answer the radius that actually came out, because "
            f"changing the number changes what the user asked for. Do NOT repeat it.")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


# Synonyms the model actually writes, mapped to what the executor understands. Indexed
# WITHOUT a fallback on purpose: the domain is closed (there is no "edge kind" handle),
# so the schema comes out as a real `enum` -- see Rule 1 of `schemas.py`.
_ARESTAS = {"curved": "curved", "curvas": "curved", "circular": "curved",
            "circulares": "curved", "straight": "straight", "retas": "straight",
            "all": "all"}


def _sel_arestas(b, p: dict, verb: str, action: str):
    """Edge selection by DESCRIPTION, shared by fillet_on/chamfer_on."""
    # `p.get("face")` with no default: with `default=""` the generator announced a
    # default NOT in the enum itself, and face="" would be rejected in validation.
    face = str(p.get("face") or "").lower()
    if face and face not in _FACES:
        b.guard(None, "not_null",
                f"{verb}: I do not know the face '{face}'. The names are top, bottom, "
                f"front, back, right, left -- and they go by the DIRECTION of the "
                f"posicao na tela (top = +Y, front = +Z, right = +X). Chame "
                f"inspect_faces_info to see which ones exist on this part.")
    kind = str(p.get("only", "all")).lower()
    if kind not in _ARESTAS:
        b.guard(None, "not_null",
                f"{verb}: I do not know only='{kind}'. Use curved (hole and radius "
                f"rims), straight (sharp corners) or all.")
    kw = {"only": _ARESTAS[kind], "body": p.get("body")}
    if face:
        kw["axis"], kw["want_max"] = _FACES[face][0], _FACES[face][1]
    n = b.helper("select_edges", **kw)
    b.guard(n, "truthy",
            f"{verb}: no edge matches only='{kind}'"
            + (f" on face '{face}'" if face else "")
            + ". What to do: call inspect_edges_info and look at which edges the part "
              "has; curved only exists if there is a hole or a radius, and straight "
              f"only on a sharp corner. To {action} EVERYTHING at once use part_{verb.split('_')[0]}_all.")
    return n


def fillet_on(p: dict) -> dict:
    """Rounds the DESCRIBED edges (curved/straight, optionally of one face).

    The pair of `hole_on`/`boss_on` for edges: the model says "the rims of the hole on
    top" and the compiler works out which they are. Without this a hole rim was
    unreachable -- `inspect_find_edge` only returns STRAIGHT edges (see the helper
    `select_edges` in the executor for the measurement)."""
    b = B("part.fillet_on")
    b.call("doc", "ClearSelection2", True)
    _sel_arestas(b, p, "fillet_on", "arredondar")
    created = b.call("fm", "FeatureFillet3",
                     FILLET_PROPAGATE | FILLET_UNIFORM, mm(_req(p, "radius_mm")), 0, 0,
                     FILLET_SIMPLE, 0, 0, None, None, None, None, None, None, None,
                     cast="IFeature")
    # The message does NOT assert the cause. The version that said only "the radius is
    # too large, try half" sent the model halving 1 -> 0.007 mm across 41 calls, when
    # what had happened was the opposite: the fillet HAD already been applied on the
    # first call and SolidWorks was refusing the second. A guard that guesses the cause
    # steers the model into the wrong hole with all the authority of an instruction.
    b.guard(created, "not_null",
            f"fillet_on: SolidWorks refused a radius of {_req(p, 'radius_mm')} mm nessas "
            f"edges. The two common causes, IN THIS ORDER: (1) the fillet HAS already "
            f"been applied -- check with inspect_list_features, and if there is already "
            f"a Fillet the step may be DONE; (2) the radius does not fit (it cannot "
            f"exceed half the smallest neighbouring face) -- then yes, try half, ONCE. "
            f"If the same error comes back with another value, it is not the value.")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def chamfer_on(p: dict) -> dict:
    """Chamfers the DESCRIBED edges. The pair of `fillet_on`."""
    b = B("part.chamfer_on")
    b.call("doc", "ClearSelection2", True)
    _sel_arestas(b, p, "chamfer_on", "chanfrar")
    created = b.call("fm", "InsertFeatureChamfer",
                     CHAMFER_TANGENT_PROP, CHAMFER_ANGLE_DIST,
                     mm(_req(p, "dist_mm")), deg(float(p.get("angle_deg", 45.0))),
                     0.0, 0.0, 0.0, 0.0, cast="IFeature")
    b.guard(created, "not_null",
            f"chamfer_on: o SolidWorks recusou distancia {_req(p, 'dist_mm')} mm "
            f"on those edges. The two common causes, IN THIS ORDER: (1) the chamfer HAS "
            f"already been applied -- check with inspect_list_features; (2) the distance "
            f"does not fit -- then try half, ONCE. The same error with another value is "
            f"value.")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def chamfer_all(p: dict) -> dict:
    """Chamfers ALL the body edges."""
    b = B("part.chamfer_all")
    b.call("doc", "ClearSelection2", True)
    arestas = b.helper("edges", body=p.get("body"))
    n = b.helper("select", entities=arestas)
    b.guard(n, "truthy", "chamfer_all: no edge selected")
    ang = float(p.get("angle_deg", 45.0))
    # The SAME call as the fine-grained `chamfer` verb (and as `sw_parts.chamfer`,
    # validated in the smoke): 8 arguments, FLAGS and TYPE in separate positions, and
    # the distances as FLOATs. The previous version ORed the two flags into one argument
    # and passed `int 0` for the distances -- both give a "Type mismatch" in COM.
    created = b.call("fm", "InsertFeatureChamfer",
                     CHAMFER_TANGENT_PROP, CHAMFER_ANGLE_DIST,
                     mm(_req(p, "dist_mm")), deg(ang), 0.0, 0.0, 0.0, 0.0,
                     cast="IFeature")
    b.guard(created, "not_null",
            f"chamfer_all: SolidWorks refused a distance of {_req(p, 'dist_mm')} mm on "
            f"ALL the edges at once -- normally the chamfer does not fit on ONE edge. "
            f"What to do, IN THIS ORDER: "
            f"(1) KEEP the {_req(p, 'dist_mm')} mm distance and reduce the SET: "
            f"part_chamfer_on(dist_mm={_req(p, 'dist_mm')}, only='straight'|'curved', "
            f"face=) chamfers only part of the edges; "
            f"(2) call inspect_edges_info and chamfer only the ones that fit, with "
            f"part_chamfer(dist_mm, edges=[...]); "
            f"(3) only if the request really is the WHOLE part, a smaller distance -- "
            f"and then SAY in your answer the distance that actually came out, because "
            f"changing the number changes what the user asked for. Do NOT repeat it.")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


# A sketch opened ON A FACE does not inherit the global coordinate system: SolidWorks
# orients the sketch by the face normal, and only the +Z face coincides with the global
# one. Measured face by face on a 50x30x20 block (x 0..50, y 0..30, z 0..20), asking for
# the same point and reading the hole center back from the shift of the center of mass:
#
#   face    normal   sketch coords     ->  global point
#   front     +Z        (10,  10)          x=10, y=10     (identity)
#   back      -Z        (-10, 10)          x=10, y=10     (u inverted)
#   top       +Y        (10, -10)          x=10, z=10     (v inverted)
#   bottom    -Y        (10,  10)          x=10, z=10
#   right     +X        (-10, 10)          z=10, y=10     (u inverted)
#   left      -X        (10,  10)          z=10, y=10
#
# Without this translation, asking for (10,10) on the top face drew the circle at
# z=-10 -- OUTSIDE the material -- and the cut failed. Only `front` worked, by accident.
_FACES = {
    # face:    (axis, want_max, pair of global axes, sign of u, sign of v)
    "front":  (2, True,  "x,y", +1, +1),
    "back":   (2, False, "x,y", -1, +1),
    "top":    (1, True,  "x,z", +1, -1),
    "bottom": (1, False, "x,z", +1, +1),
    "right":  (0, True,  "z,y", -1, +1),
    "left":   (0, False, "z,y", +1, +1),
}


def hole_on(p: dict) -> dict:
    """Hole(s) on a face found by GEOMETRY, not by handle.

    The model says "top face" and the compiler resolves it -- that was the chaining
    inspect_bodies -> inspect_faces -> pick -> hole that used to trip it up most.

    `positions` are pairs in GLOBAL COORDINATES of the face plane, in the order
    `_FACES` documents: (x,y) for front/back, (x,z) for top/bottom and (z,y) for
    right/left. The verb converts them to the face sketch.

    `face_point` ([x,y,z] in mm) breaks the tie when MORE THAN ONE face has the same
    orientation -- two bosses at the same height are both "the top face", and without
    the point the compiler picked one of them silently. Pass any point on the face
    intended one (`inspect.faces_info` gives one in `point_mm`)."""
    face_kind = str(p.get("face", "top")).lower()
    if face_kind not in _FACES:
        raise VerbError(f"face '{face_kind}' invalid ({', '.join(sorted(_FACES))})")
    axis, maior, pair, su, sv = _FACES[face_kind]
    positions = _req(p, "positions")
    d = float(_req(p, "diameter_mm"))
    b = B("part.hole_on")
    alvo = b.helper("find_face_at", axis=axis, want_max=maior,
                    point_mm=p.get("face_point"), body=p.get("body"))
    b.guard(alvo, "not_null", _no_face("hole_on", face_kind))
    b.call("doc", "ClearSelection2", True)
    b.helper("select", entities=[alvo])
    b.call("sm", "InsertSketch", True)
    for (x, y) in positions:
        b.call("sm", "CreateCircleByRadius",
               mm(su * float(x)), mm(sv * float(y)), 0.0, mm(d / 2.0))
    b.call("doc", "ClearSelection2", True)
    b.call("sm", "InsertSketch", True)
    sk = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    name = b.get(sk, "Name")
    b.call("doc", "ClearSelection2", True)
    sel = b.call("ext", "SelectByID2", name, "SKETCH", 0, 0, 0, False, 0, None, 0)
    b.guard(sel, "truthy", "hole_on: did not select the holes sketch")
    created = b.call("fm", "FeatureCut4",
                     True, False, False, THROUGH_ALL, 0, 0.0, 0.01,
                     False, False, False, False, deg(0), deg(0),
                     False, False, False, False, False, True, True,
                     False, False, False, 0, 0.0, False, False, cast="IFeature")
    # a message that TEACHES: it was measured that "Member not found" makes the model
    # guess blindly, while an error naming the right parameter is fixed on the next try.
    b.guard(created, "not_null",
            f"hole_on: the cut failed. On face '{face_kind}' each position is the pair "
            f"({pair}) in millimeters, measured from the model origin -- check that the "
            f"points fall INSIDE the face (use inspect.mass/inspect.faces to see the "
            f"dimensions) and that the {d} mm diameter fits.")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(created, "Name"), p)


def boss_on(p: dict) -> dict:
    """A rectangular boss on a face, in ONE call: width_mm and height_mm are the TWO measurements IN THE PLANE of the face (the same width x height order as block); depth_mm is always the one that extrudes OUT of it -- just like part_extrude and part_block, where depth_mm is also the extrusion.

    It exists for the same reason as `hole_on`: a sketch opened on a face does NOT use
    the global coordinate system, and the model has no way of knowing that face frame.
    Measured: told to sketch on `face='top'`, it drew the rectangle at negative y and
    the material came out DETACHED from the part -- three attempts in a row, a new
    island every time. Here the center comes in GLOBAL coordinates of the face plane
    (the same pair `hole_on` uses: (x,z) for top/bottom, (x,y) for front/back, (z,y)
    for right/left).

    The material grows OUT of the face, which is what "a boss on top" means.

    RENAMED on 2026-08-23. Before that the extrusion was called
    `height_mm` and the second in-plane measurement `depth_mm` -- the INVERSE of the
    `part_block`/`part_extrude` convention, where `depth_mm` always extrudes. It was
    measured on BOTH models that merely REWRITING the description (without renaming)
    was not enough: one case went back to 1/5 and the raw model kept making the
    SAME swap -- writing the height value into `depth_mm`, the name it already
    associates with the extrusion in `part_extrude`/`part_block`. The fix that stuck
    was to give the parameter the name the model already gets right in a sibling verb,
    not to ask it to learn a new name. Old callers with `height_mm=<extrusion>` BREAK
    (the extrusion now comes out wrong, silently) -- every golden and recipe was
    updated with it.
    """
    face_kind = str(p.get("face", "top")).lower()
    if face_kind not in _FACES:
        raise VerbError(f"face '{face_kind}' invalid ({', '.join(sorted(_FACES))})")
    axis, maior, pair, su, sv = _FACES[face_kind]
    w = float(_req(p, "width_mm")) / 2.0
    d = float(_req(p, "height_mm")) / 2.0
    alt = float(_req(p, "depth_mm"))
    # `at` with 3 values blew up with a `ValueError: too many values to unpack` that
    # nobody handled -> a BARE HTTP 500 for the model, without a word about what to do.
    # Measured 2026-08-18: 5 occurrences, and the model sent
    # `at: [30, 35, 30]` -- it repeated the coordinate of the face itself, which is the
    # exact mistake the sentence below undoes.
    raw = p.get("at") or [0.0, 0.0]
    if len(raw) != 2:
        raise VerbError(
            f"`at` came with {len(raw)} values. On face '{face_kind}' the boss center "
            f"is the PAIR ({pair}) in mm, measured from the model origin -- two "
            f"numbers, not three: the third coordinate is the face itself.")
    ca, cb = [float(v) for v in raw]
    b = B("part.boss_on")
    alvo = b.helper("find_face_at", axis=axis, want_max=maior,
                    point_mm=p.get("face_point"), body=p.get("body"))
    b.guard(alvo, "not_null", _no_face("boss_on", face_kind))
    b.call("doc", "ClearSelection2", True)
    b.helper("select", entities=[alvo])
    b.call("sm", "InsertSketch", True)
    u, v = su * ca, sv * cb
    b.call("sm", "CreateCornerRectangle", mm(u - w), mm(v - d), 0.0,
           mm(u + w), mm(v + d), 0.0)
    b.call("doc", "ClearSelection2", True)
    b.call("sm", "InsertSketch", True)
    sk = b.call("doc", "FeatureByPositionReverse", 0, cast="IFeature")
    name = b.get(sk, "Name")
    b.call("doc", "ClearSelection2", True)
    sel = b.call("ext", "SelectByID2", name, "SKETCH", 0, 0, 0, False, 0, None, 0)
    b.guard(sel, "truthy", "boss_on: did not select the boss sketch")
    feat = _extrusao(b, alt, bool(p.get("reverse", False)), True)
    b.guard(feat, "not_null",
            f"boss_on: the extrusion failed. On face '{face_kind}' the center `at` is "
            f"the pair ({pair}) in mm, measured from the model origin -- check that the "
            f"fits INSIDE the face")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.get(feat, "Name"), p)


def get_material(p: dict) -> dict:
    b = B("part.get_material")
    return b.build(b.call("part", "GetMaterialPropertyName2", str(p.get("config", ""))), p)


# ── inspection ──────────────────────────────────────────────────────────────────
def mass(p: dict) -> dict:
    b = B("inspect.mass")
    mp = b.call("ext", "CreateMassProperty", cast="IMassProperty")
    b.guard(mp, "not_null", "CreateMassProperty returned Nothing (document with no solid?)")
    return b.build({
        "mass_kg":   b.get(mp, "Mass"),
        "volume_m3": b.get(mp, "Volume"),
        "area_m2":   b.get(mp, "SurfaceArea"),
        "com_mm":    {"$ref": b.get(mp, "CenterOfMass").lstrip("$"), "scale": 1000.0},
    }, p)


def dimensions(p: dict) -> dict:
    """The part NAMED dimensions, with the value in mm -- `set_dimension` address.

    Without this list the model knew how to CHANGE a dimension and not WHICH dimension
    exists. It returns, per dimension: `name` (in the short form `part.set_dimension`
    accepts), `value_mm`, the owning `feature` and its `kind`. A through cut has no
    dimension, and a sketch circle only has one if somebody dimensioned it -- so the
    list is what you can actually revise, not everything you could measure on a part.
    """
    b = B("inspect.dimensions")
    return b.build(b.helper("dimensions"), p)


def list_features(p: dict) -> dict:
    b = B("inspect.list_features")
    return b.build(b.helper("list_features"), p)


def bodies(p: dict) -> dict:
    b = B("inspect.bodies")
    return b.build(B.handle(b.helper("bodies")), p)


def faces(p: dict) -> dict:
    b = B("inspect.faces")
    return b.build(B.handle(b.helper("faces", body=p.get("body"))), p)


def edges(p: dict) -> dict:
    b = B("inspect.edges")
    return b.build(B.handle(b.helper("edges", body=p.get("body"))), p)


def document(p: dict) -> dict:
    """Which document is open: `part`, `assembly`, `drawing` or `none`.

    It is the domain ADDRESS. Without it every document was read as a PART, and in an
    assembly three of the five reads fail in COM -- so the affordance said "there is no
    solid in the document yet, create the part first" INSIDE an assembly with two
    components (measured 2026-08-19). It also returns `title` and `path`, which are
    what `asm.mate_planes` needs to build the qualified reference."""
    b = B("inspect.document")
    return b.build(b.helper("document"), p)


def find_face_at(p: dict) -> dict:
    """Face plana by ORIENTACAO + PONTO -- desempata faces coplanares separadas.

    `find_face` returns "the face of maximum y", which is ambiguous when there are two
    at that height (two bosses). Here, with `point_mm`, the nearest one to the point
    wins. Use the OUTWARD normal: axis+want_max describe where the face points,
    not where it is."""
    b = B("inspect.find_face_at")
    ref = b.helper("find_face_at", axis=int(p.get("axis", 1)),
                   want_max=bool(p.get("want_max", True)),
                   point_mm=p.get("point_mm"), body=p.get("body"))
    b.guard(ref, "not_null",
            f"no face pointing to {'+' if p.get('want_max', True) else '-'}"
            f"{'XYZ'[int(p.get('axis', 1))]}")
    return b.build(B.handle(ref), p)


def find_face(p: dict) -> dict:
    """Find a face by GEOMETRY (no fragile coordinate). Returns a reusable HANDLE."""
    b = B("inspect.find_face")
    ref = b.helper("find_face", kind=str(p.get("kind", "plane")),
                   axis=int(p.get("axis", 1)), want_max=bool(p.get("want_max", True)),
                   radius_mm=p.get("radius_mm"), body=p.get("body"))
    b.guard(ref, "not_null",
            f"no '{p.get('kind', 'plane')}' face found with that criterion")
    return b.build(B.handle(ref), p)


def edges_info(p: dict) -> dict:
    """Edges WITH geometry: handle + straight? + point and direction (mm).

    `find_edge` returns the first straight edge of an axis -- fine as a pattern
    direction. When the choice is POSITIONAL (the reentrant root of a slot, the top
    edge of one side only), the whole list is needed to filter. Without it, only a
    fragile coordinate would be left."""
    b = B("inspect.edges_info")
    ref = b.helper("edges_info", body=p.get("body"))
    b.guard(ref, "nonempty", "the body has no edges (is there a solid in the document?)")
    return b.build({"$ref": ref.lstrip("$")}, p)


def faces_info(p: dict) -> dict:
    """Faces WITH geometry: handle + kind (planar/cylindrical) + normal + box + area.

    `faces` returns only handles, indistinguishable from each other; it was measured
    that this makes the model repeat the same query without learning anything. Here
    comes what allows a DECISION: the normal says where the face points, the box (mm,
    in global coordinates) says where it starts and ends, and the radius identifies an
    existing hole."""
    b = B("inspect.faces_info")
    ref = b.helper("faces_info", body=p.get("body"))
    b.guard(ref, "nonempty", "the body has no faces (is there a solid in the document?)")
    return b.build({"$ref": ref.lstrip("$")}, p)


def find_edge(p: dict) -> dict:
    b = B("inspect.find_edge")
    ref = b.helper("find_edge", axis=int(p.get("axis", 0)), body=p.get("body"))
    b.guard(ref, "not_null", f"no straight edge on axis {p.get('axis', 0)}")
    return b.build(B.handle(ref), p)


def screenshot(p: dict) -> dict:
    b = B("inspect.screenshot")
    return b.build(b.helper("screenshot", path=str(_req(p, "path")),
                            width=int(p.get("width", 1200)),
                            height=int(p.get("height", 900))), p)


VERBS = {
    "part.new_part": new_part, "part.open_part": open_part, "part.save": save,
    "part.export": export, "part.rebuild": rebuild,
    "part.sketch": sketch, "part.sketch_begin": sketch_begin,
    "part.sketch_add": sketch_add, "part.sketch_end": sketch_end,
    "part.sketch_relation": sketch_relation, "part.sketch_dimension": sketch_dimension,
    "part.extrude": extrude, "part.revolve": revolve, "part.sweep": sweep,
    "part.loft": loft, "part.fillet": fillet, "part.chamfer": chamfer,
    "part.fillet_on": fillet_on, "part.chamfer_on": chamfer_on,
    "part.hole": hole, "part.countersink_cone": countersink_cone,
    # the coarse layer: one intent = one call (see the comment block above)
    "part.block": block, "part.cylinder": cylinder,
    "part.fillet_all": fillet_all, "part.chamfer_all": chamfer_all,
    "part.hole_on": hole_on, "part.boss_on": boss_on,
    "part.pattern_linear": pattern_linear, "part.pattern_circular": pattern_circular,
    "part.pattern_mirror": pattern_mirror,
    "part.reference_plane": reference_plane, "part.axis_from_face": axis_from_face,
    "part.reference_axis": reference_axis, "part.reference_point": reference_point,
    "part.reference_csys": reference_csys,
    "part.shell": shell, "part.scale": scale, "part.combine": combine,
    "part.delete_body": delete_body, "part.delete_feature": delete_feature,
    "part.draft": draft,
    "part.get_dimension": get_dimension, "part.set_dimension": set_dimension,
    "part.set_dimensions": set_dimensions, "part.set_tolerance": set_tolerance,
    "part.add_equation": add_equation, "part.equations": equations,
    "part.set_property": set_property, "part.get_property": get_property,
    "part.set_material": set_material, "part.get_material": get_material,
    "inspect.mass": mass, "inspect.list_features": list_features,
    "inspect.document": document,
    "inspect.dimensions": dimensions,
    "inspect.bodies": bodies, "inspect.faces": faces, "inspect.edges": edges,
    "inspect.find_face": find_face, "inspect.find_edge": find_edge,
    "inspect.find_face_at": find_face_at,
    "inspect.edges_info": edges_info, "inspect.faces_info": faces_info,
    "inspect.screenshot": screenshot,
}
