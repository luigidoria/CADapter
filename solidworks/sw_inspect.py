# -*- coding: utf-8 -*-
"""
sw_inspect -- read-only inspection (the LLM's "eyes") + selection by geometry.

A large part of the SolidWorks API (~34%) is getters; here that collapses into a few
verbs: list features, enumerate/find faces and edges by geometry (for mates/fillets),
mass, screenshot (a viewport image for the judge to look at), and the two that close
the loop after building: `measure` (compare the geometry with the intent) and
`validate` (did the document rebuild clean).
"""
import os

from . import sw_assembly as A
from . import sw_com as C

# swBodyType_e
SOLID, SHEET, ALL_BODIES = 0, 1, -1


def _part():
    return C.active("IPartDoc")


def bodies():
    """List of solid IBody2 of the active doc."""
    bs = _part().GetBodies2(SOLID, False)
    return [C.cast(b, "IBody2") for b in (bs or [])]


def list_features():
    """Feature tree of the active doc: [{name, type}]."""
    md = C.active("IModelDoc2")
    out, raw = [], md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        out.append({"name": f.Name, "type": f.GetTypeName2()})
        raw = f.GetNextFeature()
    return out


def _not_a_body(raw) -> str:
    """Say WHAT came in as `body`, not just that it is wrong -- the sentence has to be
    concrete for a caller that passed a face or an edge by mistake."""
    what = "is not a body"
    for iface, member, label in (("IFace2", "GetArea", "is a FACE"),
                                 ("IEdge", "GetCurve", "is an EDGE")):
        try:
            getattr(C.cast(raw, iface), member)()
            what = label
            break
        except C.pythoncom.com_error:
            continue
    return (f"`body=` was given an object that {what}. OMIT `body` -- the verb falls "
            f"back to the document's single body on its own, which is what you want "
            f"almost always. If the part has more than one body, take the body from "
            f"inspect.bodies (a face or edge from inspect.faces/inspect.edges will not do).")


def _body(body=None):
    """The body from `body=`, VALIDATED -- or the sentence that says what to do.

    `C.cast` wraps WITHOUT validating: casting an EDGE to IBody2 passes silently and only
    the following `GetFaces()` blows up with 'Member not found'. Measured 2026-08-18 with
    a local model: 74 such errors in one round, the model passing an edge from the very
    inspection it had just called. The probe is `GetFaceCount`, which exists on IBody2
    and not on IFace2/IEdge. With no body at all (an empty part) the error says to create
    the solid first instead of an IndexError.
    """
    if body is None:
        bs = bodies()
        if not bs:
            raise RuntimeError(
                "the document has no solid body yet -- create the solid first "
                "(part.block, part.cylinder, or part.sketch + part.extrude); "
                "part.new_part alone only opens the blank sheet.")
        return bs[0]
    b = C.cast(body, "IBody2")
    try:
        b.GetFaceCount()
    except C.pythoncom.com_error:
        raise ValueError(_not_a_body(body)) from None
    return b


def faces(body=None, info: bool = False):
    """The faces of a body (default: the part's first body).

    info=False -> the IFace2 objects. info=True -> one record per face WITH its geometry:
    {face, kind (planar|cylindrical|other), normal, outward_normal, radius_mm, cyl_axis,
    convex, box_mm, point_mm, area_mm2}. The bare handles are indistinguishable from each
    other -- measured 2026-08-15, a local model asked for them ELEVEN times in a row
    trying to work out where a hole would fit. With the outward normal, the box (mm,
    global) and a point guaranteed ON the face, a caller can say WHICH face each one is.
    `outward_normal` points out of the material; `convex` is False for a hole (material
    outside the cylinder) and True for a boss or a corner fillet."""
    fs = [C.cast(f, "IFace2") for f in _body(body).GetFaces()]
    return [_face_info(f) for f in fs] if info else fs


def edges(body=None, info: bool = False):
    """The edges of a body (default: the part's first body).

    info=False -> the IEdge objects. info=True -> one record per edge WITH its geometry:
    {edge, straight, point_mm, direction, start_mm, end_mm, length_mm, adj_faces}.
    `adj_faces` describes the TWO faces the edge separates (kind, axis, pos_mm,
    outward_normal, box_mm) -- what lets an edge be named "top|front" instead of by a
    handle -- and `length_mm` is what limits a fillet's radius (None on a curve, where the
    chord would mislead)."""
    es = [C.cast(e, "IEdge") for e in _body(body).GetEdges()]
    return [_edge_info(e) for e in es] if info else es


def _surf(f2):
    return C.cast(f2.GetSurface(), "ISurface")


def _outward(f2, s=None):
    """The OUTWARD normal of a planar face. `PlaneParams` points INTO the solid (measured:
    the face at x=0 of a block comes back with [1,0,0]); `FaceInSurfaceSense` says whether
    the face uses the surface forward, and the sign is its INVERSE -- measured face by
    face: with `True -> +1` the top of a boss came out 'bottom' in every case."""
    s = s or _surf(f2)
    n = s.PlaneParams
    sense = -1.0 if f2.FaceInSurfaceSense() else 1.0
    return [n[0] * sense, n[1] * sense, n[2] * sense]


