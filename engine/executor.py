"""
executor.py -- executes bundles of SOLIDWORKS COM operations.

Targets (resolved AT THE MOMENT of each op, because the active document changes midway):
  app doc ext sm fm part asm dwg
  $oN   the result of a PREVIOUS op (lives only inside the bundle)
  @hN   HANDLE: a COM object kept between bundles (a face found in one verb and used in
        the next verb's mate). It is the equivalent of mcp_solidworks' handle store.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
from pathlib import Path

import pythoncom

from . import allowlist

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sw-engine")
_com_ready = False

# handles live as long as the executor process lives (one working session)
_handles: dict[str, object] = {}
_handle_seq = 0


def _on_com_thread(fn, *args, **kwargs):
    """COM is apartment-threaded: everything on one thread (same reason as
    mcp_solidworks)."""
    def runner():
        global _com_ready
        if not _com_ready:
            pythoncom.CoInitialize()
            _com_ready = True
        return fn(*args, **kwargs)
    return _pool.submit(runner).result()


class BundleError(RuntimeError):
    """A failure running the bundle (guard violated, COM refused, etc.)."""


def _C():
    from solidworks import sw_com as C
    return C


def _is_com(value) -> bool:
    return hasattr(value, "_oleobj_")


def _store_handle(obj) -> str:
    global _handle_seq
    _handle_seq += 1
    key = f"h{_handle_seq}"
    _handles[key] = obj
    return f"@{key}"


def clear_handles() -> int:
    n = len(_handles)
    _handles.clear()
    return n


# A handle that is no longer in the table is, almost always, a handle from an earlier
# step: the document was closed (hygiene between cases) and the table was discarded with
# it. The wording is deliberately the SAME as for a disconnected COM object --
# the model lives the same fact, and an error that reaches the model has to say WHAT TO
# DO, not on which side of the fix it was detected.
_DEAD_HANDLE = (
    "that handle is from an EARLIER step and no longer exists (the document was "
    "closed). A handle does not cross steps. The right move is almost always to OMIT the "
    "argument (the verb falls back to the single body on its own); if you really need the "
    "object, call inspect_bodies / inspect_faces_info NOW and use the handle that comes "
    "back from there.")


def handle_count() -> int:
    return len(_handles)


# ── targets and references ────────────────────────────────────────────────────
def _resolve_target(alias: str, refs: dict):
    C = _C()
    if alias.startswith("$"):
        key = alias[1:]
        if key not in refs:
            raise BundleError(f"unknown reference: {alias}")
        return refs[key]
    if alias.startswith("@"):
        key = alias[1:]
        if key not in _handles:
            raise BundleError(f"unknown handle: {alias} -- {_DEAD_HANDLE}")
        return _handles[key]
    if alias == "app":
        return C.app()
    if alias == "doc":
        return C.active("IModelDoc2")
    if alias == "part":
        return C.active("IPartDoc")
    if alias == "asm":
        return C.active("IAssemblyDoc")
    if alias == "dwg":
        return C.active("IDrawingDoc")
    md = C.active("IModelDoc2")
    if alias == "ext":
        return C.cast(md.Extension, "IModelDocExtension")
    if alias == "sm":
        return C.cast(md.SketchManager, "ISketchManager")
    if alias == "fm":
        return C.cast(md.FeatureManager, "IFeatureManager")
    if alias == "selmgr":
        return C.cast(md.SelectionManager, "ISelectionMgr")
    raise BundleError(f"unknown target alias: '{alias}'")


def _subst(value, refs: dict):
    """Swap "$oN"/"@hN" for the real object. None stays PURE None (early binding)."""
    if isinstance(value, str):
        if value.startswith("$"):
            key = value[1:]
            if key not in refs:
                raise BundleError(f"unknown reference in argument: {value}")
            return refs[key]
        if value.startswith("@h"):
            key = value[1:]
            if key not in _handles:
                raise BundleError(
                    f"unknown handle in argument: {value} -- {_DEAD_HANDLE}")
            return _handles[key]
        return value
    if isinstance(value, list):
        return [_subst(v, refs) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, refs) for k, v in value.items()}
    return value


# ── aggregate primitives (loops and bulk reads; local execution) ─────────────────────
def _bodies(body_ref=None):
    C = _C()
    if body_ref is not None:
        return [body_ref]
    bs = C.active("IPartDoc").GetBodies2(0, False)
    return [C.cast(b, "IBody2") for b in (bs or [])]


def _h_resolve_plane(a, refs):
    C = _C()
    return C.resolve_plane(C.active("IModelDoc2"), str(a.get("plane", "Front")))


def _h_list_features(a, refs):
    C = _C()
    md = C.active("IModelDoc2")
    out, raw = [], md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        out.append({"name": f.Name, "type": f.GetTypeName2()})
        raw = f.GetNextFeature()
    return out


def _h_bodies(a, refs):
    return _bodies()


def _not_a_body(C, raw):
    """Say WHAT came in, not just that it is wrong -- the sentence has to be concrete."""
    what_it_is = "is not a body"
    for interface, member, label in (("IFace2", "GetArea", "is a FACE"),
                                     ("IEdge", "GetCurve", "is an EDGE")):
        try:
            getattr(C.cast(raw, interface), member)()
            what_it_is = label
            break
        except pythoncom.com_error:
            continue
    return (f"`body=` was given a handle that {what_it_is}. What to do: OMIT `body` -- the "
            f"verb falls back to the document's single body on its own, which is what you "
            f"want almost always. If the part has more than one body, call "
            f"`inspect_bodies` and use a handle FROM THERE (the ones from "
            f"`inspect_edges`/`inspect_faces` will not do).")


def _body_of(a):
    """The body from `body=`, VALIDATED -- or the sentence that says what to do.

    `C.cast` wraps WITHOUT validating: casting an EDGE to IBody2 passes silently and only
    the following `GetFaces()` blows up with `Member not found`, which teaches nothing.
    Measured 2026-08-18 (`ministral-3:8b`, two rounds of 9 cases): 74 and 28 occurrences,
    the #1 error of the first -- the model took a handle from `inspect_edges` and passed
    it as `body`, which is a reasonable mistake: the edge list came from the very
    inspection it had just called.

    The probe is `GetFaceCount`, which exists on IBody2 and NOT on IFace2/IEdge.
    """
    C = _C()
    raw = a.get("body")
    if raw is None:
        return C.cast(_bodies()[0], "IBody2")
    body = C.cast(raw, "IBody2")
    try:
        body.GetFaceCount()
    except pythoncom.com_error:
        raise BundleError(_not_a_body(C, raw)) from None
    return body


def _h_faces(a, refs):
    C = _C()
    return [C.cast(f, "IFace2") for f in _body_of(a).GetFaces()]


def _h_edges(a, refs):
    C = _C()
    return [C.cast(e, "IEdge") for e in _body_of(a).GetEdges()]


def _h_find_face(a, refs):
    """Face lookup by GEOMETRY -- a loop over every face, 1 round-trip."""
    C = _C()
    kind = a.get("kind", "plane")
    axis = int(a.get("axis", 1))
    want_max = bool(a.get("want_max", True))
    radius_mm = a.get("radius_mm")
    cand = []
    for f2 in _h_faces({"body": a.get("body")}, refs):
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


def _h_find_edge(a, refs):
    C = _C()
    axis = int(a.get("axis", 0))
    for e2 in _h_edges({"body": a.get("body")}, refs):
        curve = C.cast(e2.GetCurve(), "ICurve")
        if not curve.IsLine():
            continue
        d = curve.LineParams[3:6]
        if abs(d[axis]) > 0.9 and all(abs(d[j]) < 0.1 for j in range(3) if j != axis):
            return e2
    return None


def _describe_face(C, f2):
    """An adjacent face WITHOUT returning the COM object.

    Returning the face would mean one handle per edge per face (24 handles on a cube, and
    hundreds on a real part) just to say "this edge separates the top from the front".
    Axis + position + box identify the face just as well, and they are numbers.
    """
    s = C.cast(f2.GetSurface(), "ISurface")
    box = [v * 1000.0 for v in (f2.GetBox() or [])]
    if not s.IsPlane() or len(box) != 6:
        return {"kind": "cylindrical" if s.IsCylinder() else "other", "box_mm": box}
    n = s.PlaneParams
    sense = -1.0 if f2.FaceInSurfaceSense() else 1.0       # see `_h_faces_info`
    outward = [n[i] * sense for i in range(3)]
    # a SLANTED planar face has no degenerate axis; without the normal it becomes just
    # "planar" and the six sides of a hexagon end up with the same edge name.
    axis = next((k for k in range(3) if abs(box[k + 3] - box[k]) < 1e-4), None)
    return {"kind": "planar", "axis": axis,
            "pos_mm": box[axis] if axis is not None else None,
            "outward_normal": [round(v, 4) for v in outward],
            "box_mm": box}


def _h_edges_info(a, refs):
    """Every edge WITH each one's geometry, in a single round-trip.

    `find_edge` only returns the FIRST straight edge on an axis; picking an edge by
    position (the reentrant root of a slot, say) needs the whole list with direction and
    point. The COM object travels along -> the verb's `returns` turns it into a handle.

    Besides point and direction, the LENGTH and the TWO ADJACENT FACES come back. The
    adjacent faces are what lets an edge be called "top|front" instead of "@h7": a stable
    name, one the model can repeat, and one that does not depend on the order the edges
    came back in. The length is what limits a fillet's radius.
    """
    C = _C()
    out = []
    for e2 in _h_edges({"body": a.get("body")}, refs):
        curve = C.cast(e2.GetCurve(), "ICurve")
        straight = bool(curve.IsLine())
        lp = curve.LineParams if straight else None
        # GetCurveParams2 -> 11 doubles; [0:3] start and [3:6] end, in METERS (measured)
        cp = e2.GetCurveParams2()
        p0 = [v * 1000.0 for v in cp[0:3]]
        p1 = [v * 1000.0 for v in cp[3:6]]
        length = sum((x - y) ** 2 for x, y in zip(p0, p1)) ** 0.5
        adj = e2.GetTwoAdjacentFaces() or ()
        out.append({
            "edge": e2,
            "straight": straight,
            "point_mm": [lp[0] * 1000.0, lp[1] * 1000.0, lp[2] * 1000.0] if straight else None,
            "direction": [lp[3], lp[4], lp[5]] if straight else None,
            "start_mm": p0, "end_mm": p1,
            "length_mm": length if straight else None,   # a curve: the chord would mislead
            "adj_faces": [_describe_face(C, C.cast(f, "IFace2")) for f in adj],
        })
    return out


def _h_faces_info(a, refs):
    """Every face WITH each one's geometry, in a single round-trip.

    The analogue of `edges_info` for FACES, and it exists because of a measured failure:
    `faces` returns only the handles (`@h1, @h2...`), with nothing to tell them apart. On
    2026-08-15 the local model called `inspect.faces` ELEVEN times in a row trying to
    work out where a hole would fit -- and there was nothing to learn in any of the
    answers, because the answer carries no information. With normal, box and area, the
    caller can say WHICH face each one is and WHERE it is possible to drill.

    Returns measured geometry. Naming a face ('top') and deciding where an operation
    is valid belong to the code consuming these measurements.
    """
    C = _C()
    out = []
    for f2 in _h_faces({"body": a.get("body")}, refs):
        s = C.cast(f2.GetSurface(), "ISurface")
        box = [v * 1000.0 for v in (f2.GetBox() or [])]
        center = ([(box[i] + box[i + 3]) / 2000.0 for i in range(3)]
                  if len(box) == 6 else [0.0, 0.0, 0.0])
        # a point GUARANTEED to lie on the face (the box center can fall outside it, on an
        # L-shaped face or one with a hole). It is what identifies WHICH face when two are
        # coplanar and separate -- the case where the name 'top' alone is ambiguous.
        p = f2.GetClosestPointOn(*center)
        info = {
            "face": f2,                       # _jsonify turns it into a handle
            "kind": "other",
            "normal": None,
            "outward_normal": None,
            "radius_mm": None,
            "box_mm": box or None,
            "point_mm": [p[0] * 1000.0, p[1] * 1000.0, p[2] * 1000.0] if p else None,
            "area_mm2": f2.GetArea() * 1e6,
        }
        if s.IsPlane():
            info["kind"] = "planar"
            n = s.PlaneParams          # (nx,ny,nz, px,py,pz)
            info["normal"] = [n[0], n[1], n[2]]
            # The SURFACE's normal points INTO the solid: measured, the face at x=0 of a
            # block comes back with [1,0,0]. `FaceInSurfaceSense` says whether the face
            # uses the surface in the forward sense, and the sign is its INVERSE --
            # measured face by face: with `True -> +1` the top of a boss (y=30) came out
            # labelled 'bottom' and the base (y=0) came out 'top', inverted in every case.
            # Without this flag, two opposite faces of a hexagon arrive with the SAME raw
            # normal and become indistinguishable.
            sense = -1.0 if f2.FaceInSurfaceSense() else 1.0
            info["outward_normal"] = [n[0] * sense, n[1] * sense, n[2] * sense]
        elif s.IsCylinder():
            info["kind"] = "cylindrical"
            cp = s.CylinderParams              # (px,py,pz, ax,ay,az, radius)
            info["radius_mm"] = cp[6] * 1000.0
            # The AXIS comes from the surface, not from the box. Deducing it from the box
            # (the largest extent) is wrong for a hole WIDER than it is deep: a D20 in a
            # 10 mm plate has a box of [20,20,10] and the "axis" would come out X.
            info["cyl_axis"] = [cp[3], cp[4], cp[5]]
            # CONVEX = material INSIDE the cylinder (a boss, a corner fillet).
            # CONCAVE = material OUTSIDE (a hole). Without this distinction the host was
            # guessing from the slenderness, and got it wrong BOTH ways: it missed wide
            # shallow holes, and read a long corner fillet as if it were a hole.
            info["convex"] = not bool(f2.FaceInSurfaceSense())
        out.append(info)
    return out


def _h_find_face_at(a, refs):
    """Face by ORIENTATION (+ an optional POINT to break the tie).

    `find_face` picks by the box's extreme, and with that two coplanar and SEPARATE faces
    (two bosses at the same height) are both "the face of maximum y" -- it returns one of
    them silently. Here, when `point_mm` comes in, the face of that orientation whose
    point is nearest wins: the choice becomes the caller's.

    Without `point_mm` the behaviour is the old one (the extreme), so as not to break
    existing users.
    """
    C = _C()
    axis = int(a.get("axis", 1))
    sign = 1.0 if a.get("want_max", True) else -1.0
    target = a.get("point_mm")
    cands = []
    for f2 in _h_faces({"body": a.get("body")}, refs):
        s = C.cast(f2.GetSurface(), "ISurface")
        if not s.IsPlane():
            continue
        n = s.PlaneParams
        sense = -1.0 if f2.FaceInSurfaceSense() else 1.0   # see `_h_faces_info`
        outward = [n[i] * sense for i in range(3)]
        if outward[axis] * sign < 0.9 or any(abs(outward[j]) > 0.1
                                             for j in range(3) if j != axis):
            continue
        box = [v * 1000.0 for v in (f2.GetBox() or [0] * 6)]
        center = [(box[i] + box[i + 3]) / 2000.0 for i in range(3)]
        p = f2.GetClosestPointOn(*center)
        cands.append(([p[0] * 1000.0, p[1] * 1000.0, p[2] * 1000.0], f2))
    if not cands:
        return None
    if target:
        return min(cands, key=lambda t: sum((x - y) ** 2
                                            for x, y in zip(t[0], target)))[1]
    cands.sort(key=lambda t: t[0][axis])
    return cands[-1][1] if sign > 0 else cands[0][1]


def _h_select(a, refs):
    """Select entities using optional selection marks supplied by the bundle
    (direction=1, seed=4, mirror=2...)."""
    C = _C()
    md = C.active("IModelDoc2")
    # ONE level of flattening: a verb that selects "the fixed face and then every bend
    # edge" has the face as a single ref and the edges as a LIST ref, and a bundle cannot
    # concatenate them itself (convert to sheet metal, corner trim).
    ents = []
    for e in (a.get("entities") or []):
        ents.extend(e) if isinstance(e, (list, tuple)) else ents.append(e)
    marks = a.get("marks") or []
    if a.get("clear", True):
        md.ClearSelection2(True)
    selmgr = C.cast(md.SelectionManager, "ISelectionMgr")
    n = 0
    for i, e in enumerate(ents):
        append = (i > 0) or (not a.get("clear", True))
        mark = marks[i] if i < len(marks) else (marks[0] if len(marks) == 1 else None)
        data = None
        if mark is not None:
            data = selmgr.CreateSelectData()
            data.Mark = int(mark)
        if C.cast(e, "IEntity").Select4(append, data):
            n += 1
    return n


def _h_select_edges(a, refs):
    """Select EDGES by curvature and by adjacent face -- with no handle in the model's
    hands.

    It exists because a hole's rim was UNREACHABLE through the flattened path. The only
    edge-finding verb was `find_edge(axis)`, which by construction only returns STRAIGHT
    edges -- so "round the edges of the circle" had nowhere to go. Measured 2026-08-16:
    the model called `find_edge(axis=1)`, got a vertical edge of the block and rounded a
    CORNER, with the checker approving (the volume went down, which was all the gate
    looked at).

    `only`: "curved" (hole and radius rims), "straight" (corners), "all".
    `axis`/`want_max`: optional, restricts to the edges touching the face of that
    orientation -- "the edges of the hole ON TOP" is (only='curved', axis=1,
    want_max=True).
    """
    C = _C()
    md = C.active("IModelDoc2")
    kind = str(a.get("only", "all")).lower()
    axis = a.get("axis")
    want_max = a.get("want_max", True)
    chosen = []
    for e in _h_edges_info({"body": a.get("body")}, refs):
        if (kind == "curved" and e["straight"]) or (kind == "straight" and not e["straight"]):
            continue
        if axis is not None:
            # the edge "belongs" to the face if one of its two neighbours is planar and
            # points that way. The rim of a through hole in Y has, on each side, the
            # cylindrical face and a plane -- and it is the plane that addresses it.
            sign = 1.0 if want_max else -1.0
            touches = False
            for d in (e.get("adj_faces") or []):
                n = d.get("outward_normal")
                if d.get("kind") != "planar" or not n:
                    continue
                if n[int(axis)] * sign > 0.9 and all(
                        abs(n[j]) < 0.1 for j in range(3) if j != int(axis)):
                    touches = True
                    break
            if not touches:
                continue
        chosen.append(e["edge"])
    if not chosen:
        return 0
    md.ClearSelection2(True)
    return _h_select({"entities": chosen}, refs)


def _h_select_bodies(a, refs):
    C = _C()
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    n = 0
    for i, b in enumerate(a.get("bodies") or []):
        if C.cast(b, "IBody2").Select2(i > 0, None):
            n += 1
    return n


def _h_combine(a, refs):
    """InsertCombineFeature requires a VARIANT array of dispatch -- pure marshaling."""
    C = _C()
    tools = a.get("tools") or []
    main = a.get("main")
    arr = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_DISPATCH,
                          [C.cast(b, "IBody2")._oleobj_ for b in tools])
    main_raw = C.cast(main, "IBody2")._oleobj_ if main is not None else None
    pd = C.cast(C.active("IPartDoc"), "IPartDoc")
    return bool(pd.InsertCombineFeature(int(a["operation"]), main_raw, arr))


def _h_relation(a, refs):
    """AddRelation also wants a VARIANT array; the relation's ENUM comes from the compiler."""
    C = _C()
    sm = C.cast(C.active("IModelDoc2").SketchManager, "ISketchManager")
    sk = C.cast(sm.ActiveSketch, "ISketch")
    rm = C.cast(sk.RelationManager, "ISketchRelationManager")
    arr = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_DISPATCH,
                          [e._oleobj_ for e in (a.get("entities") or [])])
    return rm.AddRelation(arr, int(a["kind"])) is not None


