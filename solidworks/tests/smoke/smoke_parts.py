# -*- coding: utf-8 -*-
r"""
SMOKE of the PART verbs (solidworks/) -- organised in named BLOCKS.

During development, run ONLY the block under test (it avoids redoing what is already
validated and does not pile windows up). For a regression, run 'all'.

Usage (from the MCP-SolidWorks root; attaches to the open SW or launches it):
  .venv\Scripts\python.exe -u solidworks\tests\smoke\smoke_parts.py [block|all]
  blocks: basic features inspect sketch sketch_dim props_tol patterns hole wall_body
          body_ops adv_features material dimension validate read_back configs
          (default: all)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from solidworks import sw_com as C, sw_parts as P, sw_inspect as I, sw_sketch as S  # noqa: E402
from solidworks.tests import isolation as T  # noqa: E402

# shared tests/validation_assembly/ in development, a new directory per run in public
OUT = T.output_root()


# ── helpers ───────────────────────────────────────────────────────────────────
def nfaces():
    b = C.cast(C.active("IPartDoc").GetBodies2(0, False)[0], "IBody2")
    return len(b.GetFaces())


def cyl_count():
    b = C.cast(C.active("IPartDoc").GetBodies2(0, False)[0], "IBody2")
    return sum(1 for f in b.GetFaces()
               if C.cast(C.cast(f, "IFace2").GetSurface(), "ISurface").IsCylinder())


def block(w=100, h=100, d=20):
    P.new_part()
    P.sketch("Top", "rect", x1=0, y1=0, x2=w, y2=h)
    P.extrude(d, "boss")


def block_centered(w=100, h=100, d=20):
    P.new_part()
    P.sketch("Top", "rect", x1=-w / 2, y1=-h / 2, x2=w / 2, y2=h / 2)
    P.extrude(d, "boss")


def seed_hole(r, cx, cy):
    P.sketch("Top", "circle", r=r, cx=cx, cy=cy)
    try:
        return P.extrude(0, "cut", through_all=True)
    except RuntimeError:
        P.sketch("Top", "circle", r=r, cx=cx, cy=cy)
        return P.extrude(0, "cut", through_all=True, reverse=True)


class Chk:
    def __init__(self):
        self.ok = self.n = 0

    def __call__(self, name, cond, detail=""):
        self.n += 1
        self.ok += bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL '}] {name}" + (f" -- {detail}" if detail else ""))


# ── blocks ────────────────────────────────────────────────────────────────────
def basic(chk):
    block()
    chk("block (rect+boss)", nfaces() == 6, f"{nfaces()} faces")
    seed_hole(10, 50, 50)
    chk("through hole (cut through-all)", nfaces() == 7, f"{nfaces()} faces")
    p = P.save(os.path.join(OUT, "bloco_furo.sldprt"))
    chk("save .sldprt", os.path.exists(p))
    e = P.export(os.path.join(OUT, "bloco_furo.step"))
    chk("export .step", os.path.exists(e))
    P.new_part(); P.sketch("Top", "circle", r=15); P.extrude(40, "boss")
    chk("cylinder (circle+boss)", nfaces() == 3, f"{nfaces()} faces")


def features(chk):
    block(); fil = P.fillet(5, edges=I.edges())
    chk("fillet (12 edges)", nfaces() > 6, f"{nfaces()} faces")
    try:     # the way back after a wrong operation: the block is a plain box again
        chk("delete_feature (the fillet)", P.delete_feature(fil) == fil and nfaces() == 6,
            f"{fil}: {nfaces()} faces")
    except Exception as exc:  # noqa: BLE001 -- keep the rest of the block measurable
        chk("delete_feature (the fillet)", False, f"{type(exc).__name__}: {exc}")
    block(60, 60); P.chamfer(5, edges=I.edges())
    chk("chamfer (12 edges)", nfaces() > 6, f"{nfaces()} faces")
    block(50, 50); P.reference_plane("Top", 30, name="datum_top")
    planes = [f["name"] for f in I.list_features() if f["type"] == "RefPlane"]
    chk("reference_plane (datum)", "datum_top" in planes, str(planes))
    P.new_part(); S.begin("Front"); S.centerline(0, -50, 0, 50); S.rect(10, -5, 20, 5); S.end()
    P.revolve(360, "boss")
    vol = I.mass()["volume_m3"]
    chk("revolve (ring)", abs(vol - 9.4248e-6) < 1e-7, f"vol={vol:.3e} m3")


def inspect(chk):
    block()
    m = I.mass()
    chk("mass properties", m["mass_kg"] > 0, f"{m['mass_kg']:.3f} kg")
    chk("screenshot", os.path.exists(I.screenshot(os.path.join(OUT, "smoke.bmp"))))


def validate(chk):
    """inspect.measure + inspect.validate: the loop after building (measure the geometry
    against the intent, then ask whether the document rebuilt clean)."""
    block()                                  # 100 x 100 on Top, 20 tall along Y
    seed_hole(10, 50, 50)
    ytop, ybot = I.find_face("plane", 1, True), I.find_face("plane", 1, False)
    xmax, hole = I.find_face("plane", 0, True), I.find_face("cylinder", radius_mm=10)
    d = I.measure([ytop, ybot], "distance")
    chk("measure distance (parallel faces)", abs(d["value"] - 20) < 1e-6 and d["measured"]["parallel"],
        f"{d['value']} mm")
    chk("measure angle", abs(I.measure([ytop, xmax], "angle")["value"] - 90) < 1e-6)
    chk("measure diameter (hole)", abs(I.measure([hole], "diameter")["value"] - 20) < 1e-6)
    c = I.measure([hole, xmax], "distance")["value"]
    m = I.measure([hole, xmax], "minimum_distance")["value"]
    chk("measure hole->wall: centre / minimum", abs(c - 50) < 1e-6 and abs(m - 40) < 1e-6,
        f"{c} / {m} mm")
    try:
        I.measure([ytop], "angle")
        chk("measure refuses a kind that does not apply", False)
    except ValueError as exc:
        chk("measure refuses a kind that does not apply", "does not apply" in str(exc))
    v = I.validate()
    chk("validate: healthy part", v["valid"] and v["body_count"] == 1 and not v["warnings"],
        f"errors={v['errors']} warnings={v['warnings']}")
    # a real rebuild ERROR: a cut sketched on a face whose feature is then deleted
    P.new_part()
    P.sketch("Front", "rect", x1=0, y1=0, x2=50, y2=30)
    base = P.extrude(10, "boss")
    P.sketch("Top", "rect", x1=0, y1=-5, x2=60, y2=40)
    P.extrude(3, "boss")
    S.begin_on_face(I.find_face("plane", 2, True))
    S.circle(25, 15, 4)
    P.extrude(3, "cut", sketch_name=S.end())
    ext = C.cast(C.active("IModelDoc2").Extension, "IModelDocExtension")
    ext.SelectByID2(base, "BODYFEATURE", 0, 0, 0, False, 0, None, 0)
    ext.DeleteSelection2(0)
    v = I.validate()
    # the enum depends on the version (measured 2026-09-25): SW 2026 blames the cut that
    # no longer intersects, SW 2017 blames its sketch, which lost the face it sat on
    broken = {"swFeatureErrorCutNotIntersectModel", "swSketchErrorExtRefFail"}
    chk("validate: a broken feature makes it invalid, with the enum name",
        not v["valid"] and any(e.get("message") in broken for e in v["errors"]),
        f"{[e['message'] for e in v['errors']]}")
    # a WARNING: an over-defined sketch rebuilds 'fine'
    P.new_part()
    S.begin("Front")
    ln = S.line(0, 0, 40, 10)
    S.relation([ln], "horizontal")
    S.relation([ln], "vertical")
    S.end()
    v = I.validate()
    chk("validate: over-defined sketch is a warning (still valid)",
        v["valid"] and any(w.get("message") == "swOverConstrained" for w in v["warnings"]),
        f"{v['warnings']}")


def sketch(chk):
    P.new_part(); S.begin("Front")
    c1, c2 = S.circle(0, 0, 20), S.circle(35, 15, 8)
    rel = S.relation([c1, c2], "concentric")
    S.end()
    chk("sketch relation (concentric)", rel is not None)
    # polygon -> hexagonal prism (8 faces)
    P.new_part(); S.begin("Top"); S.polygon(0, 0, 30, 6); S.end(); P.extrude(20, "boss")
    chk("sketch polygon (hexagon)", nfaces() == 8, f"{nfaces()} faces")
    # slot -> rounded prism (6 faces)
    P.new_part(); S.begin("Top"); S.slot(-30, 0, 30, 0, 20); S.end(); P.extrude(15, "boss")
    chk("sketch slot", nfaces() == 6, f"{nfaces()} faces")
    # spline (creation)
    P.new_part(); S.begin("Top"); sp = S.spline([(0, 0), (20, 15), (40, -5), (60, 10)]); S.end()
    chk("sketch spline", sp is not None)


def _seg_ends(s):
    ln = C.cast(s, "ISketchLine")
    a = C.cast(ln.GetStartPoint2(), "ISketchPoint")
    b = C.cast(ln.GetEndPoint2(), "ISketchPoint")
    return (a.X, a.Y), (b.X, b.Y)


def sketch_dim(chk):
    # a PARAMETRIC sketch dimension as a verb: an L120 x W40 rectangle drivable BY NAME
    P.new_part()
    S.begin("Top")
    segs = S.rect(0, 0, 120, 40)
    horiz = next(s for s in segs if abs(_seg_ends(s)[0][1] - _seg_ends(s)[1][1]) < 1e-6)
    vert = next(s for s in segs if abs(_seg_ends(s)[0][0] - _seg_ends(s)[1][0]) < 1e-6)
    nL = S.dimension(horiz, (60, -15), orient="horizontal", name="L")
    nW = S.dimension(vert, (-15, 20), orient="vertical", name="W")
    S.end()
    P.extrude(25, "boss")
    chk("dimension length (L=120)", abs(P.get_dimension(nL) - 120) < 0.01, f"{nL}")
    chk("dimension width (W=40)", abs(P.get_dimension(nW) - 40) < 0.01, f"{nW}")
    chk("stable name (rename 'L')", nL.startswith("L@"), nL)
    # the dimension DRIVES the sketch (the optimisation loop uses this)
    P.set_dimension(nL, 150)
    chk("drivable sketch dimension (L->150)", abs(P.get_dimension(nL) - 150) < 0.01)
    # a circle's diameter (1 circular entity -> Ø)
    P.new_part()
    S.begin("Top")
    c = S.circle(0, 0, 6)
    nd = S.dimension(c, (20, 20), name="hole")
    S.end()
    chk("dimension diameter (Ø12)", abs(P.get_dimension(nd) - 12) < 0.01, f"{nd}")
    # distance between 2 points (aligned)
    P.new_part()
    S.begin("Top")
    p1, p2 = S.point(0, 0), S.point(30, 40)
    ndist = S.dimension([p1, p2], (15, 20), orient="aligned")
    S.end()
    chk("dimension distance between 2 points (50)", abs(P.get_dimension(ndist) - 50) < 0.01, ndist)


def props_tol(chk):
    # get_property: a round-trip with set_property
    block()
    P.set_property("Description", "VIGA X")
    chk("get_property round-trip", P.get_property("Description") == "VIGA X",
        P.get_property("Description"))
    chk("a missing get_property -> ''", P.get_property("DoesNotExist") == "")
    # set_tolerance symmetric + basic (only fit+bilateral were covered) on a sketch dimension
    P.new_part()
    S.begin("Top"); segs = S.rect(0, 0, 80, 50)
    horiz = next(s for s in segs if abs(_seg_ends(s)[0][1] - _seg_ends(s)[1][1]) < 1e-6)
    nL = S.dimension(horiz, (40, -15), orient="horizontal", name="Lx")
    S.end(); P.extrude(20, "boss")
    md = C.active("IModelDoc2")

    def _tol():
        dim = C.cast(md.Parameter(nL), "IDimension")
        return C.cast(dim.Tolerance, "IDimensionTolerance")
    P.set_tolerance(nL, "symmetric", upper_mm=0.15)
    t = _tol()
    lo, hi = t.GetMinValue() * 1000, t.GetMaxValue() * 1000   # metres -> mm
    chk("tolerance symmetric (Type=4, +/-0.15)",
        t.Type == P.TOL_SYMMETRIC and abs(hi - 0.15) < 1e-4 and abs(lo + 0.15) < 1e-4,
        f"type={t.Type} min={lo:.3f} max={hi:.3f}")
    P.set_tolerance(nL, "basic")
    chk("tolerance basic (Type=1)", _tol().Type == P.TOL_BASIC, f"type={_tol().Type}")


def patterns(chk):
    block(); seed = seed_hole(4, 15, 15); edge = I.find_edge(0)
    P.pattern_linear(seed, edge, 4, 20, reverse=I.edge_dir(edge)[0] < 0)
    chk("pattern_linear (4 holes)", cyl_count() == 4, f"{cyl_count()}")
    block_centered(); seed = seed_hole(5, 20, 0); P.pattern_mirror(seed, "Right")
    chk("pattern_mirror", cyl_count() == 2, f"{cyl_count()}")
    block_centered(); ax = P.reference_axis("Front", "Right", name="axis1")
    seed = seed_hole(4, 30, 0); P.pattern_circular(seed, ax, 6, 360)
    chk("pattern_circular (6 holes)", cyl_count() == 6, f"{cyl_count()}")


def hole(chk):
    block()
    pos = [(25, 25), (75, 25), (25, 75), (75, 75)]
    try:
        P.hole("Top", 10, pos, through_all=True)
    except RuntimeError:
        P.hole("Top", 10, pos, through_all=True, reverse=True)
    chk("hole (4 holes, 1 cut)", cyl_count() == 4, f"{cyl_count()}")
    # counterbore (a Ø10 hole + a Ø18 recess) on the top face -> 2 coaxial diameters
    block()
    P.counterbore([(50, 50)], 10, 18, 6)
    rs = sorted(round(C.cast(C.cast(f, "IFace2").GetSurface(), "ISurface").CylinderParams[6] * 1000, 1)
                for f in C.cast(C.active("IPartDoc").GetBodies2(0, False)[0], "IBody2").GetFaces()
                if C.cast(C.cast(f, "IFace2").GetSurface(), "ISurface").IsCylinder())
    chk("counterbore (Ø10+Ø18)", rs == [5.0, 9.0], f"radii {rs}")
    # countersink (a D8 hole + a cone) -> a CONICAL face has to exist
    block()
    P.countersink([(50, 50)], 8, 16, 90)
    has_cone = any(C.cast(C.cast(f, "IFace2").GetSurface(), "ISurface").IsCone()
                   for f in C.cast(C.active("IPartDoc").GetBodies2(0, False)[0], "IBody2").GetFaces())
    chk("countersink (cone)", has_cone)


def wall_body(chk):
    block(); top = I.find_face("plane", axis=1, want_max=True)
    m0 = I.mass()["mass_kg"]; P.shell(2, [top])
    chk("shell (hollow, sheds mass)", I.mass()["mass_kg"] < m0,
        f"{m0:.3f}->{I.mass()['mass_kg']:.3f} kg")
    block(); v0 = I.mass()["volume_m3"]; P.scale(2.0)
    chk("scale 2x (vol x8)", abs(I.mass()["volume_m3"] / v0 - 8) < 0.1,
        f"ratio {I.mass()['volume_m3']/v0:.1f}")


def two_bodies(overlap_x=40):
    """Plate A [0,60]x and plate B [overlap_x, overlap_x+60]x -> 2 crossing bodies."""
    P.new_part()
    P.sketch("Top", "rect", x1=0, y1=0, x2=60, y2=60); P.extrude(20, "boss")
    P.sketch("Top", "rect", x1=overlap_x, y1=0, x2=overlap_x + 60, y2=60)
    P.extrude(20, "boss", merge=False)


def nbodies():
    return len(I.bodies())


def body_ops(chk):
    # combine ADD (joins ALL the bodies: main=None internally)
    two_bodies()
    chk("multibody (extrude merge=False)", nbodies() == 2, f"{nbodies()} bodies")
    P.combine("add")
    chk("combine add -> 1 body", nbodies() == 1, f"{nbodies()}")
    # combine COMMON (a 20x60x20 intersection = 2.4e-5 m3)
    two_bodies(); P.combine("common")
    v = I.mass()["volume_m3"]
    chk("combine common (intersection)", abs(v - 2.4e-5) < 2e-6, f"vol={v:.3e} m3")
    # combine SUBTRACT (main stays, tools remove)
    two_bodies(); bs = I.bodies()
    P.combine("subtract", main_body=bs[0], tool_bodies=[bs[1]])
    chk("combine subtract -> 1 body", nbodies() == 1, f"{nbodies()}")
    # draft: tilts the 4 sides relative to the top (neutral) -> the volume changes
    block(60, 60, 40)
    top = I.find_face("plane", axis=1, want_max=True)
    sides = [f for f in I.faces()
             if C.cast(f.GetSurface(), "ISurface").IsPlane()
             and abs(C.cast(f.GetSurface(), "ISurface").PlaneParams[1]) < 0.1]
    v0 = I.mass()["volume_m3"]
    P.draft(top, sides, 5)
    chk("draft (neutral=top, 4 faces)", abs(I.mass()["volume_m3"] - v0) > 1e-8,
        f"{v0:.3e}->{I.mass()['volume_m3']:.3e}")
    # an embedded equation drives an existing dimension: D1@Fillet1 = radius - clearance = 7.7
    block(); P.fillet(5, edges=I.edges())
    P.add_equation('"radius" = 8'); P.add_equation('"clearance" = 0.3')
    P.add_equation('"D1@Fillet1" = "radius" - "clearance"')
    chk("add_equation drives a dimension (7.7mm)",
        abs(P.get_dimension("D1@Fillet1") - 7.7) < 0.01, f"{P.get_dimension('D1@Fillet1')} mm")


def adv_features(chk):
    # sweep: an r8 circular profile swept along a straight 60mm path -> a cylinder
    P.new_part()
    S.begin("Front"); S.line(0, 0, 0, 60); path = S.end()
    S.begin("Top"); S.circle(0, 0, 8); prof = S.end()
    P.sweep(prof, path)
    v = I.mass()["volume_m3"]
    chk("sweep (cylinder r8 h60)", abs(v - 1.206e-5) < 1e-6, f"vol={v:.3e}")
    # loft between 2 circles on parallel planes -> a truncated cone
    P.new_part()
    S.begin("Top"); S.circle(0, 0, 20); p1 = S.end()
    P.reference_plane("Top", 50, name="alto")
    S.begin("alto"); S.circle(0, 0, 8); p2 = S.end()
    P.loft([p1, p2])
    v = I.mass()["volume_m3"]
    chk("loft (truncated cone)", abs(v - 3.266e-5) < 3e-6, f"vol={v:.3e}")
    # extrude thin: a circle -> a hollow tube (2 cylinders, 4 faces)
    P.new_part(); P.sketch("Top", "circle", r=20); P.extrude(30, "boss", thin_mm=2)
    chk("extrude thin (hollow tube)", cyl_count() == 2 and nfaces() == 4,
        f"faces={nfaces()} cyl={cyl_count()}")
    # delete_body: discards 1 of 2 bodies
    P.new_part()
    P.sketch("Top", "rect", x1=0, y1=0, x2=40, y2=40); P.extrude(20, "boss")
    P.sketch("Top", "rect", x1=60, y1=0, x2=100, y2=40); P.extrude(20, "boss", merge=False)
    P.delete_body([I.bodies()[1]])
    chk("delete_body -> 1 body", len(I.bodies()) == 1, f"{len(I.bodies())}")
    # reference_point (the top face's centre)
    block(60, 60, 40)
    P.reference_point([I.find_face("plane", axis=1, want_max=True)], "face_center", name="pt")
    chk("reference_point face_center", any(f["type"] == "RefPoint" for f in I.list_features()))
    # reference_csys (from a vertex)
    block(60, 60, 40)
    v0 = C.cast(I.edges()[0].GetStartVertex(), "IVertex")
    P.reference_csys(v0, name="cs")
    chk("reference_csys", any(f["type"] == "CoordSys" for f in I.list_features()))


def material(chk):
    block(); P.set_material("6061 Alloy")
    name, db = P.get_material()
    chk("material set/get", name == "6061 Alloy", f"{name} / {db}")


def dimension(chk):
    block(); P.fillet(5, edges=I.edges())
    P.set_dimensions({"D1@Boss-Extrude1": 30, "D1@Fillet1": 10})  # the loop's pattern
    ok = P.get_dimension("D1@Boss-Extrude1") == 30 and P.get_dimension("D1@Fillet1") == 10
    chk("dimension get/set/batch", ok)


def read_back(chk):
    """inspect.sketch + inspect.model_summary: READ a part back (what an agent does on a
    file it did not build)."""
    P.new_part()
    S.begin("Front")
    l1 = S.line(0, 0, 60, 0); S.line(60, 0, 60, 30); S.line(60, 30, 0, 30); S.line(0, 30, 0, 0)
    c = S.circle(30, 15, 5)
    S.relation([l1], "horizontal")
    S.dimension([l1], (30, -10), name="width")
    S.dimension([c], (45, 40))
    sk = S.end()
    r = I.sketch(sk)
    chk("sketch: 4 lines + a circle", sum(e["type"] == "line" for e in r["entities"]) == 4
        and any(e["type"] == "circle" and abs(e["radius_mm"] - 5) < 1e-6 for e in r["entities"]))
    chk("sketch: named dimensions in mm",
        any(d["name"] == f"width@{sk}" and abs(d["value"] - 60) < 1e-6 for d in r["dimensions"]),
        str(r["dimensions"]))
    chk("sketch: relations by entity name",
        any(x["type"] == "horizontal" and x["entities"] == ["Line1"] for x in r["relations"]))
    chk("sketch: under-defined", r["status"] == "under_defined" and not r["fully_defined"])
    P.extrude(10, "boss")
    s = I.model_summary()
    chk("model_summary: features, dimensions, bbox, health",
        [f["type"] for f in s["features"]] == ["ProfileFeature", "Extrusion"]
        and any(d["name"] == "D1@Boss-Extrude1" for d in s["dimensions"])
        and s["bounding_box"]["size_mm"][2] == 10.0 and s["health"]["valid"],
        str({k: s[k] for k in ("features", "bounding_box")}))


def configs(chk):
    """part.*configuration + set_dimension(config=): a variant in the same file."""
    block(50, 30, 20)
    default = P.configurations()["active"]
    P.add_configuration("Large")
    P.set_dimension("D1@Boss-Extrude1", 40, config="Large")
    vol = lambda: I.mass()["volume_m3"] * 1e9  # noqa: E731
    chk("configs: the dimension changed only in 'Large'",
        abs(P.get_dimension("D1@Boss-Extrude1", config=default) - 20) < 1e-6
        and abs(vol() - 50 * 30 * 40) < 1, f"{vol():.0f} mm3")
    P.activate_configuration(default)
    chk("configs: activating the default brings 20 back", abs(vol() - 50 * 30 * 20) < 1)
    P.rename_configuration("Large", "Tall")
    chk("configs: rename + delete",
        "Tall" not in P.delete_configuration("Tall") and P.configurations()["names"] == [default])


BLOCKS = {"basic": basic, "features": features, "inspect": inspect,
          "sketch": sketch, "sketch_dim": sketch_dim, "props_tol": props_tol,
          "patterns": patterns, "hole": hole,
          "wall_body": wall_body, "body_ops": body_ops, "adv_features": adv_features,
          "material": material, "dimension": dimension, "validate": validate,
          "read_back": read_back, "configs": configs}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    names = list(BLOCKS) if which == "all" else [which]
    print(f"SW rev {C.app().RevisionNumber()} | blocks: {names}")
    T.begin()
    T.close_scratch()  # starts clean
    chk = Chk()
    for name in names:
        print(f"\n== {name} ==")
        # hygiene PER BLOCK: every check opens a new Part, and on SW 2017 ~20 open
        # windows exhaust the GDI handles and the SW hangs (measured 2026-09-25: it
        # died at the start of body_ops, the 26th doc of the run)
        T.close_scratch()
        try:
            BLOCKS[name](chk)
        except Exception as e:  # a crashed block is a FAIL, not the end of the run
            chk(f"{name}: block crashed", False, f"{type(e).__name__}: {e}")
            if "RPC" in str(e):  # the SW itself died -- the next blocks cannot run
                break
    T.close_scratch()  # does not leave parts open
    T.finish()
    print(f"\nSUMMARY: {chk.ok}/{chk.n} PASS")


if __name__ == "__main__":
    main()