def _face_info(f2) -> dict:
    """One face measured: see `faces(info=True)`."""
    s = _surf(f2)
    box = [v * 1000.0 for v in (f2.GetBox() or [])]
    center = ([(box[i] + box[i + 3]) / 2000.0 for i in range(3)]
              if len(box) == 6 else [0.0, 0.0, 0.0])
    # a point GUARANTEED on the face (the box centre can fall outside an L-shaped face or
    # one with a hole) -- it identifies WHICH face when two are coplanar and separate
    p = f2.GetClosestPointOn(*center)
    info = {"face": f2, "kind": "other", "normal": None, "outward_normal": None,
            "radius_mm": None, "box_mm": box or None,
            "point_mm": [p[0] * 1000.0, p[1] * 1000.0, p[2] * 1000.0] if p else None,
            "area_mm2": f2.GetArea() * 1e6}
    if s.IsPlane():
        n = s.PlaneParams
        info["kind"] = "planar"
        info["normal"] = [n[0], n[1], n[2]]
        info["outward_normal"] = _outward(f2, s)
    elif s.IsCylinder():
        cp = s.CylinderParams              # (px,py,pz, ax,ay,az, radius)
        info["kind"] = "cylindrical"
        info["radius_mm"] = cp[6] * 1000.0
        # the AXIS comes from the surface, not from the box: a D20 hole in a 10 mm plate
        # has a box of [20,20,10] and the "longest extent" would say X
        info["cyl_axis"] = [cp[3], cp[4], cp[5]]
        info["convex"] = not bool(f2.FaceInSurfaceSense())
    return info


def _describe_face(f2) -> dict:
    """An adjacent face as NUMBERS, without the COM object (one handle per edge per face
    would be hundreds on a real part just to say "this edge separates top from front")."""
    s = _surf(f2)
    box = [v * 1000.0 for v in (f2.GetBox() or [])]
    if not s.IsPlane() or len(box) != 6:
        return {"kind": "cylindrical" if s.IsCylinder() else "other", "box_mm": box}
    # a SLANTED planar face has no degenerate axis; the normal is what names it
    axis = next((k for k in range(3) if abs(box[k + 3] - box[k]) < 1e-4), None)
    return {"kind": "planar", "axis": axis,
            "pos_mm": box[axis] if axis is not None else None,
            "outward_normal": [round(v, 4) for v in _outward(f2, s)],
            "box_mm": box}


def _edge_info(e2) -> dict:
    """One edge measured: see `edges(info=True)`."""
    curve = C.cast(e2.GetCurve(), "ICurve")
    straight = bool(curve.IsLine())
    lp = curve.LineParams if straight else None
    cp = e2.GetCurveParams2()          # 11 doubles; [0:3] start, [3:6] end, in METERS
    p0 = [v * 1000.0 for v in cp[0:3]]
    p1 = [v * 1000.0 for v in cp[3:6]]
    length = sum((x - y) ** 2 for x, y in zip(p0, p1)) ** 0.5
    adj = e2.GetTwoAdjacentFaces() or ()
    return {"edge": e2, "straight": straight,
            "point_mm": [lp[0] * 1000.0, lp[1] * 1000.0, lp[2] * 1000.0] if straight else None,
            "direction": [lp[3], lp[4], lp[5]] if straight else None,
            "start_mm": p0, "end_mm": p1,
            "length_mm": length if straight else None,
            "adj_faces": [_describe_face(C.cast(f, "IFace2")) for f in adj]}


def _face_at(axis: int, want_max: bool = True, point_mm=None, body=None):
    """The planar face whose OUTWARD normal points along +axis (want_max) or -axis, with
    `point_mm` [x,y,z] breaking the tie between faces of that orientation (the nearest to
    the point wins); without it, the outermost one along the axis. None if there is none.
    Helper for the verbs that address a face by its NAME (`part.hole(face='top')`)."""
    sign = 1.0 if want_max else -1.0
    cands = []
    for f2 in faces(body):
        s = _surf(f2)
        if not s.IsPlane():
            continue
        out = _outward(f2, s)
        if out[axis] * sign < 0.9 or any(abs(out[j]) > 0.1 for j in range(3) if j != axis):
            continue
        box = [v * 1000.0 for v in (f2.GetBox() or [0] * 6)]
        p = f2.GetClosestPointOn(*[(box[i] + box[i + 3]) / 2000.0 for i in range(3)])
        cands.append(([p[0] * 1000.0, p[1] * 1000.0, p[2] * 1000.0], f2))
    if not cands:
        return None
    if point_mm:
        return min(cands, key=lambda t: sum((x - float(y)) ** 2
                                            for x, y in zip(t[0], point_mm)))[1]
    cands.sort(key=lambda t: t[0][axis])
    return cands[-1][1] if sign > 0 else cands[0][1]


def _edges_where(only: str = "all", axis=None, want_max: bool = True, body=None) -> list:
    """Edges picked by DESCRIPTION: `only` 'curved' (hole and radius rims) | 'straight'
    (sharp corners) | 'all', optionally only those touching the planar face that points
    along +axis (want_max) or -axis.

    It exists because a hole's rim was unreachable by geometry search: `find_edge(axis)`
    returns only STRAIGHT edges. Measured 2026-08-16: asked to round the circle's edges,
    a model got a vertical corner from `find_edge` and rounded THAT. An edge "belongs" to
    a face when one of its two neighbours is planar and points that way -- the rim of a
    through hole in Y has the cylinder on one side and that plane on the other."""
    chosen = []
    for e in edges(body, info=True):
        if (only == "curved" and e["straight"]) or (only == "straight" and not e["straight"]):
            continue
        if axis is not None:
            sign = 1.0 if want_max else -1.0
            if not any(d.get("kind") == "planar" and d.get("outward_normal")
                       and d["outward_normal"][int(axis)] * sign > 0.9
                       and all(abs(d["outward_normal"][j]) < 0.1
                               for j in range(3) if j != int(axis))
                       for d in e.get("adj_faces") or []):
                continue
        chosen.append(e["edge"])
    return chosen