def _h_spline(a, refs):
    C = _C()
    sm = C.cast(C.active("IModelDoc2").SketchManager, "ISketchManager")
    arr = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8,
                          [float(v) for v in a.get("points") or []])
    return C.cast(sm.CreateSpline(arr), "ISketchSegment")


def _h_select_sketch_entities(a, refs):
    """Select4 on the CONCRETE INTERFACE (gotcha: re-casting to IEntity gives a Type
    mismatch).

    An object that arrives DYNAMIC (with no cast at its origin) also gives a 'Type
    mismatch' here, because the dynamic path would want VARIANT(VT_DISPATCH, None) in
    `Data` and we pass pure None. Whoever creates the entity is who should cast it
    (`verbs_part._draw`), but the handle may have come from somewhere else -- so we try
    both concrete interfaces before giving up, and the error SAYS WHAT TO DO.
    """
    C = _C()
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    n = 0
    for i, e in enumerate(a.get("entities") or []):
        ok = None
        for attempt in (lambda: e.Select4(i > 0, None),
                        lambda: C.cast(e, "ISketchSegment").Select4(i > 0, None),
                        lambda: C.cast(e, "ISketchPoint").Select4(i > 0, None)):
            try:
                ok = attempt()
                break
            except (pythoncom.com_error, AttributeError):
                continue
        if ok is None:
            raise BundleError(
                f"entity {i} is not selectable as a sketch entity. "
                "Use the handle returned by part.sketch_add (line, rectangle, circle, "
                "arc, point) in the sketch that is still OPEN; a face/edge/body handle "
                "will not do for a sketch dimension or relation.")
        if ok:
            n += 1
    return n


def _h_load_component(a, refs):
    """AddComponent5 requires the document LOADED -- inserting from a CLOSED file returns
    Nothing. Opens it silently and RESTORES the active document (otherwise the `asm` alias
    would start resolving to the part instead of the assembly)."""
    C = _C()
    sw = C.app()
    path = str(a["path"])
    if sw.GetOpenDocumentByName(path) is not None:
        return False                       # already loaded
    current = C.active("IModelDoc2").GetTitle()
    # OpenDoc6 requires all SIX arguments, INCLUDING the Errors/Warnings out-params, even
    # under early bindings: with four, SW 2017 raises "Type mismatch". It was the
    # same defect as `part.open_part`, fixed there on 2026-08-18 and forgotten here --
    # and the effect was worse: `asm.add_component` of a CLOSED part always failed, and
    # assembly only worked with the parts already open by chance (measured 2026-08-19).
    sw.OpenDoc6(path, 1, 1, "", 0, 0)      # swDocPART, swOpenDocOptions_Silent
    # Both want the `Errors` out-param -- ActivateDoc3(Name, UseUserPreferences,
    # Option, Errors) and ActivateDoc2(Name, Silent, Errors). Without it BOTH raise, the
    # `except` swallowed it, and the active document ended up being the newly opened
    # PART: the `asm` alias started resolving to it and the following `AddComponent5`
    # failed with "Invalid number of parameters". Nobody saw it because this path only
    # runs when the part is CLOSED -- and every test up to 2026-08-19 had just saved it.
    for attempt in (lambda: sw.ActivateDoc3(current, False, 0, 0),
                    lambda: sw.ActivateDoc2(current, False, 0)):
        try:
            attempt()
            break
        except Exception:  # noqa: BLE001
            continue
    # the restore is MANDATORY: carrying on with the wrong document active makes the next
    # verb act on the part while thinking it acts on the assembly.
    if C.active("IModelDoc2").GetTitle() != current:
        raise RuntimeError(
            f"load_component: I opened '{path}' but could not get back to '{current}'. "
            f"The active document is now a different one -- close the part and try again.")
    return True


def _h_close_other_doc(a, refs):
    """SaveAs FAILS if the target file is already open in another window (or referenced by
    an open assembly) -> close the previous instance first. Pure plumbing: the decision to
    save stays with the verb."""
    import os
    C = _C()
    sw = C.app()
    path = str(a.get("path") or "")
    if not path:
        return False
    prev = sw.GetOpenDocumentByName(path)
    if prev is None:
        return False
    md = C.active("IModelDoc2")
    if C.cast(prev, "IModelDoc2").GetPathName() != md.GetPathName():
        sw.CloseDoc(os.path.basename(path))
        return True
    return False


def _h_screenshot(a, refs):
    """A BMP of the viewport -- but FRAMED first, otherwise the photo does not show the
    part.

    SaveBMP photographs the viewport as it is: a freshly opened document may be in the
    front view, zoomed for another model, or with the part off screen. A photo like that
    is no use to the judge nor to whoever validates the suite. Isometric + zoom-fit costs
    two methods and makes the image comparable across parts.

    The framing is best-effort ON PURPOSE: if SW refuses (a document with no graphics
    window, a version without the method), the photo still comes out -- the photo is the
    product, the angle is finish.
    """
    C = _C()
    md = C.active("IModelDoc2")
    # TRIMETRIC, not isometric (2026-08-20). In isometric the X and Z axes project
    # symmetrically, so two components offset by (+a, 0, +a) and (-a, 0, -a) land EXACTLY
    # one behind the other: in the `assembly` case the two pins, correctly positioned at
    # [±25, 55, ±25], showed up as ONE -- the same image the wrong assembly, with both
    # stacked at the center, would produce. A validation photo that hides the very error
    # the suite measures validates nothing.
    for attempt in (lambda: md.ShowNamedView2("*Trimetric", 8),
                    lambda: md.ViewDisplayShaded(),
                    lambda: md.ViewZoomtofit2()):
        try:
            attempt()
        except Exception:  # noqa: BLE001  -- angle and finish, never the photo itself
            pass
    md.SaveBMP(a["path"], int(a.get("width", 1200)), int(a.get("height", 900)))
    return a["path"]


def _h_dimensions(a, refs):
    """The part's NAMED dimensions, with their values -- the ADDRESS that was missing for
    revising.

    `set_dimension` always existed, but nothing said WHICH dimensions exist: the planner
    read `features: Boss-Extrude1` and had no way of knowing that this part's thickness is
    called `D1@Boss-Extrude1`. Measured 2026-08-17: "Change the plate's thickness to
    20 mm" returned `[]` REPRODUCIBLY -- not from misunderstanding, from a missing
    address. It is the same diagnosis as the fillet on the wrong edge
    (2026-08-17): the vocabulary existed, the way to point at it did not.

    `FullName` comes with the document attached (`D1@Boss-Extrude1@Part33.Part`) and
    `IModelDoc2.Parameter` -- which is what `part.set_dimension` calls -- wants the SHORT
    form. Hence the cut at the second '@': returning the long name would give a list of
    addresses the next verb does not accept, which is worse than giving no list at all.

    What shows up here is what SolidWorks holds as a display dimension: a `through_all`
    cut does not (it has no depth), and a sketch circle only does if somebody DIMENSIONED
    it. Measured on a 100x60x12 plate with 4 through holes: ONE dimension,
    `D1@Boss-Extrude1` = 12 mm -- the thickness, unambiguously.
    """
    C = _C()
    md = C.active("IModelDoc2")
    out, raw = [], md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        dd = f.GetFirstDisplayDimension()
        while dd is not None:
            dim = C.cast(C.cast(dd, "IDisplayDimension").GetDimension(), "IDimension")
            out.append({"name": "@".join(str(dim.FullName).split("@")[:2]),
                        "value_mm": round(dim.SystemValue * 1000.0, 4),
                        "feature": f.Name, "kind": f.GetTypeName2()})
            dd = f.GetNextDisplayDimension(dd)
        raw = f.GetNextFeature()
    return out


def _h_equations(a, refs):
    C = _C()
    em = C.cast(C.active("IModelDoc2").GetEquationMgr(), "IEquationMgr")
    return [{"index": i, "equation": em.Equation(i), "value": em.Value(i)}
            for i in range(em.GetCount())]


# ── ASSEMBLY primitives (loops over components / transforms) ─────────────────
def _asm():
    return _C().active("IAssemblyDoc")


def _comp_faces(comp):
    C = _C()
    b2 = C.cast(C.cast(comp, "IComponent2").GetBody(), "IBody2")
    return [C.cast(f, "IFace2") for f in b2.GetFaces()]


def _h_comp_faces(a, refs):
    return _comp_faces(a["comp"])


def _h_asm_find_face(a, refs):
    C = _C()
    kind = a.get("kind", "plane")
    axis = int(a.get("axis", 1))
    radius_mm = a.get("radius_mm")
    cand = []
    for f2 in _comp_faces(a["comp"]):
        s = C.cast(f2.GetSurface(), "ISurface")
        if kind == "cylinder":
            if s.IsCylinder():
                if radius_mm is None or abs(s.CylinderParams[6] * 1000.0 - radius_mm) < 0.2:
                    return f2
        elif s.IsPlane():
            n = s.PlaneParams
            if abs(n[axis]) > 0.9 and all(abs(n[j]) < 0.1 for j in range(3) if j != axis):
                cand.append((f2.GetClosestPointOn(0, 0, 0)[axis], f2))
    if not cand:
        return None
    cand.sort(key=lambda t: t[0])
    return cand[-1][1] if bool(a.get("want_max", True)) else cand[0][1]


def _h_faces_perp(a, refs):
    C = _C()
    axis = int(a.get("axis", 0))
    out = []
    for f2 in _comp_faces(a["comp"]):
        s = C.cast(f2.GetSurface(), "ISurface")
        if s.IsPlane():
            n = s.PlaneParams
            if abs(n[axis]) > 0.9 and all(abs(n[j]) < 0.1 for j in range(3) if j != axis):
                out.append((f2.GetClosestPointOn(0, 0, 0)[axis], f2))
    out.sort(key=lambda t: t[0])
    return [f for _, f in out]


def _h_comp_box(a, refs):
    """The component's bounding box IN THE ASSEMBLY (GetBox already comes in assembly
    coordinates). It is what proves positioning -- 'the part is centered in the slot',
    'the pin goes through the whole U' -- without depending on any mate."""
    C = _C()
    bx = C.cast(a["comp"], "IComponent2").GetBox(False, False)
    lo = [bx[0] * 1000.0, bx[1] * 1000.0, bx[2] * 1000.0]
    hi = [bx[3] * 1000.0, bx[4] * 1000.0, bx[5] * 1000.0]
    return {"min_mm": lo, "max_mm": hi,
            "center_mm": [(lo[i] + hi[i]) / 2.0 for i in range(3)],
            "size_mm": [hi[i] - lo[i] for i in range(3)]}


def _h_components(a, refs):
    C = _C()
    return [C.cast(c, "IComponent2") for c in (_asm().GetComponents(False) or [])]


def _h_last_mate_name(a, refs):
    """FeatureByPositionReverse(0) returns the 'Mates' FOLDER -> descend into the
    sub-features."""
    C = _C()
    f = C.cast(C.active("IModelDoc2").FeatureByPositionReverse(0), "IFeature")
    if not f.GetTypeName2().startswith("MateGroup"):
        return f.Name
    sub, last = f.GetFirstSubFeature(), None
    while sub is not None:
        last = C.cast(sub, "IFeature")
        sub = last.GetNextSubFeature()
    return last.Name if last is not None else f.Name


def _h_mate_errors(a, refs):
    C = _C()
    md = C.active("IModelDoc2")
    out, raw = [], md.FirstFeature()
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
                    out.append({"mate": s.Name, "code": int(code), "warning": warn})
                sub = s.GetNextSubFeature()
        raw = f.GetNextFeature()
    return out


def _mate_components(C, feature):
    """The components a mate JOINS -- `IMate2.MateEntity(i).ReferenceComponent`
    (researched 2026-08-25, item "clearance step 4"). The mate's NAME is sometimes
    guessed by the model rather than read (LimitDistance1 vs Distance1 depending on
    whether the previous step included a `limit`) -- the component pair is a direct
    measurement from the CAD, immune to that naming drift."""
    spec = feature.GetSpecificFeature2()
    if spec is None:
        return []
    mate2 = C.cast(spec, "IMate2")
    names = []
    for i in range(mate2.GetMateEntityCount()):
        me = C.cast(mate2.MateEntity(i), "IMateEntity2")
        comp = me.ReferenceComponent
        if comp is not None:
            names.append(C.cast(comp, "IComponent2").Name2)
    return names


def _h_mates(a, refs):
    """The mates the assembly ALREADY has: name, type, component pair and whether they
    are in error.

    The model had no way of SEEING any mate -- only `mate_errors`, which returns the
    broken ones. A list that only shows what went wrong makes what is right look
    nonexistent, and what the model does with what it cannot see is redo it (the phantom
    step of 7k, in assembly).
    """
    C = _C()
    md = C.active("IModelDoc2")
    out, raw = [], md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2().startswith("MateGroup"):
            sub = f.GetFirstSubFeature()
            while sub is not None:
                s = C.cast(sub, "IFeature")
                res = s.GetErrorCode2()
                code = res[0] if isinstance(res, (tuple, list)) else res
                out.append({"name": s.Name, "kind": s.GetTypeName2(),
                            "components": _mate_components(C, s),
                            "error": int(code) if code else 0})
                sub = s.GetNextSubFeature()
        raw = f.GetNextFeature()
    return out


def _h_interferences(a, refs):
    """Material interference between components -- and it CLOSES the detection session.

    `Done()` is not optional hygiene: without it SolidWorks keeps the detection session
    open and THE NEXT SELECTION FAILS. Measured 2026-08-19 with a control: assembling the
    clevis and limiting the rotation works (A); calling `asm_interferences` first, the same
    limit fails with "selection of the two faces failed" (B); with another reader
    (`mate_errors`) first, it works again (C).

    It was the most treacherous defect of this front: a READ verb breaking the following
    WRITE, and with no error at all on the read. It blew up precisely when the graph
    started reading interference at every step -- that is, the quality gate was disabling
    the very step it was supposed to protect.
    """
    C = _C()
    md = C.active("IModelDoc2")
    idm = C.cast(_asm().InterferenceDetectionManager, "IInterferenceDetectionMgr")
    idm.TreatCoincidenceAsInterference = bool(a.get("coincidence_counts", False))
    idm.UseTransform = False
    out = []
    try:
        for itf in (idm.GetInterferences() or []):
            it = C.cast(itf, "IInterference")
            comps = [C.cast(c, "IComponent2").Name2 for c in (it.Components or [])]
            out.append({"volume_mm3": it.Volume * 1e9, "comps": comps})
    finally:
        # the `finally` matters: if the read fails midway, the session would stay open and
        # the damage would show up on the NEXT operation, far from the cause.
        try:
            idm.Done()
        except Exception:  # noqa: BLE001
            pass
        md.ClearSelection2(True)
    return out


def _math():
    C = _C()
    return C.cast(C.app().GetMathUtility(), "IMathUtility")


def _transform(arr16):
    C = _C()
    v = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8, list(arr16))
    return C.cast(_math().CreateTransform(v), "IMathTransform")


def _h_free_translations(a, refs):
    """Nudge + solve: measures which translations were left free (GetRemainingDOFs does
    not marshal through pywin32). Restores the pose at the end."""
    C = _C()
    comp = C.cast(a["comp"], "IComponent2")
    if comp.IsFixed():
        return []
    delta = float(a.get("delta_mm", 3.0)) / 1000.0
    base = list(C.cast(comp.Transform2, "IMathTransform").ArrayData)
    md = C.active("IModelDoc2")
    free = []
    for ax, nm in ((0, "X"), (1, "Y"), (2, "Z")):
        arr = list(base)
        arr[9 + ax] += delta
        comp.SetTransformAndSolve(_transform(arr))
        md.EditRebuild3()
        moved = C.cast(comp.Transform2, "IMathTransform").ArrayData[9 + ax] - base[9 + ax]
        if abs(moved) > delta * 0.5:
            free.append(nm)
        comp.SetTransformAndSolve(_transform(base))
        md.EditRebuild3()
    return free


def _box_center(comp, C):
    """The component's box center IN THE ASSEMBLY, in mm. The position reference.

    A SUPPRESSED component has no box (GetBox returns nothing), and without this
    message the error reaching the model would be a TypeError from inside the executor --
    an error that does not say what to do (invariant 4).
    """
    bx = comp.GetBox(False, False)
    if not bx or len(bx) < 6:
        raise ValueError(
            "the component has no bounding box -- is it SUPPRESSED? "
            "Unsuppress it first (asm_suppress with state=false) and only then move it.")
    return [(bx[i] + bx[i + 3]) * 500.0 for i in range(3)]


def _comp_origin(comp, C):
    """Transform2's translation -- where the part's ORIGIN sits in the assembly, in mm."""
    arr = C.cast(comp.Transform2, "IMathTransform").ArrayData
    return [arr[9 + i] * 1000.0 for i in range(3)]


def _h_move_component(a, refs):
    """Put the component's BOX CENTER at `xyz` (mm) -- the SAME reference as
    `add_component`, as `asm.component_box` and as the affordance.

    WHY IT EXISTS (measured 2026-08-20, in a recorded automation session): assembly had no
    way BACK. After a mate left
    the plate at [-10, 17.5, 10], the model deleted all five mates, exploded, collapsed
    and called `free_translations` -- 27 calls -- and the center NEVER left
    [-10, 17.5, 10]. Deleting a mate does not bring the part back, and no verb moved a
    component: the only way back was to remove and re-insert. It is invariant 6 (every
    rejection needs a way back) missing from the assembly domain.

    UNTIL 2026-08-22 THIS HELPER PUT THE PART'S **ORIGIN** AT `xyz`, and the
    docstring DECLARED that to be "the same reference as add_component". The declaration
    was FALSE, and `probe_reference_xyz.py` measured it on SIX parts: after
    `add_component(xyz)` the BOX CENTER matches `xyz` (on all six, exactly), and after
    `move_component(xyz)` with the same `xyz` the part ended up at
    `xyz + (center - origin)`:

        base [50, 10, -50] · plate [40, 7.5, -40] · pin_smoke [0, 30, 0]
        clevis [30, 20, -30] · pin [31, 0, 0]     · tongue [10, 20, -30]

    None of the six has its origin at the box center, so the error happened on EVERY call
    -- and silently, because the helper itself checked the result against the ORIGIN and
    declared success. There were **207 calls** to `asm_move_component` in the journals
    (the 5th most-called assembly verb), and the `clearance` case asserted in a comment
    that "delete+move and delete+remove+re-insert end at the same center" -- which was
    false by [0, 30, 0] and failed one of the two legitimate paths.

    The lesson is the usual one on this front, now inside the WAY BACK: **a declaration
    treated as a measurement punishes the correct answer.** The fix is to make reality
    match the declaration, because everyone who speaks of position here -- the affordance,
    the judge, the user's request, `component_box` -- speaks of the BOX CENTER.

    What comes back is the position MEASURED after the solve, not the one requested:
    the mates that already exist may hold the part, and in that case it does not go where
    it was asked to. Saying "it moved" without looking would be declaring instead of
    measuring -- which is the project's whole theme.
    """
    C = _C()
    comp = C.cast(a["comp"], "IComponent2")
    if comp.IsFixed():
        raise ValueError(
            "move_component: the component is FIXED and does not move. Release it first "
            "(asm_float), move it, and fix it again (asm_fix) if you still want it "
            "grounded.")
    xyz = a.get("xyz") or [0.0, 0.0, 0.0]
    if len(xyz) != 3:
        raise ValueError(f"move_component: xyz needs 3 numbers in mm, got {xyz!r}")
    center_before = _box_center(comp, C)
    origin_before = _comp_origin(comp, C)
    # the origin->center vector, ALREADY in the assembly's frame: since only the
    # translation changes, it is the same before and after the move.
    off = [center_before[i] - origin_before[i] for i in range(3)]
    arr = list(C.cast(comp.Transform2, "IMathTransform").ArrayData)
    for i in range(3):
        arr[9 + i] = (float(xyz[i]) - off[i]) / 1000.0
    comp.SetTransformAndSolve(_transform(arr))
    C.active("IModelDoc2").EditRebuild3()
    # MEASURE after the solve: the existing mates may have pulled the part back.
    center_after = _box_center(comp, C)
    origin_after = _comp_origin(comp, C)
    requested = [float(v) for v in xyz]
    held = any(abs(center_after[i] - requested[i]) > 0.01 for i in range(3))
    out = {"center_mm": [round(v, 3) for v in center_after],
           "requested_mm": requested,
           "origin_mm": [round(v, 3) for v in origin_after],
           "moved": any(abs(center_after[i] - center_before[i]) > 0.01 for i in range(3))}
    if held:
        out["warning"] = (
            "the part did NOT end up where you asked: the mates that already exist hold "
            "it. Delete the mate that constrains that axis (asm_delete_mate) before "
            "moving, or move only along the axes that are free (asm_free_translations "
            "says which).")
    return out


def _h_cylinder_axis_world(a, refs):
    """CylinderParams comes in PART coordinates -> take it to the ASSEMBLY via
    Transform2."""
    C = _C()
    comp = C.cast(a["comp"], "IComponent2")
    f = _h_asm_find_face({"comp": comp, "kind": "cylinder",
                          "radius_mm": a.get("radius_mm")}, refs)
    if f is None:
        return None
    cp = C.cast(f.GetSurface(), "ISurface").CylinderParams
    m, xf = _math(), C.cast(comp.Transform2, "IMathTransform")
    va = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8, [cp[0], cp[1], cp[2]])
    vd = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8, [cp[3], cp[4], cp[5]])
    pw = C.cast(C.cast(m.CreatePoint(va), "IMathPoint").MultiplyTransform(xf),
                "IMathPoint").ArrayData
    vw = C.cast(C.cast(m.CreateVector(vd), "IMathVector").MultiplyTransform(xf),
                "IMathVector").ArrayData
    # radius_mm comes along: it is the REAL one of the face that was found (not the
    # nominal one asked for) -- that is how you measure the clearance of an already
    # assembled hole/pin fit.
    return {"point_mm": [pw[0] * 1000, pw[1] * 1000, pw[2] * 1000],
            "direction": [vw[0], vw[1], vw[2]],
            "radius_mm": cp[6] * 1000.0}