def find_face(kind="plane", axis=1, want_max=True, body=None, radius_mm=None,
              point_mm=None):
    """Find a face by GEOMETRY (no fragile coordinate):
      kind='plane': planar face perpendicular to `axis` (0=X,1=Y,2=Z), picked by
        POSITION (want_max -> largest coordinate on that axis). PlaneParams gives the
        PLANE's normal, not the outward one -> tell them apart by position, not sign.
        With `point_mm` [x,y,z]: the face whose OUTWARD normal points to +axis
        (want_max) or -axis and that is NEAREST to that point -- it breaks the tie
        between coplanar, separate faces (two bosses at the same height).
      kind='cylinder': cylindrical face; with `radius_mm`, only matches one of roughly
        equal radius (tol 0.2mm) -- so the hole is not confused with a fillet/head/
        counterbore.
    Returns IFace2 or None."""
    if kind != "cylinder" and point_mm:
        return _face_at(int(axis), bool(want_max), point_mm, body)
    cand = []
    for f2 in faces(body):
        s = _surf(f2)
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


def select(entities, clear=True):
    """Select a list of entities (IFace2/IEdge/...) via Select4 (early binding ->
    Data=None). `clear` clears the selection first. Returns how many were selected."""
    md = C.active("IModelDoc2")
    if clear:
        md.ClearSelection2(True)
    n = 0
    for i, e in enumerate(entities):
        if C.cast(e, "IEntity").Select4(i > 0 or not clear, None):
            n += 1
    return n


def edge_dir(e2):
    """Direction (dx,dy,dz) of a straight edge, or None if it is not straight."""
    curve = C.cast(e2.GetCurve(), "ICurve")
    if curve.IsLine():
        lp = curve.LineParams  # (rootx,rooty,rootz, dirx,diry,dirz)
        return lp[3:6]
    return None


def find_edge(axis: int, body=None):
    """First STRAIGHT edge aligned with `axis` (0=X,1=Y,2=Z). Useful as the direction
    of a linear pattern."""
    for e2 in edges(body):
        d = edge_dir(e2)
        if d and abs(d[axis]) > 0.9 and all(abs(d[j]) < 0.1 for j in range(3) if j != axis):
            return e2
    return None


def select_marked(entity, mark, append=False):
    """Select an entity (face/edge/...) with a MARK (patterns use the mark to tell
    direction=1 / axis=1 / seed-feature=4 apart). Early binding: Data=SelectData."""
    md = C.active("IModelDoc2")
    selmgr = C.cast(md.SelectionManager, "ISelectionMgr")
    sd = selmgr.CreateSelectData()
    sd.Mark = mark
    return C.cast(entity, "IEntity").Select4(append, sd)


def select_feature(name, mark, append=True):
    """Select a FEATURE by name (BODYFEATURE) with a mark. Pattern seed=4."""
    ext = C.cast(C.active("IModelDoc2").Extension, "IModelDocExtension")
    return ext.SelectByID2(name, "BODYFEATURE", 0, 0, 0, append, mark, None, 0)


def mass(body=None):
    """Mass properties of the active doc: {mass_kg, volume_m3, area_m2, com_mm}."""
    ext = C.cast(C.active("IModelDoc2").Extension, "IModelDocExtension")
    mp = ext.CreateMassProperty()
    com = mp.CenterOfMass
    return {
        "mass_kg": mp.Mass,
        "volume_m3": mp.Volume,
        "area_m2": mp.SurfaceArea,
        "com_mm": [c * 1000 for c in com],
    }


def screenshot(path: str, width: int = 1200, height: int = 900) -> str:
    """Save a BMP of the current viewport (the judge's 'eyes'), framed first: trimetric,
    shaded, zoom to fit. Returns the path.
    TRIMETRIC and not isometric: in isometric X and Z project symmetrically, so two parts
    offset by (+a,0,+a) and (-a,0,-a) land exactly one behind the other (measured
    2026-08-20: two correctly placed pins showed up as ONE). The framing is best-effort --
    if SolidWorks refuses a view command, the photo still comes out."""
    md = C.active("IModelDoc2")
    for attempt in (lambda: md.ShowNamedView2("*Trimetric", 8),
                    lambda: md.ViewDisplayShaded(),
                    lambda: md.ViewZoomtofit2()):
        try:
            attempt()
        except Exception:  # noqa: BLE001 -- angle and finish, never the photo itself
            pass
    md.SaveBMP(path, width, height)
    return path


# ── sketch inspection (read an EXISTING sketch) ───────────────────────────────
def _label(enum: str, value, prefix: str) -> str:
    """Short lowercase name of an enum value: ('swConstraintType_e', 4,
    'swConstraintType_') -> 'horizontal'. Falls back to the raw number."""
    name = C.enum_names(enum).get(value)
    if not name:
        return str(value)
    return (name[len(prefix):] if name.startswith(prefix) else name).lower()