def _h_sweep_collision_angle(a, refs):
    """Rotate by transform (without creating mates) until interference is found. Restores
    the pose."""
    C = _C()
    comp = C.cast(a["comp"], "IComponent2")
    pt = a["axis_point_mm"]
    d = a["axis_dir"]
    step, amax = int(a.get("step", 15)), int(a.get("amax", 170))
    m = _math()
    md = C.active("IModelDoc2")
    base = C.cast(comp.Transform2, "IMathTransform")
    base_arr = list(base.ArrayData)
    last_ok, hit = 0, None
    from math import radians
    vp = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8,
                         [pt[0] / 1000.0, pt[1] / 1000.0, pt[2] / 1000.0])
    vv = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8, [float(x) for x in d])
    for ang in range(step, amax + 1, step):
        p = C.cast(m.CreatePoint(vp), "IMathPoint")
        v = C.cast(m.CreateVector(vv), "IMathVector")
        rot = C.cast(m.CreateTransformRotateAxis(p, v, radians(ang)), "IMathTransform")
        comp.SetTransformAndSolve(C.cast(rot.Multiply(base), "IMathTransform"))
        md.EditRebuild3()
        if _h_interferences({}, refs):
            hit = ang
            break
        last_ok = ang
    comp.SetTransformAndSolve(_transform(base_arr))
    md.EditRebuild3()
    return {"max_ok_angle": last_ok, "collision_angle": hit}


def _h_mirror_components(a, refs):
    """MirrorComponents2 only creates anything if ComponentOrientations has the SAME
    size."""
    C = _C()
    md = C.active("IModelDoc2")
    comps = [C.cast(c, "IComponent2") for c in (a.get("comps") or [])]
    pf = C.cast(C.cast(md.SelectionManager, "ISelectionMgr").GetSelectedObject6(1, -1),
                "IFeature")
    md.ClearSelection2(True)
    comp_arr = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_DISPATCH,
                               [c._oleobj_ for c in comps])
    orient = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_I4, [0] * len(comps))
    _asm().MirrorComponents2(pf._oleobj_, comp_arr, orient, False, None,
                             False, None, 0, "", "", 0, False, False)
    md.EditRebuild3()
    return True


def _h_remove_component(a, refs):
    """Remove the component and CHECK that it went -- otherwise raise.

    Measured 2026-08-19, on the first assembly driven by the graph: the verb selected,
    called `DeleteSelection2` and returned the component count -- which was still 3. That
    is, it returned, in its own result, the proof that it had deleted nothing, and
    reported success. The model called the same removal EIGHT times in a row, each one
    "successful", and spent the whole step on it.

    It is the same defect as `sw_close_all`: an operation that becomes a no-op and
    says it worked. The check lives here because only here do the BEFORE and the AFTER
    exist.
    """
    C = _C()
    md = C.active("IModelDoc2")
    asm = _asm()
    comp = C.cast(a["comp"], "IComponent2")
    name = comp.Name2
    before = int(asm.GetComponentCount(True))
    md.ClearSelection2(True)
    if not comp.Select4(False, None, False):
        raise ValueError(f"remove_component: could not select '{name}'.")
    md.Extension.DeleteSelection2(int(a.get("option", 0)))
    md.EditRebuild3()
    md.ClearSelection2(True)
    after = int(asm.GetComponentCount(True))
    if after >= before:
        names = [C.cast(c, "IComponent2").Name2
                 for c in (asm.GetComponents(False) or [])]
        raise ValueError(
            f"remove_component: '{name}' is STILL in the assembly (there were {before} "
            f"components, there are {after}). SolidWorks refused the removal without "
            f"saying why -- the path that usually works is to delete first the mates that "
            f"hold that part (asm_mates lists them, asm_delete_mate deletes them) and only "
            f"then remove it. Components right now: {', '.join(names)}.")
    return after


def _h_select_components(a, refs):
    """IComponent2.Select4(Append, Data, SuppressSelectDialog) -- its own signature."""
    C = _C()
    md = C.active("IModelDoc2")
    if a.get("clear", True):
        md.ClearSelection2(True)
    selmgr = C.cast(md.SelectionManager, "ISelectionMgr")
    marks = a.get("marks") or []
    n = 0
    for i, comp in enumerate(a.get("comps") or []):
        data = None
        if i < len(marks) and marks[i] is not None:
            data = selmgr.CreateSelectData()
            data.Mark = int(marks[i])
        if C.cast(comp, "IComponent2").Select4(i > 0, data, False):
            n += 1
    return n


def _h_comp_info(a, refs):
    """Name, grounding and WHERE each component is -- in a single trip to the CAD.

    The box goes in here, rather than in a per-component call, because the assembly
    affordance needs it for ALL of them: saying "clevis-1, pin-1, tongue-1" without saying
    where each one is means giving a name with no address. A 5-part assembly would cost 5
    trips.
    """
    C = _C()
    out = []
    for c in (a.get("comps") or _h_components({}, refs)):
        comp = C.cast(c, "IComponent2")
        info = {"name": comp.Name2, "fixed": bool(comp.IsFixed()),
                "refs": _component_refs(C, comp)}
        # THE FILE PATH (measured 2026-08-24, `mirror` case). The model saw name,
        # grounding, box and references -- never the FILE the instance came from. When
        # the request was "mirror that pin, so there is an identical pin on the other
        # side", it chose a legitimate path (insert a second instance of the same part)
        # and then had to write the `path`... which it had no way of knowing. It wrote
        # "C:/path/pin_smoke.sldprt" -- the PLACEHOLDER from the prompt's example --
        # twice, and gave up on the third try.
        # It was not a lack of vocabulary nor of reasoning: it was a missing DATUM in the
        # reading.
        try:
            info["file"] = comp.GetPathName()
        except Exception:  # noqa: BLE001
            info["file"] = None
        try:
            bx = comp.GetBox(False, False)
            lo = [bx[i] * 1000.0 for i in range(3)]
            hi = [bx[i + 3] * 1000.0 for i in range(3)]
            info["box_mm"] = [round(v, 2) for v in lo + hi]
            info["center_mm"] = [round((lo[i] + hi[i]) / 2.0, 2) for i in range(3)]
            info["size_mm"] = [round(hi[i] - lo[i], 2) for i in range(3)]
        except Exception:  # noqa: BLE001
            # a suppressed or unloaded component has no box -- and that is information,
            # not a reason for the whole read to fail.
            info["box_mm"] = None
        out.append(info)
    return out


def _component_refs(C, comp) -> list:
    """The NAMED references of that component's part: axes and planes.

    It is the address `asm.mate_planes` asks for -- and nothing listed it. Measured
    2026-08-19, in the `clevis` case: the request said to mount the tongue on the pin "by
    its axis", the model wrote `name_b='pivot_axis'` for the PIN (whose axis is called
    `body_axis`) and the mate selected nothing. It had no way of knowing: the name only
    existed inside the part.

    The three default planes are left out: they exist on every part, distinguish nothing
    and would only dilute the list (the same reason `geometry` filters noise).

    /!\\ AND THE NAME IS NOT ENOUGH TO RECOGNISE THEM. The list below used to hold the
    pt-BR names in full ("plano frontal") but only the bare English words ("front") --
    and SolidWorks calls them "Front Plane", "Top Plane", "Right Plane". So on an
    ENGLISH installation all three walked straight past the filter. It surfaced as a
    phantom version difference: the 2026-09-21 battery filed "SW 2017 exposes the three
    default planes, SW 2026 exposes none" as a difference in SolidWorks, when the two
    machines only differed in interface LANGUAGE and one of them was leaking.

    So the defaults are recognised by POSITION as well, which no language changes: in
    every template the three default planes come FIRST in the tree, before the origin
    and before anything anyone created. Name matching stays as the cheap first pass;
    position is what makes it hold in a language nobody here has seen.
    """
    defaults = {"front", "top", "right",
                "front plane", "top plane", "right plane",
                "plano frontal", "plano superior", "plano direito",
                "origin", "origem", "point1", "ponto1"}
    try:
        md = comp.GetModelDoc2()
        if md is None:
            return []
        raw = C.cast(md, "IModelDoc2").FirstFeature()
    except Exception:                                    # noqa: BLE001
        return []                                        # part not loaded: stay silent
    type_map = {"RefAxis": "axis", "RefPlane": "plane", "CoordSys": "csys"}
    out = []
    leading_planes = 0          # how many RefPlanes seen before anything else
    only_datums_so_far = True
    while raw is not None:
        f = C.cast(raw, "IFeature")
        raw_type = f.GetTypeName2()
        t = type_map.get(raw_type)
        if t == "plane" and only_datums_so_far and leading_planes < 3:
            leading_planes += 1                          # a default plane, by position
        elif t:
            out.append({"name": f.Name, "kind": t})
        if raw_type not in ("RefPlane", "OriginProfileFeature"):
            only_datums_so_far = False
        raw = f.GetNextFeature()
    return [r for r in out if r["name"].strip().lower() not in defaults]


def _h_comp_by_name(a, refs):
    """A component by its instance NAME -- the address the model can repeat.

    A handle is what this project takes out of the model's hands: it changes number,
    says nothing and only holds within the step. Whereas "clevis-1" it reads in
    `asm.components`, sees in the tree and repeats in the next step. Accepts the exact name
    ('clevis-1'), the name without the instance suffix ('clevis') when that identifies just
    ONE, and ignores case.

    The error message is built during execution from the actual component
    names: a static message cannot say which components the assembly has.
    """
    C = _C()
    target = str(a.get("instance_name") or "").strip().lower()
    comps = [C.cast(c, "IComponent2") for c in (_asm().GetComponents(False) or [])]
    names = [c.Name2 for c in comps]
    if not target:
        raise ValueError("comp_by_name: empty name. The components of this assembly are: "
                         + (", ".join(names) or "none yet"))
    exact = [c for c in comps if c.Name2.lower() == target]
    if len(exact) == 1:
        return exact[0]
    # without the instance suffix: 'clevis' matches 'clevis-1' -- only when unambiguous
    base = [c for c in comps if c.Name2.lower().rsplit("-", 1)[0] == target]
    if len(base) == 1:
        return base[0]
    if len(base) > 1:
        raise ValueError(
            f"comp_by_name: '{a.get('instance_name')}' matches more than one component "
            f"({', '.join(c.Name2 for c in base)}). Pass the name WITH the instance "
            f"suffix, exactly as it appears.")
    raise ValueError(
        f"comp_by_name: there is no component '{a.get('instance_name')}' in this "
        f"assembly. The ones that exist are: "
        f"{', '.join(names) or 'none yet -- use asm_add_component'}.")


def strip_extension(title: str) -> str:
    """Strip the extension off a document title. PURE on purpose: it is a rule
    the deterministic checks exercise, and a rule that only exists inside a COM call
    cannot be pinned down without CAD."""
    for ext in (".sldasm", ".sldprt", ".slddrw"):
        if title.lower().endswith(ext):
            return title[:-len(ext)]
    return title


def _h_doc_title(a, refs):
    """The document title WITHOUT the extension -- the name SolidWorks uses in the tree.

    MEASURED 2026-08-20, with a control: `IModelDoc2.GetTitle` returns `Assem98` while
    the assembly has NOT been saved, and `tit2.sldasm` -- WITH the extension -- after
    saving. The qualified name of a component reference
    (`pivot_axis@clevis-1@<assembly>`) uses the tree's name, which has no extension. So
    `asm.mate_planes` worked on a new assembly and BROKE on the same assembly once saved,
    with the message "did not select" -- which sends you looking at the datum and the
    component, the two wrong places.

    It was latent because the suite never saved mid-case; archiving artifacts started
    saving per step and the defect showed up in `clevis` on the very first round.
    Continuing an ALREADY SAVED assembly is the most common use case there is.
    """
    return strip_extension(str(_C().active("IModelDoc2").GetTitle() or ""))


def _h_document(a, refs):
    """Which document is open: a part, an assembly or a drawing.

    It is the domain's ADDRESS, and it was missing. The graph read every document as a
    PART: in an assembly `bodies`/`faces_info`/`edges_info` fail in COM ("Invalid number
    of parameters") and the affordance then said "there is no solid in the document yet,
    create the part first" -- inside an assembly with two components (measured
    2026-08-19).
    """
    C = _C()
    type_map = {1: "part", 2: "assembly", 3: "drawing"}
    try:
        md = C.active("IModelDoc2")
    except Exception:                                    # noqa: BLE001
        md = None
    if md is None:
        return {"kind": "none", "title": "", "path": ""}
    t = int(md.GetType())
    return {"kind": type_map.get(t, f"unknown({t})"), "kind_num": t,
            "title": md.GetTitle(), "path": md.GetPathName()}


# ── DRAWING primitives (annotation walks and local installation paths) ───────
def _dwg():
    return _C().active("IDrawingDoc")


def _h_sheetformat_path(a, refs):
    """Locate the standard's .slddrt in the local SOLIDWORKS installation. Derives the root from the default template (independent of the SW
    version)."""
    import os
    C = _C()
    tpl = C.app().GetUserPreferenceStringValue(10)      # swDefaultTemplateDrawing
    base = os.path.dirname(os.path.dirname(tpl))
    langroot = os.path.join(base, "lang")
    langs = (["Portuguese-Brazilian", "english"]
             if a.get("language", "pt") == "pt" else ["english"])
    fname = f"{str(a['size']).rstrip('v').lower()} - {str(a['standard']).lower()}.slddrt"
    tried = []
    for lg in langs:
        p = os.path.join(langroot, lg, "sheetformat", fname)
        tried.append(p)
        if os.path.exists(p):
            return p
    raise BundleError(f"sheet format not found: {tried}")


def _h_delete_other_sheets(a, refs):
    """Deleting a sheet ONLY works through SelectByID2 'SHEET' + DeleteSelection2
    (EditDelete does not)."""
    C = _C()
    md = C.active("IModelDoc2")
    ext = C.cast(md.Extension, "IModelDocExtension")
    keep, n = a["keep"], 0
    for s in list(_dwg().GetSheetNames()):
        if s == keep:
            continue
        md.ClearSelection2(True)
        if ext.SelectByID2(s, "SHEET", 0, 0, 0, False, 0, None, 0):
            ext.DeleteSelection2(0)
            n += 1
    return n


def _h_view_names(a, refs):
    """Names of the views (the 1st from GetFirstView is the SHEET, not a view)."""
    C = _C()
    dwg = _dwg()
    out = []
    raw = dwg.GetFirstView()
    raw = C.cast(raw, "IView").GetNextView() if raw is not None else None
    while raw is not None:
        v = C.cast(raw, "IView")
        out.append(v.GetName2())
        raw = v.GetNextView()
    return out


def _h_count_annotations(a, refs):
    """Count annotations of a TYPE in the active view (GetCenterMarkCount lies on
    SW 2017)."""
    C = _C()
    dwg = _dwg()
    if a.get("view"):
        dwg.ActivateView(a["view"])
    v = C.cast(dwg.ActiveDrawingView, "IView")
    kind = int(a["type"])
    n, ann = 0, v.GetFirstAnnotation2()
    while ann is not None:
        an = C.cast(ann, "IAnnotation")
        if an.GetType() == kind:
            n += 1
        ann = an.GetNext3()
    return n


def _h_pick_edge(a, refs):
    """Pick an EDGE at (x,y) on the sheet; falls back to VERTEX at the same point."""
    C = _C()
    md = C.active("IModelDoc2")
    ext = C.cast(md.Extension, "IModelDocExtension")
    x, y = float(a["x"]) / 1000.0, float(a["y"]) / 1000.0
    if not a.get("append"):
        md.ClearSelection2(True)
    if ext.SelectByID2("", "EDGE", x, y, 0.0, bool(a.get("append")), 0, None, 0):
        return True
    return bool(ext.SelectByID2("", "VERTEX", x, y, 0.0, bool(a.get("append")), 0, None, 0))


def _h_dedupe_dimensions(a, refs):
    """One dimension per model only: import_model_dims(all_views) repeats them across
    views. Selection through IAnnotation.Select2 (Select3 fails)."""
    C = _C()
    dwg = _dwg()
    md = C.active("IModelDoc2")
    seen, removed = set(), 0
    for nm in _h_view_names({}, refs):
        dwg.ActivateView(nm)
        v = C.cast(dwg.ActiveDrawingView, "IView")
        dups, dd = [], v.GetFirstDisplayDimension5()
        while dd is not None:
            ddc = C.cast(dd, "IDisplayDimension")
            fn = C.cast(ddc.GetDimension2(0), "IDimension").FullName
            if fn in seen:
                dups.append(ddc)
            else:
                seen.add(fn)
            dd = ddc.GetNext5()
        for ddc in dups:
            md.ClearSelection2(True)
            if C.cast(ddc.GetAnnotation(), "IAnnotation").Select2(False, 0):
                md.EditDelete()
                removed += 1
    return removed


def _h_count_annotations_result(a, refs):
    return _h_count_annotations(a, refs)


def _h_sheet_info(a, refs):
    """The whole SHEET, measured: its scale, and per view the name, where it is, its own
    scale and HOW MANY dimensions and annotations it has.

    THIS HELPER IS THE DRAWING'S ADDRESS, and without it there is no drawing gate: in a
    drawing document the model does not change -- `inspect_mass` returns volume 0.0 on
    EVERY step (measured 2026-08-19) -- so volume and tree, which are the part criterion,
    measure absolutely nothing. What changes on a sheet is the number of views and of
    dimensions, and until now there was only `view_names`: you could list the views and
    could not count anything inside them. A GATE THAT DOES NOT MEASURE IS NOT A GATE.

    The dimension walk is `GetFirstDisplayDimension5`/`GetNext5` -- the same one
    `dedupe_dimensions` already used; it lives here now because counting is the common
    case and deleting is the rare one.
    """
    C = _C()
    dwg = _dwg()
    md = C.active("IModelDoc2")
    sheet = C.cast(dwg.GetCurrentSheet(), "ISheet")
    try:
        props = sheet.GetProperties2()
        scale = ([float(props[2]), float(props[3])] if props and len(props) > 3
                 else None)
    except Exception:  # noqa: BLE001
        scale = None
    views = []
    for name in _h_view_names({}, refs):
        try:
            dwg.ActivateView(name)
            v = C.cast(dwg.ActiveDrawingView, "IView")
        except Exception:  # noqa: BLE001
            # a view that does not activate is INFORMATION, not a reason for the whole
            # read to fail -- the same rule as the suppressed component in `comp_info`.
            views.append({"name": name, "error": "did not activate"})
            continue
        n_dims, dd = 0, v.GetFirstDisplayDimension5()
        while dd is not None:
            ddc = C.cast(dd, "IDisplayDimension")
            n_dims += 1
            dd = ddc.GetNext5()
        n_ann, ann = 0, v.GetFirstAnnotation2()
        while ann is not None:
            an = C.cast(ann, "IAnnotation")
            n_ann += 1
            ann = an.GetNext3()
        try:
            pos = v.Position
            where = [round(pos[0] * 1000.0, 2), round(pos[1] * 1000.0, 2)]
        except Exception:  # noqa: BLE001
            where = None
        try:
            view_scale = [float(v.ScaleRatio[0]), float(v.ScaleRatio[1])]
        except Exception:  # noqa: BLE001
            view_scale = None
        views.append({"name": name, "at_mm": where, "scale": view_scale,
                      "dims": n_dims, "annotations": n_ann})
    return {"sheet": md.GetTitle() if md is not None else None,
            "scale": scale, "n_views": len(views),
            "total_dims": sum(v.get("dims") or 0 for v in views),
            "total_annotations": sum(v.get("annotations") or 0 for v in views),
            "views": views}


def _h_view_center(a, refs):
    C = _C()
    dwg = _dwg()
    dwg.ActivateView(a["view"])
    pos = C.cast(dwg.ActiveDrawingView, "IView").Position
    return [pos[0] * 1000.0, pos[1] * 1000.0]


def _h_activate_view(a, refs):
    """Activate the view and return the active IView (several verbs operate on it)."""
    C = _C()
    dwg = _dwg()
    dwg.ActivateView(a["view"])
    v = dwg.ActiveDrawingView
    return C.cast(v, "IView") if v is not None else None


def _h_import_model_dims(a, refs):
    """InsertModelAnnotations3 returns an array (or None) -> count it."""
    dwg = _dwg()
    if a.get("view") and not a.get("all_views", True):
        dwg.ActivateView(a["view"])
    res = dwg.InsertModelAnnotations3(0, int(a["mask"]), bool(a.get("all_views", True)),
                                      False, False, False)
    if res is None:
        return 0
    try:
        return len(res)
    except TypeError:
        return 1


# ── SHEET METAL: geometry and tree primitives (loops and bulk reads; local execution) ─
# The family follows the same split as the rest of the file: the LOOP lives here (a
# bundle cannot iterate), the operation parameters come from the bundle. Which interface, which
# attribute, which mark, which argument order and every unit conversion arrive as
# keyword arguments -- nothing in this section knows what a base flange is.
_SM_EPS_FACE = 1.0e-5          # 0.01 mm: inside a sheet wall, above numeric noise


def _sm_feats(nested: bool = False):
    """(feature, is_sub) over the whole tree. A sheet metal BEND is a SUB-feature of the
    flange -- a sweep that only walks GetNextFeature never sees it."""
    C = _C()
    raw = C.active("IModelDoc2").FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        yield f, False
        if nested:
            sub = f.GetFirstSubFeature()
            while sub is not None:
                sf = C.cast(sub, "IFeature")
                yield sf, True
                sub = sf.GetNextSubFeature()
        raw = f.GetNextFeature()


def _h_sm_names(a, refs):
    """Feature NAMES filtered by GetTypeName2 (`type`), optionally including
    sub-features (`nested`) or excluding a type (`exclude`)."""
    want = str(a.get("type", "") or "")
    skip = str(a.get("exclude", "") or "")
    out = []
    for f, _ in _sm_feats(bool(a.get("nested"))):
        tp = f.GetTypeName2()
        if want and tp != want:
            continue
        if skip and tp == skip:
            continue
        out.append(f.Name)
    return out


def _h_sm_feature(a, refs):
    """ONE feature, by `name` or by `type` (`which`: 'first' | 'last'). None if absent."""
    name = str(a.get("name", "") or "")
    want = str(a.get("type", "") or "")
    last = str(a.get("which", "first")) == "last"
    found = None
    for f, _ in _sm_feats(bool(a.get("nested"))):
        if name:
            if f.Name != name:
                continue
        elif want and f.GetTypeName2() != want:
            continue
        found = f
        if not last:
            break
    return found


def _sm_bbox():
    C = _C()
    boxes = [C.cast(b, "IBody2").GetBodyBox() for b in _bodies()]
    if not boxes:
        raise BundleError(
            "the part has no solid body -- create the sheet first (sheet.new_sheet / "
            "sheet.base_flange).")
    lo = [min(c[i] for c in boxes) for i in range(3)]
    hi = [max(c[i + 3] for c in boxes) for i in range(3)]
    return [(hi[i] - lo[i]) * 1000.0 for i in range(3)]


def _sm_state(key: str, type_name: str = ""):
    C = _C()
    if key == "bbox_mm":
        return _sm_bbox()
    if key == "face_count":
        return len(_h_faces({}, {}))
    if key == "edge_count":
        return len(_h_edges({}, {}))
    if key == "body_count":
        return len(_bodies())
    if key == "volume_m3":
        md = C.active("IModelDoc2")
        mp = C.cast(md.Extension, "IModelDocExtension").CreateMassProperty()
        return float(mp.Volume)
    if key == "type_count":
        return sum(1 for f, _ in _sm_feats(True) if f.GetTypeName2() == type_name)
    if key == "has_type":
        return any(f.GetTypeName2() == type_name for f, _ in _sm_feats(True))
    if key == "suppressed":
        f = _h_sm_feature({"type": type_name}, {})
        return None if f is None else bool(f.IsSuppressed())
    if key == "unsuppressed":
        # what answers "is it flat?": the Flat-Pattern feature is born SUPPRESSED, and
        # SW's own bend-state flag was measured lying in both directions
        f = _h_sm_feature({"type": type_name}, {})
        return f is not None and not f.IsSuppressed()
    if key == "path":
        return C.active("IModelDoc2").GetPathName() or ""
    if key == "error_code":
        f = _h_sm_feature({"type": type_name}, {})
        return None if f is None else int(f.GetErrorCode())
    raise BundleError(f"sm_state: unknown key '{key}'")


def _h_sm_state(a, refs):
    """ONE scalar reading of the part's state -- what a guard can assert on.

    `key`: bbox_mm | face_count | edge_count | body_count | volume_m3 | type_count |
    has_type | suppressed | path | error_code. `type` names the feature type when the
    key needs one (the bundle specifies the feature type)."""
    return _sm_state(str(a.get("key", "")), str(a.get("type", "") or ""))


def _h_sm_compare(a, refs):
    """Compare the CURRENT state with an earlier reading -- the GEOMETRIC verdict.

    Half of this family's COM methods return None (or True) whether or not they built
    anything, so the proof has to be the geometry. `mode`: grew | changed | same."""
    before = a.get("before")
    key = str(a.get("key", "bbox_mm"))
    mode = str(a.get("mode", "changed"))
    tol = float(a.get("tol", 1e-6))
    now = _sm_state(key, str(a.get("type", "") or ""))
    if isinstance(now, (list, tuple)):
        prev = list(before or [])
        if len(prev) != len(now):
            return mode == "changed"
        delta = max(abs(now[i] - prev[i]) for i in range(len(now)))
        return delta > tol if mode == "changed" else delta <= tol
    if now is None or before is None:
        return False
    if mode == "grew":
        return now > before + tol
    if mode == "same":
        return abs(now - before) <= tol
    return abs(now - before) > tol


# -- selection by geometry (the sheet metal "eyes") ---------------------------
def _sm_planar(f2) -> bool:
    C = _C()
    return bool(C.cast(f2.GetSurface(), "ISurface").IsPlane())


def _sm_cylindrical(f2) -> bool:
    C = _C()
    return bool(C.cast(f2.GetSurface(), "ISurface").IsCylinder())


def _h_sm_base_face(a, refs):
    """The planar face of LARGEST AREA. On a sheet it is the base flange's face; on an
    ordinary L-shaped solid it is the OUTER one (which `convert` refuses) -- the caller
    is the one that knows which of the two situations it is in."""
    planar = [f for f in _h_faces({"body": a.get("body")}, refs) if _sm_planar(f)]
    if not planar:
        raise BundleError("the part has no planar face (is there a solid?).")
    return max(planar, key=lambda f: f.GetArea())


def _sm_edge_len(e2) -> float:
    p = e2.GetCurveParams2()
    return abs(p[7] - p[6])


def _h_sm_free_edges(a, refs):
    """The face's edges that do NOT yet touch a bend, longest first.

    The criterion is geometric: an edge that already took a flange touches the bend's
    cylinder. That is what lets flanges be chained with no index bookkeeping."""
    C = _C()
    face = a.get("face") or _h_sm_base_face(a, refs)
    min_mm = float(a.get("min_mm", 1.0))
    free = []
    for e in (C.cast(face, "IFace2").GetEdges() or []):
        e2 = C.cast(e, "IEdge")
        adj = [C.cast(x, "IFace2") for x in (e2.GetTwoAdjacentFaces2() or []) if x]
        if any(_sm_cylindrical(o) for o in adj):
            continue
        if _sm_edge_len(e2) * 1000.0 >= min_mm:
            free.append(e2)
    return sorted(free, key=_sm_edge_len, reverse=True)


def _sm_into_face(face, normal, mid, tangent):
    """Unit direction leaving the MIDDLE of the edge and ENTERING the face.

    The sign comes from a test, not a convention: the side that still BELONGS to the
    face (GetClosestPointOn gives the point back). An "interior point of the face" would
    not do -- on an L the box centre falls outside the face."""
    u = (tangent[1] * normal[2] - tangent[2] * normal[1],
         tangent[2] * normal[0] - tangent[0] * normal[2],
         tangent[0] * normal[1] - tangent[1] * normal[0])
    n = sum(x * x for x in u) ** 0.5
    if n < 1e-12:
        return None
    u = [x / n for x in u]
    for s in (1.0, -1.0):
        p = [mid[k] + s * _SM_EPS_FACE * u[k] for k in range(3)]
        q = face.GetClosestPointOn(*p)
        if sum((q[k] - p[k]) ** 2 for k in range(3)) < 1e-18:
            return [s * x for x in u]
    return None