def _feature(name: str):
    """IFeature by name in the active part or assembly (sub-features included)."""
    md = C.active("IModelDoc2")
    doc = C.active("IPartDoc") if md.GetType() == C.DOC_PART else C.active("IAssemblyDoc")
    raw = doc.FeatureByName(name)
    if raw is None:
        raise ValueError(f"feature '{name}' not found. Use inspect.list_features to see "
                         "the tree")
    return C.cast(raw, "IFeature")


def _last_sketch_feature():
    """The last sketch in the tree (the sketch being edited wins, if any)."""
    md = C.active("IModelDoc2")
    active = C.cast(md.SketchManager, "ISketchManager").ActiveSketch
    if active is not None:
        return C.cast(C.cast(active, "ISketch"), "IFeature")
    last, raw = None, md.FirstFeature()
    while raw is not None:
        f = C.cast(raw, "IFeature")
        if f.GetTypeName2() in _SKETCH_TYPES:
            last = f
        raw = f.GetNextFeature()
    if last is None:
        raise ValueError("the active document has no sketch")
    return last


_SKETCH_TYPES = ("ProfileFeature", "3DProfileFeature")
# relations that are really DIMENSIONS (listed under 'dimensions', not repeated here)
_DIM_RELATIONS = {1, 2, 3, 15, 41, 44}   # distance angle radius diameter doubledistance arclength
_MM = lambda v: round(v * 1000.0, 4)     # noqa: E731
# swConstrainedStatus_e, in the words the FeatureManager uses
_SKETCH_STATUS = {1: "unknown", 2: "under_defined", 3: "fully_defined", 4: "over_defined",
                  5: "no_solution", 6: "invalid_solution", 7: "autosolve_off"}


def _pt_mm(p):
    p = C.cast(p, "ISketchPoint")
    return [_MM(p.X), _MM(p.Y)] + ([_MM(p.Z)] if abs(p.Z) > 1e-9 else [])


def sketch(name: str = "", include=("entities", "dimensions", "relations")) -> dict:
    """READ an existing sketch (also one made outside this MCP): its entities, dimensions
    and relations, and whether it is fully/under/over defined. `name` = the sketch
    feature name ('Sketch1'; default: the sketch being edited, else the LAST one).
    `include` picks the sections: entities | dimensions | relations | points.
    Coordinates are in mm in the SKETCH's own 2D space (not the model's). Entities are
    named like the FeatureManager does (Line1, Arc1...); points are labelled by the
    segment end they belong to ('Line1.start', 'Arc1.center') or by their coordinates.
    Relations that are dimensions (distance, radius...) are listed under dimensions.
    Returns {name, status, fully_defined, entities, dimensions, relations}."""
    if isinstance(include, str):
        include = [include]
    feat = _feature(name) if name else _last_sketch_feature()
    if feat.GetTypeName2() not in _SKETCH_TYPES:
        raise ValueError(f"'{feat.Name}' is a {feat.GetTypeName2()}, not a sketch")
    sk = C.cast(feat.GetSpecificFeature2(), "ISketch")
    status = sk.GetConstrainedStatus()
    out = {"name": feat.Name, "status": _SKETCH_STATUS.get(status, f"status {status}"),
           "fully_defined": status == 3}

    # every point that is a segment end gets a readable label. Walking the segments is
    # the slow part on a big sketch (measured: 34 s for one hole-grid sketch of a
    # SOLIDWORKS sample part), so it is skipped when no section needs it
    labels, entities = {}, []
    walk = any(k in include for k in ("entities", "points", "relations"))
    for raw in (sk.GetSketchSegments() or []) if walk else []:
        sg = C.cast(raw, "ISketchSegment")
        kind = _label("swSketchSegments_e", sg.GetType(), "swSketch")
        ent = {"name": sg.GetName(), "type": kind,
               "construction": bool(sg.ConstructionGeometry)}
        if kind == "line":
            ln = C.cast(sg, "ISketchLine")
            ends = (("start", ln.GetStartPoint2()), ("end", ln.GetEndPoint2()))
            ent["length_mm"] = _MM(sg.GetLength())
        elif kind == "arc":
            arc = C.cast(sg, "ISketchArc")
            circle = bool(arc.IsCircle())
            ent["type"] = "circle" if circle else "arc"
            ent["radius_mm"] = _MM(arc.GetRadius())
            ends = [("center", arc.GetCenterPoint2())]
            if not circle:
                ends += [("start", arc.GetStartPoint2()), ("end", arc.GetEndPoint2())]
                ent["length_mm"] = _MM(sg.GetLength())
        else:
            ends = ()
            ent["length_mm"] = _MM(sg.GetLength())
        for role, p in ends:
            if p is None:
                continue
            key = tuple(C.cast(p, "ISketchPoint").GetID())
            labels.setdefault(key, f"{ent['name']}.{role}")
            ent[role + "_mm"] = _pt_mm(p)
        entities.append(ent)
    if "entities" in include:
        out["entities"] = entities
    if "points" in include:
        out["points"] = [{"id": list(C.cast(p, "ISketchPoint").GetID()),
                          "label": labels.get(tuple(C.cast(p, "ISketchPoint").GetID())),
                          "at_mm": _pt_mm(p)} for p in (sk.GetSketchPoints2() or [])]

    if "dimensions" in include:
        dims, dd = [], feat.GetFirstDisplayDimension()
        while dd is not None:
            dd = C.cast(dd, "IDisplayDimension")
            dim = C.cast(dd.GetDimension2(0), "IDimension")
            angular = dim.GetType() == 1           # swDimensionParamTypeDoubleAngular
            value = dim.GetSystemValue3(1, None)[0]  # swThisConfiguration
            dims.append({"name": dim.FullName.rsplit("@", 1)[0] if dim.FullName.count("@") > 1
                         else dim.FullName,
                         "value": round(value * (57.29577951308232 if angular else 1000.0), 6),
                         "unit": "deg" if angular else "mm",
                         "kind": _label("swDimensionType_e", dd.GetType(), "sw")
                         .replace("dimension", ""),
                         "driving": dim.DrivenState == 2})
            dd = feat.GetNextDisplayDimension(dd)
        out["dimensions"] = dims

    if "relations" in include:
        rels = []
        rm = C.cast(sk.RelationManager, "ISketchRelationManager")
        for raw in rm.GetRelations(0) or []:          # swAll
            r = C.cast(raw, "ISketchRelation")
            rtype = r.GetRelationType()
            if rtype in _DIM_RELATIONS:
                continue
            names = []
            types = list(r.GetEntitiesType() or [])
            for i, e in enumerate(r.GetEntities() or []):
                etype = types[i] if i < len(types) else 0
                try:
                    if etype == 2:                    # a point
                        p = C.cast(e, "ISketchPoint")
                        names.append(labels.get(tuple(p.GetID()),
                                                "point(" + ", ".join(map(str, _pt_mm(p))) + ")"))
                    elif etype in (3, 4, 5, 6, 7):   # line, arc, ellipse, parabola, spline
                        names.append(C.cast(e, "ISketchSegment").GetName())
                    else:                             # plane / face / edge outside the sketch
                        names.append(_label("swSketchRelationEntityTypes_e", etype,
                                            "swSketchRelationEntityType_") + " (external)")
                except Exception:  # noqa: BLE001 -- an external entity: name its kind
                    names.append(_label("swSketchRelationEntityTypes_e", etype,
                                        "swSketchRelationEntityType_") + " (external)")
            rels.append({"type": _label("swConstraintType_e", rtype, "swConstraintType_"),
                         "entities": names})
        out["relations"] = rels
    return out