def _h_sm_sharp_bend_edges(a, refs):
    """CONCAVE sharp-corner edges -- the bend candidates of an ordinary solid.

    Concave = material on the INSIDE of the dihedral (an L's or U's inner corner); the
    convex ones do not work as bends. Returns {"edges", "faces", "fixed_face"}, longest
    edge first; `fixed` ('larger'|'smaller') says which of the FIRST edge's two
    neighbouring faces comes back as `fixed_face`."""
    C = _C()
    min_mm = float(a.get("min_mm", 1.0))
    out = []
    for e2 in _h_edges({"body": a.get("body")}, refs):
        adj = [C.cast(x, "IFace2") for x in (e2.GetTwoAdjacentFaces2() or []) if x]
        if len(adj) != 2 or not all(_sm_planar(f) for f in adj):
            continue
        n1, n2 = adj[0].Normal, adj[1].Normal
        if abs(sum(n1[k] * n2[k] for k in range(3))) > 1e-6:
            continue                                   # 90-degree corners only
        cp = e2.GetCurveParams2()
        mid = tuple((cp[i] + cp[i + 3]) / 2.0 for i in range(3))
        t = [cp[i + 3] - cp[i] for i in range(3)]
        nt = sum(x * x for x in t) ** 0.5
        if nt * 1000.0 < min_mm:
            continue
        u2 = _sm_into_face(adj[1], n2, mid, [x / nt for x in t])
        if u2 is None or sum(n1[k] * u2[k] for k in range(3)) <= 1e-9:
            continue
        out.append((e2, adj))
    out.sort(key=lambda r: _sm_edge_len(r[0]), reverse=True)
    if not out:
        return {"edges": [], "faces": [], "fixed_face": None}
    pair = out[0][1]
    bigger = str(a.get("fixed", "larger")) == "larger"
    fixed = (max if bigger else min)(pair, key=lambda f: f.GetArea())
    return {"edges": [r[0] for r in out], "faces": list(pair), "fixed_face": fixed}


def _h_sm_face_point(a, refs):
    """An INTERIOR point of the face, in METERS -- to re-select it by coordinate after
    the IFace2 pointer stops being valid.

    With `line` ([[x,y,z],[x,y,z]] in meters) the point is pushed to ONE SIDE of that
    line: a point landing ON the line leaves SW unable to tell which half to hold."""
    C = _C()
    face = C.cast(a.get("face") or _h_sm_base_face(a, refs), "IFace2")
    box = face.GetBox()
    centre = ((box[0] + box[3]) / 2.0, (box[1] + box[4]) / 2.0, (box[2] + box[5]) / 2.0)
    inside = face.GetClosestPointOn(*centre)[:3]
    line = a.get("line")
    if not line:
        return list(inside)
    p1, p2 = line[0], line[1]
    mid = face.GetClosestPointOn(*[(p1[k] + p2[k]) / 2.0 for k in range(3)])[:3]
    t = [p2[k] - p1[k] for k in range(3)]
    nt = sum(x * x for x in t) ** 0.5
    if nt < 1e-12:
        return list(inside)
    t = [x / nt for x in t]
    n = face.Normal
    u = (t[1] * n[2] - t[2] * n[1], t[2] * n[0] - t[0] * n[2], t[0] * n[1] - t[1] * n[0])
    nu = sum(x * x for x in u) ** 0.5
    if nu < 1e-12:
        return list(inside)
    u = [x / nu for x in u]
    d = float(a.get("fraction", 0.25)) * sum(
        (box[k + 3] - box[k]) ** 2 for k in range(3)) ** 0.5
    best, best_d = None, -1.0
    for s in (1.0, -1.0):
        p = [mid[k] + s * d * u[k] for k in range(3)]
        q = face.GetClosestPointOn(*p)[:3]
        w = [q[k] - p1[k] for k in range(3)]
        proj = sum(w[k] * t[k] for k in range(3))
        dist = sum((w[k] - proj * t[k]) ** 2 for k in range(3)) ** 0.5
        if dist > best_d:
            best, best_d = list(q), dist
    return best if best_d > 1e-9 else list(inside)


def _h_sm_open_corners(a, refs):
    """Corner CANDIDATES: pairs of flange SIDE faces facing each other across a gap.

    Every threshold arrives from the compiler (`thickness_mm`, `area_max_mm2`,
    `max_gap_mm`, `perp_tol`) -- filtering by area alone brings the bend reliefs of the
    same corner in as false corners. They are CANDIDATES: closing a corner does not take
    it off the list, because the two side faces survive the operation."""
    thick = float(a.get("thickness_mm", 0.0))
    limit = float(a.get("max_gap_mm", 0.0)) or (5.0 * thick)
    area_max = float(a.get("area_max_mm2", 200.0))
    perp = float(a.get("perp_tol", 0.2))
    smalls = []
    for f in _h_faces({"body": a.get("body")}, refs):
        if not _sm_planar(f):
            continue
        if f.GetArea() * 1e6 >= area_max:
            continue
        box = f.GetBox()
        dims = sorted((box[k + 3] - box[k]) * 1000.0 for k in range(3))
        if thick and (abs(dims[1] - thick) > 0.1 * thick or dims[2] < 3.0 * thick):
            continue
        smalls.append((f, _h_sm_face_point({"face": f}, refs)))
    out = []
    for i in range(len(smalls)):
        for j in range(i + 1, len(smalls)):
            (fa, pa), (fb, pb) = smalls[i], smalls[j]
            na, nb = fa.Normal, fb.Normal
            if abs(sum(na[k] * nb[k] for k in range(3))) > perp:
                continue
            d = sum((pa[k] - pb[k]) ** 2 for k in range(3)) ** 0.5 * 1000.0
            if d <= limit:
                out.append((d, fa, fb))
    out.sort(key=lambda t: t[0])
    start = int(a.get("from_index", 0))
    return {"a": [r[1] for r in out], "b": [r[2] for r in out],
            "gap_mm": [round(r[0], 4) for r in out],
            # `faces` is the SAME thing already in the order the closing tries them:
            # every candidate's first face, then every candidate's second one
            "faces": [r[1] for r in out[start:]] + [r[2] for r in out[start:]]}


def _h_sm_bend_faces(a, refs):
    """The TWO planar faces forming a bend's CONCAVE corner -- a gusset's support.

    Found through the bend's SMALLEST-radius cylinder (the outer one is radius +
    thickness) and the planar faces adjacent to it. `bend` indexes the cylinders from
    the smallest radius up."""
    C = _C()
    perp = float(a.get("perp_tol", 0.2))
    cands = []
    for f in _h_faces({"body": a.get("body")}, refs):
        s = C.cast(f.GetSurface(), "ISurface")
        if not s.IsCylinder():
            continue
        planar, seen = [], set()
        for e in (f.GetEdges() or []):
            for x in (C.cast(e, "IEdge").GetTwoAdjacentFaces2() or []):
                if x is None:
                    continue
                f2 = C.cast(x, "IFace2")
                if not _sm_planar(f2):
                    continue
                key = tuple(round(v, 6) for v in _h_sm_face_point({"face": f2}, refs))
                if key in seen:
                    continue
                seen.add(key)
                planar.append(f2)
        # the bend's little RELIEF faces also touch the cylinder -- keep the TWO largest,
        # and only if they are perpendicular to each other (that is what makes a corner)
        planar.sort(key=lambda x: x.GetArea(), reverse=True)
        if len(planar) < 2:
            continue
        na, nb = planar[0].Normal, planar[1].Normal
        if abs(sum(na[k] * nb[k] for k in range(3))) > perp:
            continue
        cands.append((s.CylinderParams[6], [planar[0], planar[1]]))
    if not cands:
        raise BundleError("the part has no bend with two planar support faces.")
    cands.sort(key=lambda t: t[0])
    i = int(a.get("bend", 0))
    if i >= len(cands):
        raise BundleError(f"bend {i} does not exist ({len(cands)} candidates).")
    return cands[i][1]


def _h_sm_edge_vertex(a, refs):
    """A straight edge's start (`end`=0) or end (`end`=1) vertex."""
    C = _C()
    e2 = C.cast(a["edge"], "IEdge")
    v = e2.GetStartVertex() if int(a.get("end", 0)) == 0 else e2.GetEndVertex()
    return None if v is None else C.cast(v, "IVertex")


# -- sketches whose coordinates come from the MODEL ---------------------------
def _sm_xform(sketch_feat):
    C = _C()
    s = C.cast(sketch_feat.GetSpecificFeature2(), "ISketch")
    return C.cast(s.ModelToSketchTransform, "IMathTransform").ArrayData


def _sm_to_sketch(m, p):
    x, y, z = p
    return (m[0] * x + m[3] * y + m[6] * z + m[9],
            m[1] * x + m[4] * y + m[7] * z + m[10],
            m[2] * x + m[5] * y + m[8] * z + m[11])


def _sm_vec_to_sketch(m, v):
    x, y, z = v
    return (m[0] * x + m[3] * y + m[6] * z,
            m[1] * x + m[4] * y + m[7] * z,
            m[2] * x + m[5] * y + m[8] * z)


def _h_sm_sketch_lines(a, refs):
    """Open a sketch on a FACE (or on a named PLANE) and draw lines given in MODEL
    coordinates, in METERS. Returns the sketch feature's NAME.

    TWO lines in the same sketch = two bends in one call (the U channel). The projection
    model -> sketch is pure algebra; WHICH lines to draw is the caller's business."""
    C = _C()
    md = C.active("IModelDoc2")
    md.ClearSelection2(True)
    if a.get("plane"):
        C.select_plane(md, str(a["plane"]))
    else:
        face = a.get("face") or _h_sm_base_face(a, refs)
        C.cast(face, "IEntity").Select4(False, None)
    sm = C.cast(md.SketchManager, "ISketchManager")
    sm.InsertSketch(True)
    m = C.cast(C.cast(sm.ActiveSketch, "ISketch").ModelToSketchTransform,
               "IMathTransform").ArrayData
    for p1, p2 in (a.get("lines") or []):
        u, v = _sm_to_sketch(m, p1), _sm_to_sketch(m, p2)
        sm.CreateLine(u[0], u[1], 0.0, v[0], v[1], 0.0)
    md.ClearSelection2(True)
    sm.InsertSketch(True)
    names = _h_sm_names({"type": str(a.get("sketch_type", "ProfileFeature"))}, refs)
    return names[-1] if names else ""


def _h_sm_flange_sketch(a, refs):
    """The edge flange's PROFILE: the closed rectangle that actually defines the flange.

    `InsertSketchForEdgeFlange` hands back an EMPTY sketch -- draw nothing and SW still
    returns an `Edge-Flange1` with no error and no geometry. And the rectangle has to sit
    on the side of the plane OPPOSITE the material, which is why the FACE comes in: the
    sign is its outward normal projected on the sketch's Y axis. Everything in METERS /
    RADIANS. Returns the sketch FEATURE (the flange call needs the object)."""
    C = _C()
    md = C.active("IModelDoc2")
    e2 = C.cast(a["edge"], "IEdge")
    face = C.cast(a.get("face") or _h_sm_base_face(a, refs), "IFace2")
    sk = md.InsertSketchForEdgeFlange(e2, float(a.get("angle", 1.5707963267948966)), False)
    if sk is None:
        raise BundleError(
            "InsertSketchForEdgeFlange returned None -- that edge does not take a flange "
            "(is it an edge of the sheet's face? does it already have a bend?).")
    skf = C.cast(sk, "IFeature")
    m = _sm_xform(skf)
    p = e2.GetCurveParams2()
    pa, pb = _sm_to_sketch(m, p[0:3]), _sm_to_sketch(m, p[3:6])
    sign = 1.0 if _sm_vec_to_sketch(m, face.Normal)[1] >= 0 else -1.0
    margin = float(a.get("margin", 0.0))
    x0, x1 = sorted((pa[0], pb[0]))
    x0, x1 = x0 + margin, x1 - margin
    if x1 <= x0:
        raise BundleError("margin_mm does not fit on the edge (it is wider than the edge).")
    md.ClearSelection2(True)
    ext = C.cast(md.Extension, "IModelDocExtension")
    if not ext.SelectByID2(skf.Name, "SKETCH", 0, 0, 0, False, 0, None, 0):
        raise BundleError(f"could not select the flange sketch '{skf.Name}'.")
    sm = C.cast(md.SketchManager, "ISketchManager")
    sm.InsertSketch(True)
    sm.CreateCornerRectangle(x0, pa[1], 0.0, x1, pa[1] + sign * float(a["length"]), 0.0)
    md.ClearSelection2(True)
    sm.InsertSketch(True)
    return skf


def _h_sm_miter_profile(a, refs):
    """The miter flange's PROFILE: a line leaving the TIP of the edge, on a plane
    perpendicular to it. Returns the sketch NAME.

    The profile has to TOUCH the tip of the edge: two millimetres away and SW refuses in
    silence. Lengths in METERS; the direction is the face's inward normal."""
    C = _C()
    e2 = C.cast(a["edge"], "IEdge")
    face = C.cast(a.get("face") or _h_sm_base_face(a, refs), "IFace2")
    tip = e2.GetCurveParams2()[0:3]
    n = face.Normal
    length = float(a["length"])
    target = [tip[k] - n[k] * length for k in range(3)]
    return _h_sm_sketch_lines({"plane": str(a["plane"]),
                               "lines": [[list(tip), target]]}, refs)


def _h_sm_select_bends(a, refs):
    """The selection unfold/fold require: the fixed FACE with one mark and each BEND
    (a sub-feature, by name, as BODYFEATURE) with another. The marks come from the
    compiler. With the wrong selection SW creates the feature and unfolds nothing."""
    C = _C()
    md = C.active("IModelDoc2")
    face = a.get("fixed_face") or _h_sm_base_face(a, refs)
    md.ClearSelection2(True)
    selmgr = C.cast(md.SelectionManager, "ISelectionMgr")
    sd = selmgr.CreateSelectData()
    sd.Mark = int(a.get("mark_fixed", 1))
    if not C.cast(face, "IEntity").Select4(False, sd):
        raise BundleError("could not select the fixed face.")
    ext = C.cast(md.Extension, "IModelDocExtension")
    n = 0
    for name in (a.get("bends") or []):
        if ext.SelectByID2(str(name), "BODYFEATURE", 0, 0, 0, True,
                           int(a.get("mark_bend", 2)), None, 0):
            n += 1
    return n