# ── model summary (one call to "understand this CAD document") ───────────────
_ANN_KEYS = {2: "datums", 4: "dimensions", 5: "gtols", 6: "notes", 7: "surface_finishes",
             8: "weld_symbols", 13: "center_marks", 14: "tables", 15: "centerlines"}


def _dims_of(feat, limit, out):
    dd = feat.GetFirstDisplayDimension()
    while dd is not None and len(out) < limit:
        dd = C.cast(dd, "IDisplayDimension")
        dim = C.cast(dd.GetDimension2(0), "IDimension")
        angular = dim.GetType() == 1
        full = dim.FullName
        # `name` is the SHORT form part.set_dimension takes ('D1@Boss-Extrude1'), and the
        # owning feature says what the dimension is -- on an extrusion D1 is its depth
        out.append({"name": full.rsplit("@", 1)[0] if full.count("@") > 1 else full,
                    "value": round(dim.SystemValue * (57.29577951308232 if angular else 1000.0), 4),
                    "unit": "deg" if angular else "mm",
                    "feature": feat.Name, "kind": feat.GetTypeName2()})
        dd = feat.GetNextDisplayDimension(dd)


def _box_mm(box):
    lo, hi = [v * 1000.0 for v in box[:3]], [v * 1000.0 for v in box[3:6]]
    return {"min_mm": [round(v, 3) for v in lo], "max_mm": [round(v, 3) for v in hi],
            "size_mm": [round(h - l, 3) for l, h in zip(lo, hi)]}


def _suppression(comp) -> int:
    """swComponentSuppressionState_e of a component. GetSuppression2 does not exist in the
    SW 2017 typelib (measured 2026-09-25: AttributeError on IComponent2, rev 25.3), so fall
    back to GetSuppression, which both versions have."""
    try:
        return comp.GetSuppression2()
    except AttributeError:
        return comp.GetSuppression()