def _h_sm_select_segments(a, refs):
    """Select every SEGMENT of the named sketches with the same mark.

    `ISketchSegment` does not cast to `IEntity`: the segment has its own Select4 and the
    mark travels through SelectData. Selecting the sketches as "SKETCH" instead returns
    None from the lofted bend -- it is the ENTITY TYPE and the mark, not the method."""
    C = _C()
    md = C.active("IModelDoc2")
    selmgr = C.cast(md.SelectionManager, "ISelectionMgr")
    md.ClearSelection2(True)
    first, mark = True, int(a.get("mark", 1))
    for name in (a.get("sketches") or []):
        f = _h_sm_feature({"name": str(name)}, refs)
        if f is None:
            raise BundleError(f"sketch '{name}' is not in the tree.")
        sk = C.cast(f.GetSpecificFeature2(), "ISketch")
        for seg in (sk.GetSketchSegments() or []):
            sd = selmgr.CreateSelectData()
            sd.Mark = mark
            if not C.cast(seg, "ISketchSegment").Select4(not first, sd):
                raise BundleError(f"could not select a segment of '{name}'.")
            first = False
    if first:
        raise BundleError("the sketches have no segments at all.")
    return True


def _h_sm_try_closed_corner(a, refs):
    """Try the candidate faces one at a time until the GEOMETRY changes.

    `InsertSheetMetalClosedCorner` takes NO arguments -- it is 100% selection, and the
    face and the mark arrive from the compiler. It returns nothing even when it builds, and
    a corner already closed keeps its side faces, so the verdict is: a new feature of
    `type` AND more volume. Returns the feature name, or None."""
    C = _C()
    md = C.active("IModelDoc2")
    type_name = str(a.get("type", "CornerFeat"))
    mark = int(a.get("mark", 1))
    n0 = _sm_state("type_count", type_name)
    for face in (a.get("faces") or []):
        v0 = _sm_state("volume_m3")
        md.ClearSelection2(True)
        selmgr = C.cast(md.SelectionManager, "ISelectionMgr")
        sd = selmgr.CreateSelectData()
        sd.Mark = mark
        if not C.cast(face, "IEntity").Select4(False, sd):
            continue
        md.InsertSheetMetalClosedCorner()
        md.ClearSelection2(True)
        md.EditRebuild3()
        if _sm_state("type_count", type_name) > n0 and _sm_state("volume_m3") > v0:
            f = _h_sm_feature({"type": type_name, "which": "last"}, refs)
            return None if f is None else f.Name
    return None


# -- feature DEFINITIONS: the read/write funnel -------------------------------
def _sm_definition(C, feature, interface):
    d = feature.GetDefinition()
    if d is None:
        raise BundleError("the feature has no definition (GetDefinition returned None).")
    return C.cast(d, interface) if interface else d


def _sm_read(d, spec) -> dict:
    """`spec` = {out_name: [attribute, scale]} -- the bundle supplies the scale
    used for each unit conversion."""
    out = {}
    for name, how in (spec or {}).items():
        attr = how[0] if isinstance(how, (list, tuple)) else how
        scale = how[1] if isinstance(how, (list, tuple)) and len(how) > 1 else None
        value = getattr(d, attr)
        if (scale is not None and isinstance(value, (int, float))
                and not isinstance(value, bool)):
            value = value * float(scale)
        out[name] = _jsonify(value)
    return out


def _sm_read_calls(d, spec) -> dict:
    out = {}
    for name, how in (spec or {}).items():
        method = how[0] if isinstance(how, (list, tuple)) else how
        call_args = list(how[1]) if isinstance(how, (list, tuple)) and len(how) > 1 else []
        scale = how[2] if isinstance(how, (list, tuple)) and len(how) > 2 else None
        value = getattr(d, method)(*call_args)
        if scale is not None and isinstance(value, (list, tuple)):
            value = [v * float(scale) for v in value]
        elif (scale is not None and isinstance(value, (int, float))
                and not isinstance(value, bool)):
            value = value * float(scale)
        out[name] = _jsonify(value)
    return out


def _sm_allowance_obj(C, d):
    cba = d.GetCustomBendAllowance()
    return None if cba is None else C.cast(cba, "ICustomBendAllowance")


def _h_sm_modify(a, refs):
    """Read and/or WRITE a feature's definition. The executor only does getattr/setattr.

    WHICH feature, WHICH interface, WHICH attribute, in WHAT ORDER and with which
    override call is specified by the bundle -- and it has to be, because half of this
    family LIES: `ModifyDefinition` returns True over `ISheetMetalFeatureData` and
    changes nothing until `SetOverrideDefaultParameter` has been called, and a cast
    through the wrong interface scrambles the values WITHOUT raising.

    args: feature|name|type(+which), interface, access_selections, pre_calls, set,
          allowance{...}, modify, require_modify, rebuild, reread, read{...},
          read_calls{...}, read_allowance{...}.
    """
    C = _C()
    md = C.active("IModelDoc2")
    feature = a.get("feature")
    interface = str(a.get("interface", "") or "")
    if feature is None:
        # `alternatives` is where the SAME reading lives on two different features: the
        # thickness is on the base flange, but a part born from a lofted bend has no base
        # flange and answers through the SheetMetal feature. The bundle specifies the order.
        tries = a.get("alternatives") or [{"name": a.get("name", ""),
                                           "type": a.get("type", ""),
                                           "which": a.get("which", "first"),
                                           "interface": interface}]
        for spec in tries:
            found = _h_sm_feature({"name": spec.get("name", ""),
                                   "type": spec.get("type", ""),
                                   "which": spec.get("which", "first"),
                                   "nested": spec.get("nested", False)}, refs)
            if found is not None:
                feature = found
                interface = str(spec.get("interface", interface) or "")
                break
    if feature is None:
        raise BundleError(str(a.get("missing_msg")
                              or "the feature this verb edits is not in the tree."))
    feature = C.cast(feature, "IFeature")
    d = _sm_definition(C, feature, interface)

    if a.get("access_selections"):
        d.AccessSelections(md, None)
    for call in (a.get("pre_calls") or []):
        getattr(d, call[0])(*list(call[1:]))
    for attr, value in (a.get("set") or {}).items():
        setattr(d, attr, value)
    # `set_if` = "only when the part is in this state": with a gauge table attached, a
    # thickness typed by hand has to declare itself an OVERRIDE, or the file keeps
    # claiming a gauge it no longer measures.
    for cond, attrs in (a.get("set_if") or {}).items():
        if getattr(d, cond):
            for attr, value in attrs.items():
                setattr(d, attr, value)
    allowance = a.get("allowance")
    if allowance:
        cba = _sm_allowance_obj(C, d)
        if cba is None:
            raise BundleError("this bend has no custom allowance "
                              "(GetCustomBendAllowance returned None).")
        for attr, value in allowance.items():
            setattr(cba, attr, value)
        d.SetCustomBendAllowance(cba)

    modified = None
    if a.get("modify"):
        modified = bool(feature.ModifyDefinition(d, md, None))
        if a.get("require_modify") and not modified:
            raise BundleError(str(a.get("refused_msg")
                                  or "ModifyDefinition refused the change."))
        if a.get("rebuild", True):
            md.EditRebuild3()
        if a.get("reread", True):
            again = _h_sm_feature({"name": feature.Name}, refs)
            if again is not None:
                feature = C.cast(again, "IFeature")
                d = _sm_definition(C, feature, interface)

    out = {"name": feature.Name, "modified": modified}
    out.update(_sm_read(d, a.get("read")))
    out.update(_sm_read_calls(d, a.get("read_calls")))
    if a.get("read_allowance"):
        cba = _sm_allowance_obj(C, d)
        out.update(_sm_read(cba, a.get("read_allowance")) if cba is not None
                   else {k: None for k in a["read_allowance"]})
    return out


def _h_sm_allowance(a, refs):
    """Write and/or read the allowance of every bend that accepts one.

    `interfaces` ({feature type: interface}) and `skip_types` come from the compiler --
    the reach is not uniform (the jog takes a K-factor and refuses a bend TABLE, and the
    sketched bend takes neither), and pretending it is would make the part claim an
    allowance it does not have."""
    C = _C()
    md = C.active("IModelDoc2")
    interfaces = a.get("interfaces") or {}
    skip = set(a.get("skip_types") or [])
    write = a.get("write")
    if write and a.get("require_file"):
        if not os.path.isfile(str(a["require_file"])):
            raise BundleError(f"file not found: {a['require_file']}")
    targets, left_out = [], []
    for f, _ in _sm_feats(False):
        tp = f.GetTypeName2()
        if tp not in interfaces:
            continue
        if tp in skip:
            left_out.append(f.Name)
        else:
            targets.append((f.Name, interfaces[tp]))
    if write:
        for name, iface in targets:
            _h_sm_modify({"name": name, "interface": iface,
                          "set": {"UseDefaultBendAllowance": False},
                          "allowance": write, "modify": True, "require_modify": True,
                          "refused_msg": (f"ModifyDefinition refused the allowance of "
                                          f"'{name}'."),
                          "rebuild": True, "reread": False}, refs)
        md.ForceRebuild3(False)
        # An allowance this sheet cannot take does NOT raise: it breaks the FLAT PATTERN,
        # and the part just stays folded -- a state that would only surface the next time
        # somebody tried to flatten it. So it is checked here and UNDONE if it broke.
        check = str(a.get("check_error_type", "") or "")
        if check:
            err = _sm_state("error_code", check)
            if err:
                _h_sm_restore_allowance(
                    {"bends": [n for n, _ in targets],
                     "interfaces": {n: i for n, i in targets}}, refs)
                raise BundleError(str(a.get("error_msg", "the allowance broke {err}"))
                                  .format(err=err))
    out = []
    for name, iface in targets:
        f = C.cast(_h_sm_feature({"name": name}, refs), "IFeature")
        d = _sm_definition(C, f, iface)
        cba = _sm_allowance_obj(C, d)
        out.append({"bend": name,
                    "allowance_type": int(cba.Type) if cba is not None else None,
                    "table": (cba.BendTableFile or "") if cba is not None else "",
                    "k_factor": round(cba.KFactor, 4) if cba is not None else None,
                    "default": bool(d.UseDefaultBendAllowance)})
    return {"bends": out, "names": [n for n, _ in targets],
            "interfaces": {n: i for n, i in targets}, "not_reached": left_out}


def _h_sm_restore_allowance(a, refs):
    """Put the named bends back on the document's DEFAULT allowance -- the way back when
    a table breaks the flat pattern."""
    interfaces = a.get("interfaces") or {}
    for name in (a.get("bends") or []):
        _h_sm_modify({"name": name, "interface": str(interfaces.get(name, "")),
                      "set": {"UseDefaultBendAllowance": True},
                      "modify": True, "rebuild": True, "reread": False}, refs)
    return len(a.get("bends") or [])


def _h_sm_bend_info(a, refs):
    """One record per BEND (a sub-feature), read through the interface the compiler names."""
    C = _C()
    iface = str(a.get("interface", "IOneBendFeatureData"))
    want = str(a.get("type", "OneBend"))
    out = []
    for f, is_sub in _sm_feats(True):
        if not is_sub or f.GetTypeName2() != want:
            continue
        d = _sm_definition(C, f, iface)
        row = {"name": f.Name}
        row.update(_sm_read(d, a.get("read")))
        out.append(row)
    return out


# -- fabrication ---------------------------------------------------------------
def _h_sm_sheet_bodies(a, refs):
    """One record per solid BODY: {name, sheet, box_mm}. Everything else in this domain
    assumes ONE body -- this is what lets the caller find out that it is not the case
    before believing a number."""
    C = _C()
    out = []
    for raw in _bodies():
        b = C.cast(raw, "IBody2")
        c = b.GetBodyBox()
        out.append({"name": b.Name, "sheet": bool(b.IsSheetMetal()),
                    "box_mm": [round((c[i + 3] - c[i]) * 1000.0, 4) for i in range(3)]})
    return out


_SM_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _sm_value(txt):
    """The cut list delivers everything as TEXT -- here it becomes a number when it is
    one. Only an integer or a pure decimal converts ("2 mm", "nan" and "inf" stay text)."""
    if not isinstance(txt, str):
        return txt
    t = txt.strip()
    return float(t) if _SM_NUMBER.match(t) else txt


def _h_sm_cut_lists(a, refs):
    """The cut list of EACH body: [{folder, bodies, **properties}].

    `CustomPropertyManager.Get` hands back the FORMULA and `Get5` the resolved value
    EMPTY -- `Get2`, whose early-binding return is the tuple (formula, value), is the one
    that resolves. The folders show up twice in the sweep, so they are deduplicated."""
    C = _C()
    md = C.active("IModelDoc2")
    md.ForceRebuild3(False)
    seen, folders = set(), []
    for f, _ in _sm_feats(True):
        if f.GetTypeName2() == str(a.get("type", "CutListFolder")) and f.Name not in seen:
            seen.add(f.Name)
            folders.append(f)
    out = []
    for folder in folders:
        bf = C.cast(folder.GetSpecificFeature2(), "IBodyFolder")
        names = [C.cast(x, "IBody2").Name for x in (bf.GetBodies() or [])]
        cpm = folder.CustomPropertyManager
        props = {}
        for name in (cpm.GetNames() or []):
            r = cpm.Get2(name, "", "")
            props[name] = _sm_value(r[1] if isinstance(r, tuple) and len(r) > 1 else r)
        out.append({"folder": folder.Name, "bodies": names, **props})
    if a.get("single") and len(out) > 1:
        # Returning the 1st folder would report ONE body as if it were the whole part --
        # the kind of silent lie this domain exists in order not to commit.
        raise BundleError(str(a.get("multi_msg", "multi-body part ({folders})"))
                          .format(n=len(out), folders=[r["folder"] for r in out]))
    return out


def _h_sm_export_flat(a, refs):
    """Export the FLAT PATTERN (the DXF/DWG that goes to the cutting machine).

    It is checked BEFORE calling: `ExportFlatPatternView` on a part with no file
    sometimes returns False in silence and sometimes opens a modal "Save As" and stalls
    automation indefinitely. Refusing here costs nothing; the modal costs the session."""
    C = _C()
    md = C.active("IModelDoc2")
    if not md.GetPathName():
        raise BundleError(
            "sheet.export_flat requires the part SAVED to disk (ExportFlatPatternView "
            "fails silently, or stalls on a modal, on a part with no file). Call "
            "part.save with a path first.")
    path = str(a["path"])
    folder = os.path.dirname(os.path.abspath(path))
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    if not C.active("IPartDoc").ExportFlatPatternView(path, int(a.get("option", 0))):
        raise BundleError(f"ExportFlatPatternView failed: {path}")
    return path