def model_summary(max_items: int = 60, *, brief: bool = False) -> dict:
    """UNDERSTAND the active document in one call -- the first thing to run on a file
    you did not build. A composition of the other read-only verbs:
      part     -> configurations, material, bodies, features, dimensions (name = what
                  part.set_dimension takes, value, unit, owning feature and its type),
                  sketches' states, mass, bounding box;
      assembly -> components (total / top level / unique files / subassemblies / fixed),
                  mates by type and their errors, bounding box;
      drawing  -> sheets, views (dwg.list_views), and annotation counts per kind
                  (dimensions, gtols, datums, notes, tables=BOM, center marks...).
    Every type also gets `health` (inspect.validate without the interference scan).
    Lists are capped at `max_items` (the count is always complete).
    brief=True is the CHEAP reading, with no rebuild: {doc_type, title, path} and, on a
    part, the named `dimensions`. With nothing open, doc_type is 'none'."""
    if C.app().ActiveDoc is None:
        return {"title": "", "path": "", "doc_type": "none"}
    md = C.active("IModelDoc2")
    doc_type = _DOC_KIND.get(md.GetType(), "unknown")
    out = {"title": md.GetTitle(), "path": md.GetPathName(), "doc_type": doc_type}
    if brief:
        # the question a caller asks before every step ("which document, which
        # dimensions") must not cost a rebuild -- `health` below forces one
        if doc_type == "part":
            dims, raw = [], md.FirstFeature()
            while raw is not None:
                f = C.cast(raw, "IFeature")
                _dims_of(f, max_items, dims)
                raw = f.GetNextFeature()
            out["dimensions"] = dims
        return out
    if doc_type in ("part", "assembly"):
        out["configuration"] = C.cast(md.GetActiveConfiguration(), "IConfiguration").Name
        out["configurations"] = list(md.GetConfigurationNames() or [])

    if doc_type == "part":
        pd = C.active("IPartDoc")
        mat = pd.GetMaterialPropertyName2("")
        out["material"] = (mat[0] if isinstance(mat, (tuple, list)) else mat) or None
        out["body_count"] = len(bodies())
        # everything up to the Origin is folders and the three default planes
        feats, dims, raw, past_origin = [], [], md.FirstFeature(), False
        while raw is not None:
            f = C.cast(raw, "IFeature")
            ftype = f.GetTypeName2()
            if past_origin and not ftype.endswith("Folder"):
                feats.append({"name": f.Name, "type": ftype})
                _dims_of(f, max_items, dims)
            past_origin = past_origin or ftype == "OriginProfileFeature"
            raw = f.GetNextFeature()
        out["feature_count"] = len(feats)
        out["features"] = feats[:max_items]
        out["dimensions"] = dims
        out["sketches"] = {
            x["name"]: _SKETCH_STATUS.get(st, f"status {st}")
            for x in feats if x["type"] in _SKETCH_TYPES
            for st in [C.cast(_feature(x["name"]).GetSpecificFeature2(),
                              "ISketch").GetConstrainedStatus()]}
        if out["body_count"]:
            m = mass()
            out["mass_kg"] = round(m["mass_kg"], 6)
            out["volume_mm3"] = round(m["volume_m3"] * 1e9, 3)
            out["bounding_box"] = _box_mm(pd.GetPartBox(True))

    elif doc_type == "assembly":
        comps = A.components()
        top = [c for c in comps if "/" not in c.Name2]
        files = {os.path.normcase(c.GetPathName()) for c in comps if c.GetPathName()}
        out["components"] = {
            "total": len(comps), "top_level": len(top), "unique_files": len(files),
            "subassemblies": sum(1 for c in comps
                                 if c.GetPathName().lower().endswith(".sldasm")),
            "fixed": [c.Name2 for c in top if c.IsFixed()][:max_items],
            "suppressed": sum(1 for c in comps if _suppression(c) == 0)}
        ms = A.mates()
        by_type = {}
        for m in ms:
            by_type[m["type"]] = by_type.get(m["type"], 0) + 1
        out["mates"] = {"total": len(ms), "by_type": by_type,
                        "errors": [{"name": m["name"], "status": m["status"]}
                                   for m in ms if m["status"] != "ok"][:max_items]}
        try:
            out["bounding_box"] = _box_mm(C.active("IAssemblyDoc").GetBox(0))
        except Exception:  # noqa: BLE001 -- an empty assembly has no box
            pass

    elif doc_type == "drawing":
        from . import sw_drawing as D   # lazy: sw_drawing is not needed for parts
        views = D.list_views()
        counts = {}
        for sheet in C.active("IDrawingDoc").GetViews() or []:
            for raw in sheet or []:
                a = C.cast(raw, "IView").GetFirstAnnotation2()
                while a is not None:
                    an = C.cast(a, "IAnnotation")
                    key = _ANN_KEYS.get(an.GetType())
                    if key:
                        counts[key] = counts.get(key, 0) + 1
                    a = an.GetNext3()
        out["sheets"] = list(C.active("IDrawingDoc").GetSheetNames() or [])
        out["view_count"] = len(views)
        out["views"] = [{k: v[k] for k in ("name", "sheet", "type", "orientation", "scale",
                                            "model")} for v in views][:max_items]
        out["models"] = sorted({v["model"] for v in views if v["model"]})
        out["annotations"] = counts

    v = validate(interferences=False)
    out["health"] = {"valid": v["valid"], "rebuild_ok": v["rebuild_ok"],
                     "errors": v["errors"][:max_items], "warnings": v["warnings"][:max_items]}
    return out


# ── measure (IMeasure) ────────────────────────────────────────────────────────
# IMeasure answers -1 for every quantity that does not apply to the selection, so the
# reading below keeps only what was actually measured. Units: m -> mm, rad -> deg.
_MEASURED = (("Distance", "distance_mm", 1000.0), ("NormalDistance", "normal_distance_mm", 1000.0),
             ("CenterDistance", "center_distance_mm", 1000.0),
             ("Angle", "angle_deg", 57.29577951308232), ("Radius", "radius_mm", 1000.0),
             ("Diameter", "diameter_mm", 1000.0), ("Length", "length_mm", 1000.0),
             ("Area", "area_mm2", 1e6), ("Perimeter", "perimeter_mm", 1000.0))
# ArcOption: what a distance to a circular entity means (0 centre, 1 minimum, 2 maximum)
_KINDS = {"all": 0, "distance": 0, "minimum_distance": 1, "maximum_distance": 2,
          "angle": 0, "radius": 0, "diameter": 0, "length": 0, "area": 0}