_HELPERS = {
    "sheetformat_path": _h_sheetformat_path,
    "delete_other_sheets": _h_delete_other_sheets,
    "view_names": _h_view_names,
    "count_annotations": _h_count_annotations,
    "pick_edge": _h_pick_edge,
    "dedupe_dimensions": _h_dedupe_dimensions,
    "view_center": _h_view_center,
    "sheet_info": _h_sheet_info,
    "activate_view": _h_activate_view,
    "import_model_dims": _h_import_model_dims,
    "comp_faces": _h_comp_faces,
    "comp_box": _h_comp_box,
    "asm_find_face": _h_asm_find_face,
    "faces_perp": _h_faces_perp,
    "components": _h_components,
    "comp_info": _h_comp_info,
    "comp_by_name": _h_comp_by_name,
    "mates": _h_mates,
    "remove_component": _h_remove_component,
    "document": _h_document,
    "doc_title": _h_doc_title,
    "select_components": _h_select_components,
    "last_mate_name": _h_last_mate_name,
    "mate_errors": _h_mate_errors,
    "interferences": _h_interferences,
    "free_translations": _h_free_translations,
    "move_component": _h_move_component,
    "cylinder_axis_world": _h_cylinder_axis_world,
    "sweep_collision_angle": _h_sweep_collision_angle,
    "mirror_components": _h_mirror_components,
    "resolve_plane": _h_resolve_plane,
    "list_features": _h_list_features,
    "bodies": _h_bodies,
    "faces": _h_faces,
    "edges": _h_edges,
    "find_face": _h_find_face,
    "find_edge": _h_find_edge,
    "edges_info": _h_edges_info,
    "faces_info": _h_faces_info,
    "dimensions": _h_dimensions,
    "find_face_at": _h_find_face_at,
    "select": _h_select,
    "select_edges": _h_select_edges,
    "select_bodies": _h_select_bodies,
    "select_sketch_entities": _h_select_sketch_entities,
    "combine": _h_combine,
    "relation": _h_relation,
    "spline": _h_spline,
    "close_other_doc": _h_close_other_doc,
    "load_component": _h_load_component,
    "screenshot": _h_screenshot,
    "equations": _h_equations,
    # sheet metal: the tree, the geometric eyes, the definitions and the DXF
    "sm_names": _h_sm_names, "sm_feature": _h_sm_feature,
    "sm_state": _h_sm_state, "sm_compare": _h_sm_compare,
    "sm_base_face": _h_sm_base_face, "sm_free_edges": _h_sm_free_edges,
    "sm_sharp_bend_edges": _h_sm_sharp_bend_edges,
    "sm_face_point": _h_sm_face_point, "sm_open_corners": _h_sm_open_corners,
    "sm_bend_faces": _h_sm_bend_faces, "sm_edge_vertex": _h_sm_edge_vertex,
    "sm_sketch_lines": _h_sm_sketch_lines,
    "sm_flange_sketch": _h_sm_flange_sketch,
    "sm_miter_profile": _h_sm_miter_profile,
    "sm_select_bends": _h_sm_select_bends,
    "sm_select_segments": _h_sm_select_segments,
    "sm_try_closed_corner": _h_sm_try_closed_corner,
    "sm_modify": _h_sm_modify, "sm_allowance": _h_sm_allowance,
    "sm_restore_allowance": _h_sm_restore_allowance,
    "sm_bend_info": _h_sm_bend_info, "sm_sheet_bodies": _h_sm_sheet_bodies,
    "sm_cut_lists": _h_sm_cut_lists, "sm_export_flat": _h_sm_export_flat,
}


# ── guards and return value ───────────────────────────────────────────────────
def _check_guards(guards: list, op_id: str, value) -> None:
    for g in guards:
        if g.get("op") != op_id:
            continue
        kind = g.get("assert")
        bad = (
            (kind == "truthy" and not value)
            or (kind == "not_null" and value is None)
            or (kind == "nonempty" and not (value or ""))
            or (kind == "equals" and value != g.get("value"))
            or (kind == "in" and value not in (g.get("value") or []))
            # `contains` is `in` the other way round: the LIST is what the op read at run
            # time and the expected item is what the compiler knows (the gauge the caller
            # asked for against the gauges the attached table actually has).
            or (kind == "contains" and g.get("value") not in (value or []))
            # `near` is `equals` for a MEASUREMENT: several verbs of this family only
            # count as done when the geometry moved to the requested number, and an exact
            # comparison of floats would fail on the rounding of the round trip.
            or (kind == "near" and not (isinstance(value, (int, float))
                                        and abs(value - g.get("value", 0))
                                        <= g.get("tol", 1e-3)))
            or (kind == "gte" and not (isinstance(value, (int, float))
                                       and value >= g.get("value", 0)))
        )
        if bad:
            raise BundleError(g.get("msg") or f"guard '{kind}' failed on {op_id}")


def _resolve_returns(spec, refs: dict):
    if isinstance(spec, str):
        return _jsonify(_subst(spec, refs))
    if isinstance(spec, dict):
        if "$handle" in spec:
            value = refs.get(spec["$handle"])
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                return [_store_handle(v) if _is_com(v) else _jsonify(v) for v in value]
            return _store_handle(value) if _is_com(value) else _jsonify(value)
        if "$ref" in spec:
            value = refs.get(spec["$ref"])
            scale = spec.get("scale")
            if scale:
                if isinstance(value, (list, tuple)):
                    return [v * scale for v in value]
                if isinstance(value, (int, float)):
                    return value * scale
            return _jsonify(value)
        return {k: _resolve_returns(v, refs) for k, v in spec.items()}
    if isinstance(spec, list):
        return [_resolve_returns(v, refs) for v in spec]
    return spec


def _jsonify(value):
    """Serializable; a COM object becomes a handle (so the LLM can reference it later)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if _is_com(value):
        return _store_handle(value)
    return str(value)


# ── write sandbox ─────────────────────────────────────────────────────────────
_WRITE_METHODS = {"SaveAs", "SaveAs3", "SaveBMP"}


def _check_paths(bundle: dict, cfg: dict, overwrite: bool) -> None:
    allowed = [Path(p).resolve() for p in (cfg.get("allowed_dirs") or [])]
    targets = []
    for op in bundle.get("ops", []):
        if op.get("call") in _WRITE_METHODS:
            args = op.get("args") or []
            if args and isinstance(args[0], str):
                targets.append((op["call"], Path(args[0])))
        elif op.get("helper") in ("screenshot", "sm_export_flat"):
            targets.append((op["helper"],
                            Path((op.get("args") or {}).get("path", ""))))

    for method, target in targets:
        if not str(target):
            continue
        if allowed:
            resolved = target.resolve()
            if not any(str(resolved).lower().startswith(str(a).lower()) for a in allowed):
                raise BundleError(
                    f"write outside the authorized folders: {target}\n"
                    f"authorized: {[str(a) for a in allowed]}")
        if method in ("SaveAs", "SaveAs3") and target.exists() and not overwrite:
            raise BundleError(
                f"the file ALREADY EXISTS: {target}\n"
                "Destructive operation: repeat the call with overwrite=True to confirm "
                "the overwrite.")


# ── execution ─────────────────────────────────────────────────────────────────
def execute(bundle: dict, cfg: dict, *, overwrite: bool = False) -> dict:
    """Validate (allowlist, sandbox) and run the bundle. Returns the resolved `returns`."""
    allowlist.check_bundle(bundle)          # local policy comes BEFORE everything
    _check_paths(bundle, cfg, overwrite)
    return _on_com_thread(_run, bundle)


def _run(bundle: dict) -> dict:
    C = _C()
    refs: dict = {}
    guards = bundle.get("guards", [])
    executed = 0

    for op in bundle.get("ops", []):
        oid = op["id"]
        args = None            # what the error translator needs to see (see `_translate_error`)
        try:
            if "helper" in op:
                args = _subst(op.get("args") or {}, refs)
                value = _HELPERS[op["helper"]](args, refs)
            elif "call" in op:
                target = _resolve_target(op["target"], refs)
                args = [_subst(a, refs) for a in (op.get("args") or [])]
                value = getattr(target, op["call"])(*args)
            elif "index" in op:
                # unpacks a return tuple, or ONE key of a helper's record; a DATA op,
                # it does not touch COM. The key form exists because the sheet metal
                # eyes answer with several things at once (the edges AND the fixed face
                # of the same corner) and the bundle has to address one of them.
                base = _subst(op["index"], refs)
                i = op.get("i", 0)
                if isinstance(base, dict):
                    value = base.get(i)
                elif isinstance(base, (list, tuple)) and isinstance(i, int):
                    # a NEGATIVE index addresses from the end ("the last sketch"), which
                    # is what almost every sheet metal verb needs
                    value = base[i] if -len(base) <= i < len(base) else None
                else:
                    value = None
            elif "format" in op:
                # a DATA op: builds a string from pieces resolved at run time
                value = str(op["format"]).format(*[_subst(a, refs) for a in (op.get("args") or [])])
            elif "set" in op:
                target = _resolve_target(op["target"], refs)
                setattr(target, op["set"], _subst(op.get("value"), refs))
                value = True
            else:
                target = _resolve_target(op["target"], refs)
                value = getattr(target, op["get"])
        except pythoncom.com_error as exc:
            op_name = (op.get("call") or op.get("get") or op.get("set")
                       or op.get("helper"))
            msg = _com_msg(exc)
            if "disconnected from its clients" in msg:
                # The handle exists in the table, but the COM object it points at DIED.
                # With `sw_close_all` clearing the table this path became RARE --
                # it is left for whoever closes the document from outside (the SolidWorks
                # UI, another COM caller). The wording is the same as for a handle that is
                # no longer in the table: for the model, the fact is the same.
                raise BundleError(
                    f"op {oid} ({op_name}): {_DEAD_HANDLE}") from None
            if "Member not found" in msg:
                # The object EXISTS, but has no such member: almost always a handle of the
                # wrong TYPE (an edge where a body is expected). `_body_of` already catches
                # the `body=` case, which was the common one; this is the net for the rest,
                # at the single point every op passes through. Without it the model got a
                # bare "Member not found." -- 74 occurrences in one measured round.
                raise BundleError(
                    f"op {oid} ({op_name}): a handle came in with the wrong TYPE -- the "
                    f"CAD object has no such member (e.g. an edge where a body is "
                    f"expected). What to do: OMIT the object argument, or take the handle "
                    f"from the right inspection (`inspect_bodies` for a body, "
                    f"`inspect_faces_info` for a face, `inspect_edges_info` for an "
                    f"edge).") from None
            raise BundleError(
                f"op {oid} ({op_name}) failed in COM: {msg}") from None
        except BundleError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BundleError(_translate_error(exc, op, args)) from None

        if op.get("cast") and value is not None:
            # returned objects come back DYNAMIC -> re-cast (the early binding gotcha)
            if isinstance(value, (list, tuple)):
                value = [C.cast(v, op["cast"]) for v in value if v is not None]
            else:
                value = C.cast(value, op["cast"])

        refs[oid] = value
        executed += 1
        _check_guards(guards, oid, value)

    return {
        "verb": bundle.get("verb"),
        "result": _resolve_returns(bundle.get("returns"), refs),
        "ops_executed": executed,
    }


def _translate_error(exc: Exception, op: dict, args) -> str:
    """A raw Python error -> a sentence that SAYS WHAT TO DO.

    It applies the project's rule ("every guard says what to do") at the last place where
    it did not. Measured 2026-08-17 with a weak model: passing
    `body: "Part431"` -- the document's NAME instead of a handle -- produced
    `AttributeError: 'str' object has no attribute '_oleobj_'`, and on a document with no
    body it produced `IndexError: list index out of range`. Neither teaches anything, and
    the model spent 27 to 35 calls per step running into them.

    The translation lives HERE, at the single point every op goes through, rather than
    scattered across dozens of helpers: a list of parameter names would be guesswork (the
    `face` of `fillet_on` is TEXT, another verb's is a handle), and a fix applied to some
    and forgotten in the others is this project's most expensive defect.
    """
    name = op.get("helper") or op.get("call") or op.get("get") or op.get("set") or "?"
    oid = op.get("id")
    text = str(exc)

    if isinstance(exc, AttributeError) and "_oleobj_" in text:
        # which arguments are plain TEXT -- one of them is the one that should be a handle
        # `args` is a dict in the helpers and a LIST in direct COM calls -- both forms pass
        # through here, so the reading has to cope with both.
        items = (args.items() if isinstance(args, dict)
                 else enumerate(args) if isinstance(args, (list, tuple)) else [])
        suspects = [f"{k}={v!r}" for k, v in items
                    if isinstance(v, str) and not v.startswith("@h")]
        return (
            f"op {oid} ({name}): an argument that has to be a CAD OBJECT came in as text"
            + (f" -- probably {', '.join(suspects[:3])}. " if suspects else ". ")
            + "A handle is always '@h' followed by a number (`@h7`), and it only exists if "
              "it APPEARED in the result of one of your calls in this same step; the "
              "document's name ('Part431') or a feature's name will not do. The right move "
              "is almost always to OMIT that argument: the verb falls back to the "
              "document's single body on its own. If you really need an object, call "
              "inspect_bodies / inspect_faces_info first and use the handle that comes "
              "back from there.")

    if isinstance(exc, IndexError):
        return (
            f"op {oid} ({name}): the read found no geometry -- the document is probably "
            f"EMPTY (no body). Create the solid first (part_block, part_cylinder, or "
            f"sketch + extrude); part_new_part alone only opens the blank sheet.")

    return f"op {oid} ({name}) failed: {type(exc).__name__}: {exc}"


def _com_msg(exc: pythoncom.com_error) -> str:
    try:
        info = exc.args[2] if len(exc.args) > 2 else None
        detail = info[2] if info and len(info) > 2 and info[2] else ""
        return f"{exc.args[1]} {detail}".strip()
    except Exception:  # noqa: BLE001
        return str(exc)


def connect(launch: bool = True) -> str:
    """Connect to or open SOLIDWORKS on the execution thread."""
    def _do():
        C = _C()
        C.connect(launch=launch)
        return C.app().RevisionNumber()
    return _on_com_thread(_do)