_KIND_KEY = {"distance": "distance_mm", "minimum_distance": "distance_mm",
             "maximum_distance": "distance_mm", "angle": "angle_deg", "radius": "radius_mm",
             "diameter": "diameter_mm", "length": "length_mm", "area": "area_mm2"}


def _select_any(ent, append):
    """Select a face/edge/vertex/sketch segment (IEntity) or a component (IComponent2)."""
    try:
        return C.cast(ent, "IEntity").Select4(append, None)
    except Exception:  # noqa: BLE001 -- not an IEntity: a component is the other case
        return C.cast(ent, "IComponent2").Select4(append, None, False)


def measure(entities=None, kind: str = "all") -> dict:
    """MEASURE the geometry you just built, to compare with the intent. `entities`: 1 or
    2 faces/edges/vertices/components (e.g. from inspect.find_face / asm.find_face;
    through MCP, "@handle_N"); None measures the current selection.
    kind: all | distance | minimum_distance | maximum_distance | angle | radius |
    diameter | length | area.
      - two parallel faces -> distance_mm (and normal_distance_mm), parallel=True;
      - two planar faces/straight edges at an angle -> angle_deg;
      - one cylindrical face or circular edge -> diameter_mm and radius_mm;
      - one edge -> length_mm; one face -> area_mm2 and perimeter_mm;
      - to a hole/arc, `distance` is from its CENTRE; minimum_/maximum_distance measure
        to its nearest/farthest point (measured: hole R5 at 25 mm from a wall -> 25/20/30).
        The same holds for a circular FACE (a pin's end);
      - distances are between the face REGIONS, not their planes: two COPLANAR faces side
        by side (a pin flush with the bottom of the plate it goes through) measured 5.1
        (centre) / 0.1 (minimum, the radial clearance). To check flush / coplanar, read
        `delta_mm` along the face normal (0 when coplanar) with parallel=True.
    kind='all' returns every quantity that applies: {distance_mm, angle_deg, ...,
    parallel, perpendicular, delta_mm}. A specific kind returns {kind, value, unit,
    measured} and RAISES if that quantity does not apply to these entities (the error
    lists what does)."""
    if kind not in _KINDS:
        raise ValueError(f"measure: kind '{kind}' is invalid. Options: {sorted(_KINDS)}")
    md = C.active("IModelDoc2")
    if entities is not None:
        ents = entities if isinstance(entities, (list, tuple)) else [entities]
        if not 1 <= len(ents) <= 2:
            raise ValueError(f"measure: pass 1 or 2 entities, got {len(ents)}")
        md.ClearSelection2(True)
        for i, e in enumerate(ents):
            if not _select_any(e, i > 0):
                md.ClearSelection2(True)
                raise RuntimeError(f"measure: could not select entity #{i} "
                                   "(stale handle after a rebuild? find it again)")
    m = C.cast(C.cast(md.Extension, "IModelDocExtension").CreateMeasure(), "IMeasure")
    m.ArcOption = _KINDS[kind]
    try:
        if not m.Calculate(None):
            raise RuntimeError("measure: SolidWorks could not measure this selection")
        out = {}
        for prop, key, scale in _MEASURED:
            v = getattr(m, prop)
            if v != -1:
                out[key] = round(v * scale, 6)
        # a cylindrical FACE reports only the diameter; an arc edge may report only one
        if "diameter_mm" in out and "radius_mm" not in out:
            out["radius_mm"] = round(out["diameter_mm"] / 2, 6)
        elif "radius_mm" in out and "diameter_mm" not in out:
            out["diameter_mm"] = round(out["radius_mm"] * 2, 6)
        if "distance_mm" in out:
            out["delta_mm"] = [round(getattr(m, a) * 1000.0, 6)
                               for a in ("DeltaX", "DeltaY", "DeltaZ")]
        elif entities is not None and len(ents) == 2 and _KINDS[kind] != 2:
            # MEASURED: two faces a coincident mate made touch come back with NO distance
            # at all (-1), only angle 0 -- a zero gap looked like "not measurable".
            # ClosestDistance answers the minimum distance, 0 included.
            d, p1, p2 = md.ClosestDistance(ents[0], ents[1], None, None)
            if d is not None and d >= 0:
                out["distance_mm"] = round(d * 1000.0, 6)
                out["closest_points_mm"] = [[round(c * 1000.0, 6) for c in p] for p in (p1, p2)]
        if "distance_mm" in out or "angle_deg" in out:
            out["parallel"], out["perpendicular"] = bool(m.IsParallel), bool(m.IsPerpendicular)
    finally:
        if entities is not None:
            md.ClearSelection2(True)
    if kind == "all":
        return out
    key = _KIND_KEY[kind]
    if key not in out:
        raise ValueError(f"measure: '{kind}' does not apply to these entities; "
                         f"measured here: {sorted(out) or 'nothing'}")
    return {"kind": kind, "value": out[key], "unit": key.rsplit("_", 1)[1], "measured": out}


# ── validate (rebuild health) ─────────────────────────────────────────────────
_DOC_KIND = {C.DOC_PART: "part", C.DOC_ASSEMBLY: "assembly", C.DOC_DRAWING: "drawing"}


def _whats_wrong(md):
    """SolidWorks' own "What's Wrong" list: [(feature, type, code, is_warning)]."""
    ext = C.cast(md.Extension, "IModelDocExtension")
    if not ext.GetWhatsWrongCount():
        return []
    _ok, feats, codes, warns = ext.GetWhatsWrong(None, None, None)
    out = []
    for f, code, warn in zip(feats or [], codes or [], warns or []):
        f = C.cast(f, "IFeature")
        out.append((f.Name, f.GetTypeName2(), int(code), bool(warn)))
    return out


def _issue(kind, message, **extra):
    return dict({"type": kind, "message": message}, **extra)


def validate(force: bool = False, interferences: bool = True) -> dict:
    """Is the active document HEALTHY after a rebuild? Run it after building or editing,
    before calling the job done. Rebuilds (force=True rebuilds every feature, slower),
    then collects SolidWorks' "What's Wrong" list plus per-type checks:
      part     -> body_count, sketch states (over-defined / no solution);
      assembly -> mate errors, interferences (interferences=False skips the detection
                  on a large assembly), component states, components whose file is missing;
      drawing  -> every view's referenced model (missing / not loaded).
    Returns {valid, doc_type, rebuild_ok, errors:[...], warnings:[...], ...}. valid=False
    when the rebuild fails or anything is an ERROR; warnings (e.g. an over-defined
    sketch, which SolidWorks still rebuilds) do not make it invalid but are worth fixing.
    MEASURED: the rebuild's own return is not enough -- an over-defined sketch rebuilds
    'fine' and only shows up here, and a fillet or chamfer too big for a thinner part is
    silently adapted, so check the geometry too (inspect.measure, inspect.mass)."""
    md = C.active("IModelDoc2")
    doc_type = _DOC_KIND.get(md.GetType(), "unknown")
    rebuild_ok = bool(md.ForceRebuild3(False) if force else md.EditRebuild3())
    feat_err = C.enum_names("swFeatureError_e")
    errors, warnings = [], []
    for name, ftype, code, warn in _whats_wrong(md):
        item = _issue("feature_rebuild", feat_err.get(code, f"code {code}"),
                      feature=name, feature_type=ftype, code=code)
        (warnings if warn else errors).append(item)
    out = {"doc_type": doc_type, "rebuild_ok": rebuild_ok}

    if doc_type == "part":
        out["body_count"] = len(bodies())
        status = C.enum_names("swConstrainedStatus_e")
        sketches = {}
        raw = md.FirstFeature()
        while raw is not None:
            f = C.cast(raw, "IFeature")
            if f.GetTypeName2() == "ProfileFeature":
                st = C.cast(f.GetSpecificFeature2(), "ISketch").GetConstrainedStatus()
                label = status.get(st, f"status {st}")
                sketches[f.Name] = label
                if st in (4, 5, 6):   # over-constrained / no solution / invalid solution
                    # What's Wrong reports this sketch as 'swFeatureErrorUnknown' --
                    # the sketch's own status says what is actually wrong
                    same = [w for w in errors + warnings if w.get("feature") == f.Name]
                    if same:
                        same[0].update(type="sketch", message=label)
                    else:
                        warnings.append(_issue("sketch", label, feature=f.Name))
            raw = f.GetNextFeature()
        out["sketches"] = sketches
        if not out["body_count"]:
            warnings.append(_issue("geometry", "the part has no solid body"))

    elif doc_type == "assembly":
        for e in A.mate_errors():
            name, code, warn = e["mate"], e["code"], e["warning"]
            item = _issue("mate", feat_err.get(code, f"code {code}"), feature=name, code=code)
            if not any(e.get("feature") == name for e in errors + warnings):
                (warnings if warn else errors).append(item)
        supp = C.enum_names("swComponentSuppressionState_e")
        states, missing = {}, []
        comps = A.components()
        for c in comps:
            st = _suppression(c)
            label = supp.get(st, f"state {st}")
            states[label] = states.get(label, 0) + 1
            path = c.GetPathName()
            if path and not os.path.exists(path):
                missing.append(c.Name2)
                errors.append(_issue("missing_reference", "component file not found",
                                     component=c.Name2, path=path))
        out["component_count"] = len(comps)
        out["component_states"] = states
        if interferences:
            found = A.interferences()
            out["interferences"] = found
            for it in found:
                errors.append(_issue("interference",
                                     f"{it['volume_mm3']:.3f} mm3 of overlapping material",
                                     components=it["comps"]))

    elif doc_type == "drawing":
        views = []
        for sheet in C.active("IDrawingDoc").GetViews() or []:
            for raw in list(sheet or [])[1:]:           # [0] is the sheet itself
                v = C.cast(raw, "IView")
                model = v.GetReferencedModelName()
                entry = {"name": v.Name, "model": model}
                if not model or not os.path.exists(model):
                    entry["state"] = "missing"
                    errors.append(_issue("missing_reference", "view model not found",
                                         view=v.Name, model=model))
                elif v.ReferencedDocument is None:
                    entry["state"] = "not_loaded"
                    warnings.append(_issue("view", "referenced model is not loaded",
                                           view=v.Name, model=model))
                else:
                    entry["state"] = "ok"
                views.append(entry)
        out["views"] = views

    if not rebuild_ok:
        errors.insert(0, _issue("rebuild", "the rebuild reported failure"))
    out["valid"] = rebuild_ok and not errors
    out["errors"], out["warnings"] = errors, warnings
    return out
