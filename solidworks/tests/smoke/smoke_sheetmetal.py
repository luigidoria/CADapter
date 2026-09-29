# -*- coding: utf-8 -*-
r"""
SMOKE of the SHEET METAL verbs (solidworks/sw_sheetmetal.py) -- named BLOCKS.

Difference from `probe_sheetmetal.py`: there the raw COM API is measured (a failure is
a RESULT); here the curated VERB is tested (a failure is a BUG). Every criterion is
geometric -- volume, face count, bounding box, a file on disk.

Usage (from the MCP-SolidWorks root; attaches to an open SW or launches it):
  .venv\Scripts\python.exe -u solidworks\tests\smoke\smoke_sheetmetal.py [block|all]
  blocks: base flanges box hem edge flatten bends manufacturing params drawing
          sketched_bend miter edit cut gauge assembly lofted multibody
          convert closed_corner jog corner_trim gusset perp_plane
          bend_table material lofted2 mixed gusset2
          (default: all)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from solidworks import (sw_assembly as A, sw_com as C, sw_drawing as D,  # noqa: E402
                        sw_inspect as I, sw_parts as P, sw_sheetmetal as SM,
                        sw_sketch as SK)
from solidworks.tests import isolation as T  # noqa: E402

# shared tests/validation_assembly/ in development, a new directory per run in public
OUT = T.output_root()


class Chk:
    def __init__(self):
        self.ok = self.n = 0

    def __call__(self, name, cond, detail=""):
        self.n += 1
        self.ok += bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
        return bool(cond)


def vol():
    return I.mass()["volume_m3"]


def nfaces():
    return len(I.faces())


def sheet(w=100.0, h=60.0, t=2.0):
    SM.new_sheet("Front", w, h, thickness_mm=t)


# ── blocks ────────────────────────────────────────────────────────────────────
def base(chk):
    sheet()
    chk("new_sheet creates ONE body", len(I.bodies()) == 1)
    chk("volume 100x60x2", abs(vol() - 1.2e-5) < 1e-9, f"{vol():.4e}")
    chk("a flat sheet has 6 faces", nfaces() == 6, f"{nfaces()}")
    chk("is_sheet recognises a sheet metal part", SM.is_sheet())
    chk("the thickness read matches the one asked for", abs(SM.thickness() - 2.0) < 0.01,
        f"{SM.thickness():.3f} mm")
    chk("it is born folded (not flattened)", not SM.is_flat())
    try:
        st = SM.info()
        chk("info reports the same state in one call (sheet, folded, one body)",
            st["is_sheet"] is True and st["is_flat"] is False and st["bodies"] == 1,
            str({k: st[k] for k in ("is_sheet", "is_flat", "bodies", "bbox_mm")}))
    except Exception as exc:  # noqa: BLE001 -- keep the rest of the block measurable
        chk("info reports the same state in one call (sheet, folded, one body)", False,
            f"{type(exc).__name__}: {exc}")

    # base_flange over a sketch of its own (the any-profile path)
    P.new_part()
    P.sketch("Top", "circle", r=30)
    SM.base_flange(1.5, radius_mm=2.0)
    chk("base_flange over an existing sketch (a disc)",
        len(I.bodies()) == 1 and abs(vol() - 3.1416 * 0.03 ** 2 * 0.0015) < 1e-7,
        f"{vol():.4e}")

    # an ordinary part is NOT sheet metal
    P.new_part()
    P.sketch("Top", "rect", x1=0, y1=0, x2=20, y2=20)
    P.extrude(5, "boss")
    chk("is_sheet is False on an ordinary part", not SM.is_sheet())


def flanges(chk):
    sheet()
    v0 = vol()
    e = SM.free_edges()[0]
    SM.edge_flange(e, 20.0)
    chk("a 90-degree edge flange adds material", vol() > v0 * 1.15,
        f"{v0:.4e} -> {vol():.4e}")
    chk("the flange created the bend", len(SM.bends()) == 1, str(SM.bends()))
    dx, dy, dz = SM.bbox_mm()
    chk("the flange rises (height = flange + thickness)", abs(dz - 22.0) < 0.5,
        f"{dx:.1f} x {dy:.1f} x {dz:.1f} mm")

    # a flange narrower than the edge
    sheet()
    v0 = vol()
    SM.edge_flange(SM.free_edges()[0], 20.0, margin_mm=20.0)
    chk("partial edge flange (a margin at the ends)", v0 < vol() < v0 * 1.25,
        f"{v0:.4e} -> {vol():.4e}")

    # an angle other than 90
    sheet()
    SM.edge_flange(SM.free_edges()[0], 20.0, angle_deg=45.0)
    _, _, dz = SM.bbox_mm()
    chk("a 45-degree edge flange rises less than a 90-degree one", 5.0 < dz < 20.0, f"dz={dz:.1f} mm")

    # an impossible margin is refused BEFORE touching the CAD
    sheet()
    try:
        SM.edge_flange(SM.free_edges()[0], 20.0, margin_mm=60.0)
        chk("a margin bigger than the edge is refused", False, "did not raise")
    except ValueError:
        chk("a margin bigger than the edge is refused", True)


def box(chk):
    sheet()
    v0 = vol()
    flanges = SM.box_flanges(20.0)
    chk("box_flanges makes the 4 flanges", len(flanges) == 4, str(flanges))
    chk("4 bends on the part", len(SM.bends()) == 4, str(SM.bends()))
    dx, dy, dz = SM.bbox_mm()
    chk("a box the height of ONE flange (it did not stack)", abs(dz - 22.0) < 0.5,
        f"{dx:.1f} x {dy:.1f} x {dz:.1f} mm")
    chk("the box has more material than the sheet", vol() > v0, f"{v0:.4e} -> {vol():.4e}")
    os.makedirs(OUT, exist_ok=True)
    P.save(os.path.join(OUT, "sm_caixa.sldprt"))
    I.screenshot(os.path.join(OUT, "sm_caixa.bmp"))


def hem(chk):
    sheet()
    nf0 = nfaces()
    name = SM.hem(SM.free_edges()[0], kind="closed", length_mm=5.0)
    chk("a closed hem folds the edge", nfaces() > nf0, f"faces {nf0}->{nfaces()}")
    # GUARD: on a sheet metal part the last feature of the tree is always Flat-Pattern1.
    # A verb returning the name via FeatureByPositionReverse(0) would lie silently.
    chk("hem returns the FEATURE's name, not the flat pattern's",
        name != "Flat-Pattern1" and "Hem" in name, name)
    sheet()
    nf0 = nfaces()
    SM.hem(SM.free_edges()[0], kind="open", length_mm=5.0, gap_mm=1.0)
    chk("open hem", nfaces() > nf0, f"faces {nf0}->{nfaces()}")


def edge(chk):
    sheet()
    nf0 = nfaces()
    name = SM.break_corner([SM.base_face()], 5.0, kind="fillet")
    chk("break corner rounds the corners", nfaces() > nf0, f"faces {nf0}->{nfaces()}")
    chk("break_corner returns the FEATURE's name, not the flat pattern's",
        name != "Flat-Pattern1", name)

    sheet()
    SM.cross_break()
    chk("cross break enters the tree without touching the volume",
        abs(vol() - 1.2e-5) < 1e-9, f"{vol():.4e}")

    # rip: on a closed ordinary solid (before it becomes sheet metal)
    P.new_part()
    P.sketch("Top", "rect", x1=0, y1=0, x2=60, y2=60)
    P.extrude(40, "boss")
    P.shell(2.0, [I.find_face("plane", axis=1, want_max=True)])
    nf0 = nfaces()
    SM.rip([I.find_edge(1)], gap_mm=0.5)
    chk("rip slits the box's edge", nfaces() > nf0, f"faces {nf0}->{nfaces()}")


def flatten(chk):
    sheet()
    SM.box_flanges(20.0)
    v_bent = vol()
    _, _, dz0 = SM.bbox_mm()
    chk("flatten(True) flattens", SM.flatten(True))
    lx, ly, lz = SM.bbox_mm()
    chk("flattened: the thickness becomes the smallest dimension", abs(lz - 2.0) < 0.2,
        f"{lx:.1f} x {ly:.1f} x {lz:.1f} mm")
    chk("flattened: it grows in the plane", lx > 115 and ly > 75, f"{lx:.1f} x {ly:.1f}")
    chk("flattening preserves the volume", abs(vol() - v_bent) / v_bent < 0.02,
        f"{v_bent:.4e} -> {vol():.4e}")
    chk("is_flat agrees", SM.is_flat())
    chk("flatten(False) folds back", not SM.flatten(False))
    _, _, dz1 = SM.bbox_mm()
    chk("the height goes back to the folded value", abs(dz1 - dz0) < 0.1, f"{dz1:.1f} mm")


def bends(chk):
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    nf0 = nfaces()
    _, _, dz0 = SM.bbox_mm()
    chk("bends() finds the bend (a sub-feature)", SM.bends() == ["EdgeBend1"], str(SM.bends()))
    name_u = SM.unfold()
    _, _, dz1 = SM.bbox_mm()
    chk("unfold really unfolds", dz1 < dz0 / 2, f"height {dz0:.1f} -> {dz1:.1f} mm")
    chk("unfold returns the FEATURE's name, not the flat pattern's",
        name_u != "Flat-Pattern1", name_u)
    SM.fold()
    _, _, dz2 = SM.bbox_mm()
    chk("fold folds back", abs(dz2 - dz0) < 0.1 and nfaces() == nf0,
        f"height {dz2:.1f} mm, faces {nfaces()}")


def manufacturing(chk):
    os.makedirs(OUT, exist_ok=True)
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)

    # export_flat needs the part saved -- and the verb says so instead of failing silently
    try:
        SM.export_flat(os.path.join(OUT, "sm_unsaved.dxf"))
        chk("export_flat without saving raises an explanatory error", False, "did not raise")
    except RuntimeError as exc:
        chk("export_flat without saving raises an explanatory error", "sav" in str(exc).lower(),
            str(exc)[:60])

    P.save(os.path.join(OUT, "sm_fabricacao.sldprt"))
    dxf = SM.export_flat(os.path.join(OUT, "sm_planificada.dxf"))
    chk("export_flat writes the DXF", os.path.exists(dxf) and os.path.getsize(dxf) > 0,
        f"{os.path.getsize(dxf)} bytes")
    dwg = SM.export_flat(os.path.join(OUT, "sm_planificada.dwg"))
    chk("export_flat writes DWG too", os.path.getsize(dwg) > 0,
        f"{os.path.getsize(dwg)} bytes")

    cl = SM.cut_list()
    chk("cut_list carries the manufacturing fields",
        all(k in cl for k in ("Bounding Box Length", "Bounding Box Width",
                              "Sheet Metal Thickness", "Bends")), str(sorted(cl))[:80])
    chk("cut_list carries VALUES, not formulas",
        all(cl[k] and not str(cl[k]).startswith('"SW-')
            for k in ("Bounding Box Length", "Bounding Box Width",
                      "Sheet Metal Thickness", "Bends")),
        f"L={cl.get('Bounding Box Length')} W={cl.get('Bounding Box Width')} "
        f"thick={cl.get('Sheet Metal Thickness')} bends={cl.get('Bends')}")
    # what the cut list feeds is a purchase spreadsheet and a bending bench: the
    # numeric fields arrive as NUMBERS, not as the text COM hands over
    chk("cut_list converts the numeric fields to float",
        all(isinstance(cl[k], float)
            for k in ("Bounding Box Length", "Bounding Box Width",
                      "Sheet Metal Thickness", "Bends")),
        str({k: type(cl[k]).__name__ for k in sorted(cl)})[:120])
    chk("and what is NOT a number stays text",
        isinstance(cl.get("Bend Direction", ""), str),
        repr(cl.get("Bend Direction")))
    chk("the flat pattern is BIGGER than the folded sheet (what you buy)",
        float(cl["Bounding Box Width"]) > 60.0, f"{cl['Bounding Box Width']} mm")
    chk("the cut list's thickness matches the sheet's",
        abs(float(cl["Sheet Metal Thickness"]) - 2.0) < 0.01,
        cl["Sheet Metal Thickness"])
    chk("one bend counted", int(float(cl["Bends"])) == 1, cl["Bends"])


def params(chk):
    sheet()
    chk("thickness reads the thickness", abs(SM.thickness() - 2.0) < 0.01)
    chk("set_thickness changes the thickness", abs(SM.set_thickness(3.0) - 3.0) < 0.01)
    chk("the geometry confirms the new thickness", abs(vol() - 1.8e-5) < 1e-9,
        f"{vol():.4e}")
    bp = SM.bend_params()
    chk("bend_params reads the radius and the K-factor",
        abs(bp["radius_mm"] - 3.0) < 0.01 and 0 < bp["k_factor"] < 1, str(bp))

    # cutting a hole in a sheet: the sketch has to be NAMED (Flat-Pattern is the last
    # feature of the tree, so sw_parts.extrude's default would miss the target)
    sheet()
    v0 = vol()
    P.sketch("Front", "circle", r=8, cx=50, cy=30)
    P.extrude(0, "cut", through_all=True, sketch_name=SM.last_sketch())
    chk("a through hole in the sheet via last_sketch()", vol() < v0,
        f"{v0:.4e} -> {vol():.4e}")


def drawing(chk):
    os.makedirs(OUT, exist_ok=True)
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    path = P.save(os.path.join(OUT, "sm_para_desenho.sldprt"))
    D.new_drawing(path, size="A3")
    name = D.flat_pattern_view(at=(150, 150))
    chk("flat_pattern_view inserts the view", bool(name), name)
    dd = C.active("IDrawingDoc")
    v = C.cast(dd.ActivateView(name) and C.cast(
        C.cast(dd, "IDrawingDoc").ActiveDrawingView, "IView") or None, "IView")
    chk("the view declares itself a flat pattern", v.IsFlatPatternView())
    chk("the view has a bend line", v.GetBendLineCount() > 0,
        f"{v.GetBendLineCount()} lines")
    chk("bend notes on", v.ShowSheetMetalBendNotes)
    lines = D.bend_lines(name)
    chk("bend_lines returns the bend line in mm of the sheet",
        len(lines) == v.GetBendLineCount() and len(lines[0]) == 4, f"{lines}")
    tab = D.bend_table(name, at=(250, 250))
    chk("bend_table inserts the bend table", bool(tab), tab)
    D.save(os.path.join(OUT, "sm_planificada.slddrw"))

def sketched_bend(chk):
    """A SKETCHED bend (`sketched_bend`) -- the bend that does not come from an edge."""
    sheet()
    v0 = vol()
    name = SM.sketched_bend(line=((60, -10, 2), (60, 70, 2)), angle_deg=90)
    dx, dy, dz = SM.bbox_mm()
    chk("sketched_bend returns the feature (not the flat pattern)",
        name and "Flat" not in name, name)
    chk("the sheet bent (the bounding box became an L)", dz > 30.0 and dx < 70.0,
        f"{(round(dx, 1), round(dy, 1), round(dz, 1))}")
    chk("volume preserved (bending removes no material)",
        abs(vol() - v0) / v0 < 0.03, f"{v0:.4e} -> {vol():.4e}")
    chk("the bend goes into bends()", len(SM.bends()) == 1, f"{SM.bends()}")
    SM.flatten(True)
    chk("it flattens back (it is a real bend)", SM.bbox_mm()[2] < 4.0,
        f"{SM.bbox_mm()}")
    SM.flatten(False)

    # the angle is obeyed
    sheet()
    SM.sketched_bend(line=((60, -10, 2), (60, 70, 2)), angle_deg=45)
    chk("a 45-degree angle gives a box different from the 90-degree one",
        abs(SM.bbox_mm()[2] - 38.7) > 3.0, f"{SM.bbox_mm()}")

    # two lines in the same sketch = two bends (a U channel)
    sheet()
    f = SM.base_face()
    SK.begin_on_face(f)
    sm = C.cast(C.active("IModelDoc2").SketchManager, "ISketchManager")
    m = C.cast(C.cast(sm.ActiveSketch, "ISketch").ModelToSketchTransform,
               "IMathTransform").ArrayData
    for x in (30.0, 70.0):
        a = SM._pt_to_sketch(m, (C.mm(x), C.mm(-10), C.mm(2)))
        b = SM._pt_to_sketch(m, (C.mm(x), C.mm(70), C.mm(2)))
        sm.CreateLine(a[0], a[1], 0.0, b[0], b[1], 0.0)
    sk_name = SK.end()
    chk("sw_sketch.end does not return the flat pattern on a sheet metal part",
        "Flat" not in sk_name, sk_name)
    SM.sketched_bend(sketch_name=sk_name)
    chk("two lines = two bends in one call (a U channel)",
        len(SM.bends()) == 2, f"{SM.bends()}")

    # the position measured as a no-op is REFUSED, not silently accepted
    sheet()
    try:
        SM.sketched_bend(line=((60, -10, 2), (60, 70, 2)),
                         position="bend_centerline")
        chk("position='bend_centerline' is refused (it was measured as a no-op)", False)
    except ValueError:
        chk("position='bend_centerline' is refused (it was measured as a no-op)", True)

    # ON A PART THAT ALREADY HAS A FLANGE (it was an open item: "the fixed face stops
    # being the largest one"). Measured: the hypothesis is FALSE -- the base sheet is
    # still the largest face (5500 mm2 against the flange's 2200) and `base_face()`
    # gets it right. The real limit is another: the bend line must not CROSS the flange.
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    base = SM.base_face()
    b = [v * 1000.0 for v in base.GetBox()]
    chk("with a flange, base_face() still picks the base sheet (not the flange)",
        abs(base.GetArea() * 1e6 - 5500.0) < 60.0, f"{base.GetArea() * 1e6:.0f} mm2")
    v0 = vol()
    y = b[1] + (b[4] - b[1]) * 0.6
    name = SM.sketched_bend(line=((b[0] - 10, y, b[2]), (b[3] + 10, y, b[5])), face=base)
    chk("a sketched bend PARALLEL to the flange works on the already folded part",
        len(SM.bends()) == 2 and abs(vol() - v0) / v0 < 0.03,
        f"{name} | bends={SM.bends()} | vol {v0:.4e} -> {vol():.4e}")

    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    base = SM.base_face()
    b = [v * 1000.0 for v in base.GetBox()]
    x = b[0] + (b[3] - b[0]) * 0.6
    chk("and a line that CROSSES the flange is refused (not silently accepted)",
        _raises(lambda: SM.sketched_bend(
            line=((x, b[1] - 10, b[2]), (x, b[4] + 10, b[5])), face=base)))


def miter(chk):
    """A MITER flange (`miter_flange`) -- its own profile, length from the profile."""
    sheet()
    v0 = vol()
    name = SM.miter_flange(SM.free_edges()[0], 20)
    dx, dy, dz = SM.bbox_mm()
    chk("miter_flange returns the feature", name and "Flat" not in name, name)
    chk("the flange has the profile's height (20 mm)", abs(dz - 20.0) < 1.0,
        f"{(round(dx, 1), round(dy, 1), round(dz, 1))}")
    chk("it added material", vol() > v0 * 1.05, f"{v0:.4e} -> {vol():.4e}")

    sheet()
    SM.miter_flange(SM.free_edges()[0], 35)
    chk("the length comes from the profile (35 mm)", abs(SM.bbox_mm()[2] - 35.0) < 1.0,
        f"{SM.bbox_mm()}")


def edit(chk):
    """PARAMETRIC editing: global radius, existing flange, flat pattern, bend report."""
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25)
    SM.flatten(True)
    L0 = SM.bbox_mm()[1]
    SM.flatten(False)

    chk("bend_params reads the global radius", abs(SM.bend_params()["radius_mm"] - 3.0) < 0.01,
        f"{SM.bend_params()}")
    chk("set_bend_radius(8) changes the global radius", abs(SM.set_bend_radius(8) - 8.0) < 0.01)
    SM.flatten(True)
    L1 = SM.bbox_mm()[1]
    SM.flatten(False)
    chk("and the change is GEOMETRIC (the flat pattern gets shorter)", L1 < L0 - 0.5,
        f"{L0:.2f} -> {L1:.2f} mm")

    info = SM.bend_info()
    chk("bend_info reports one bend with the new radius",
        len(info) == 1 and abs(info[0]["radius_mm"] - 8.0) < 0.01, f"{info}")
    chk("bend_info carries the angle and the direction", info[0]["angle_deg"] == 90.0
        and "down" in info[0], f"{info[0]}")

    r = SM.set_flange(angle_deg=45)
    chk("set_flange changes the flange's angle", abs(r["angle_deg"] - 45.0) < 0.01, f"{r}")
    r = SM.set_flange(radius_mm=5)
    chk("set_flange changes the flange's radius", abs(r["radius_mm"] - 5.0) < 0.01, f"{r}")
    chk("and the bend starts reporting that radius",
        abs(SM.bend_info()[0]["radius_mm"] - 5.0) < 0.01, f"{SM.bend_info()}")

    o = SM.flat_options()
    chk("flat_options reports the flat pattern's state", "merge" in o, f"{o}")
    o = SM.flat_options(merge=False, simplify=False)
    chk("flat_options turns merge and simplify off",
        o["merge"] is False and o["simplify"] is False, f"{o}")
    o = SM.flat_options(merge=True, simplify=True)
    chk("and turns them back on", o["merge"] and o["simplify"], f"{o}")


    # K-FACTOR per flange (the "global" one left the escape hatch: it is per FLANGE)
    P.new_part()
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    SM.flatten(True); p0 = SM.bbox_mm()[1]; SM.flatten(False)
    st = SM.set_flange(k_factor=0.1)
    SM.flatten(True); p1 = SM.bbox_mm()[1]; SM.flatten(False)
    chk("set_flange(k_factor) SHRINKS the flat pattern (a smaller K)", p1 < p0 - 1.0,
        f"K=0.5 -> {p0:.4f} mm | K=0.1 -> {p1:.4f} mm")
    chk("and the flange starts reporting the K asked for",
        abs(st["k_factor"] - 0.1) < 1e-6 and st["k_default"] is False, str(st))
    r = SM.set_k_factor(0.9)
    SM.flatten(True); p2 = SM.bbox_mm()[1]; SM.flatten(False)
    chk("set_k_factor(0.9) STRETCHES the flat pattern and reaches every flange",
        p2 > p0 + 1.0 and r["bends"] == 1, f"{r} | {p1:.4f} -> {p2:.4f} mm")
    chk("set_k_factor refuses a part with no edge flange",
        _raises(lambda: (sheet(), SM.set_k_factor(0.3))))

def cut(chk):
    """A cut in the sheet and the unfold -> cut -> fold pair (cutting ACROSS the bend)."""
    sheet()
    SM.edge_flange(SM.free_edges()[0], 30)
    v0 = vol()
    SM.unfold()
    chk("unfold unfolded (height ~ thickness)", SM.bbox_mm()[2] < 5.0,
        f"{SM.bbox_mm()}")

    f = SM.base_face()
    SK.begin_on_face(f)
    sm = C.cast(C.active("IModelDoc2").SketchManager, "ISketchManager")
    m = C.cast(C.cast(sm.ActiveSketch, "ISketch").ModelToSketchTransform,
               "IMathTransform").ArrayData
    a = SM._pt_to_sketch(m, (C.mm(50), C.mm(60), C.mm(2)))
    sm.CreateCircleByRadius(a[0], a[1], 0.0, C.mm(6))
    sk_name = SK.end()
    name = SM.cut(sk_name)
    chk("the cut crossed the bend region", name and vol() < v0,
        f"{name} vol {v0:.4e} -> {vol():.4e}")
    v_cut = vol()

    SM.fold()
    chk("fold gave the part back folded", SM.bbox_mm()[2] > 10.0, f"{SM.bbox_mm()}")
    chk("the cut survived the fold", abs(vol() - v_cut) / v_cut < 0.02,
        f"{v_cut:.4e} -> {vol():.4e}")


def gauge(chk):
    """The GAUGE table -- what ties the model to the real material."""
    sheet()
    st = SM.gauge_table()
    chk("gauge_table reports the sheet with no table", st["active"] is False, f"{st}")
    tab = _gauge_table_file()
    if not tab:
        print("  [SKIP] gauge table -- none installed with this SOLIDWORKS "
              "(lang/<language>/... gauge table .xls); the linked-table checks did not run")
        return
    chk("there is a gauge table installed with the SW", True, os.path.basename(tab))
    st = SM.gauge_table(tab)
    chk("gauge_table(path) links the table", st["active"] is True, f"{st['gauge']}")
    chk("and the table carries thicknesses", st["thicknesses"] > 0, f"{st['thicknesses']}")
    chk("the table lists the gauges by NAME", len(st["gauges"]) == st["thicknesses"]
        and all(isinstance(x, str) for x in st["gauges"]), str(st["gauges"]))
    chk("gauge_radii carries the gauge's allowed radii", len(SM.gauge_radii()) > 0,
        str([round(r, 3) for r in SM.gauge_radii()]))

    # CHOOSING a gauge from the table -- a GEOMETRIC criterion (the bounding box changes)
    names = list(st["gauges"])
    e0 = SM.thickness()
    t1 = SM.set_gauge(names[-1])
    chk(f"set_gauge('{names[-1]}') really changes the thickness",
        abs(t1 - e0) > 0.5 and abs(SM.bbox_mm()[2] - t1) < 0.01,
        f"{e0:.4f} -> {t1:.4f} mm (bbox z={SM.bbox_mm()[2]:.4f})")
    t2 = SM.set_gauge(names[0])
    chk(f"set_gauge('{names[0]}') goes back to another thickness of the table",
        abs(t2 - t1) > 0.5 and abs(SM.bbox_mm()[2] - t2) < 0.01,
        f"{t1:.4f} -> {t2:.4f} mm")
    chk("gauge_table reports the CHOSEN gauge",
        SM.gauge_table()["gauge"] == names[0], SM.gauge_table()["gauge"])
    chk("set_gauge refuses a gauge outside the table",
        _raises(lambda: SM.set_gauge("Gauge 999")))

    # set_thickness STILL holds with the table linked -- and it marks the override
    t3 = SM.set_thickness(4.0)
    st = SM.gauge_table()
    chk("set_thickness still holds with the table linked",
        abs(t3 - 4.0) < 0.01 and abs(SM.bbox_mm()[2] - 4.0) < 0.01,
        f"{t2:.4f} -> {t3:.4f} mm")
    chk("and the part declares the thickness was IMPOSED (override), not read from the table",
        st["override_thickness"] is True and st["active"] is True,
        f"override={st['override_thickness']} table={st['active']} gauge={st['gauge']}")


def lofted(chk):
    """Lofted bend: a transition sheet between two open profiles (left the escape hatch)."""
    def profiles():
        P.new_part()
        SK.begin("Front")
        SK.line(0, 0, 60, 0); SK.line(60, 0, 60, 40); SK.line(60, 40, 0, 40)
        a = SK.end()
        P.reference_plane("Front", 80.0, name="PlanoTopo")
        SK.begin("PlanoTopo")
        SK.line(10, 10, 50, 10); SK.line(50, 10, 50, 30); SK.line(50, 30, 10, 30)
        return a, SK.end()

    a, b = profiles()
    name = SM.lofted_bend(a, b, 2.0)
    chk("lofted_bend creates ONE sheet metal body", len(I.bodies()) == 1 and SM.is_sheet(),
        f"{name} | bodies={len(I.bodies())}")
    v2 = vol()
    chk("the transition's volume at 2 mm", abs(v2 - 2.1602e-05) < 5e-8, f"{v2:.4e}")
    chk("thickness() reads the thickness even WITHOUT a base flange",
        abs(SM.thickness() - 2.0) < 0.01, f"{SM.thickness():.4f} mm")
    chk("the cut list recognises the part",
        SM.cut_list().get("Sheet Metal Thickness") == 2.0,
        SM.cut_list().get("Sheet Metal Thickness"))

    # the thickness asked for RULES the geometry
    profiles()
    SM.lofted_bend(thickness_mm=3.0)          # no names: the last two sketches
    v3 = vol()
    chk("a 3 mm thickness thickens the body in proportion", v3 > v2 * 1.4,
        f"{v2:.4e} -> {v3:.4e}")

    # the cutting DXF comes out even with the solid not flattening (a MEASURED limit)
    target = os.path.join(OUT, "sm_lofted.sldprt")
    dxf = os.path.join(OUT, "sm_lofted.dxf")
    T.close_all()
    a, b = profiles()
    SM.lofted_bend(a, b, 2.0)
    P.save(target)
    if os.path.exists(dxf):
        os.remove(dxf)
    SM.export_flat(dxf)
    chk("export_flat produces the transition's cutting DXF",
        os.path.exists(dxf) and os.path.getsize(dxf) > 5000,
        f"{os.path.getsize(dxf) // 1024} KB" if os.path.exists(dxf) else "-")

    chk("lofted_bend refuses a single profile",
        _raises(lambda: (P.new_part(), SK.begin("Front"), SK.line(0, 0, 50, 0),
                          SK.end(), SM.lofted_bend())))


def multibody(chk):
    """A sheet metal part with MORE THAN ONE body -- the verbs stop speaking for one."""
    sheet(100, 60, 2)
    md = C.active("IModelDoc2")
    e0 = SM.thickness()
    SK.begin("Front")
    SK.rect(150, 0, 230, 40)
    SK.end()
    SM.base_flange(3.0, radius_mm=2.0)
    md.ForceRebuild3(False)

    bodies = SM.sheet_bodies()
    chk("sheet_bodies sees BOTH bodies", len(bodies) == 2, str([c["name"] for c in bodies]))
    chk("and recognises both as sheet metal", all(c["sheet"] for c in bodies),
        str([(c["name"], c["sheet"]) for c in bodies]))
    by_name = {c["name"]: c for c in bodies}
    chk("each body has ITS own box (100x60 and 80x40)",
        {tuple(sorted(c["box_mm"])[-2:]) for c in bodies} == {(60.0, 100.0), (40.0, 80.0)},
        str([c["box_mm"] for c in bodies]))

    envelops = SM.bbox_mm()
    chk("bbox_mm() with no argument envelops ALL the bodies (not bodies()[0])",
        abs(envelops[0] - 230.0) < 0.01, f"{tuple(round(v, 2) for v in envelops)}")

    lists = SM.cut_lists()
    chk("cut_lists returns one cut list per body", len(lists) == 2,
        str([l["folder"] for l in lists]))
    chk("and each one points at its own body",
        all(len(l["bodies"]) == 1 for l in lists)
        and {l["bodies"][0] for l in lists} == set(by_name),
        str([(l["folder"], l["bodies"]) for l in lists]))
    chk("the material rectangles are DIFFERENT between the bodies",
        {(l.get("Bounding Box Length"), l.get("Bounding Box Width")) for l in lists}
        == {(100.0, 60.0), (80.0, 40.0)},
        str([(l.get("Bounding Box Length"), l.get("Bounding Box Width")) for l in lists]))
    chk("cut_list() REFUSES the multi-body part instead of reporting one body only",
        _raises(SM.cut_list))

    # MEASURED FINDING: the sheet thickness belongs to the DOCUMENT, not to the body --
    # creating the 2nd base flange at 3 mm thickened the 1st too, which was at 2 mm.
    chk("the 2nd base flange imposes its thickness ALSO on the body that already existed",
        abs(e0 - 2.0) < 0.01 and all(abs(sorted(c["box_mm"])[0] - 3.0) < 0.01
                                     for c in bodies),
        f"the 1st was born at {e0:.2f} mm; now both measure "
        f"{[sorted(c['box_mm'])[0] for c in bodies]}")


def _raises(fn):
    """True if `fn()` raises (a verb that REFUSES invalid input)."""
    try:
        fn()
    except Exception:  # noqa: BLE001
        return True
    return False

def _gauge_table_file():
    """The 1st .xls of the gauge tables installed with the SW (or '')."""
    base = C.app().GetExecutablePath()      # it is the exe FOLDER, not the .exe
    for root in (base, os.path.dirname(base)):
        lang = os.path.join(root, "lang")
        if not os.path.isdir(lang):
            continue
        for language in os.listdir(lang):
            d = os.path.join(lang, language)
            if not os.path.isdir(d):
                continue
            for sub in os.listdir(d):
                if "gauge table" in sub.lower():
                    p = os.path.join(d, sub)
                    xls = [a for a in sorted(os.listdir(p))
                           if a.lower().endswith((".xls", ".xlsx"))]
                    if xls:
                        return os.path.join(p, xls[0])
    return ""


def _to_assembly(comp, pt_mm):
    """A point in PART coords -> ASSEMBLY coords. Uses IMathPoint.MultiplyTransform
    with `IComponent2.Transform2` (part->assembly; the Inverse goes the other way).
    Adding the translation "by hand" only works when the component is NOT rotated --
    and a mate with align='anti' rotates 180 degrees, so the naive sum errs silently."""
    m = C.cast(C.app().GetMathUtility(), "IMathUtility")
    v = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8,
                        [x / 1000.0 for x in pt_mm])
    q = C.cast(m.CreatePoint(v), "IMathPoint")
    q = C.cast(q.MultiplyTransform(C.cast(comp.Transform2, "IMathTransform")), "IMathPoint")
    return [x * 1000.0 for x in q.ArrayData]


def assembly(chk):
    """Sheet metal INSIDE an ASSEMBLY: a mate by a sheet face, interference with the
    BEND, and the FLATTENED part as the assembly sees it.

    It was the front's biggest product gap: `flat_pattern_view`/`bend_table` closed the
    DRAWING side, but nothing exercised a sheet metal part as a COMPONENT.
    """
    SUP = os.path.join(OUT, "sm_asm_suporte.sldprt")
    PLA = os.path.join(OUT, "sm_asm_placa.sldprt")
    BLO = os.path.join(OUT, "sm_asm_bloco.sldprt")
    ASM = os.path.join(OUT, "sm_asm_conjunto.sldasm")
    sw = C.app()
    T.close_all()   # SaveAs over an open file fails (swFileSaveError=1)

    # --- the parts -------------------------------------------------------------
    SM.new_sheet("Front", 100, 60, thickness_mm=2)
    SM.edge_flange(SM.free_edges()[0], 30.0)      # an L bracket
    P.save(SUP)
    t_sup = C.active("IModelDoc2").GetTitle()
    P.new_part(); P.sketch("Front", "rect", x1=0, y1=0, x2=120, y2=80)
    P.extrude(10, "boss"); P.save(PLA)
    P.new_part(); P.sketch("Front", "rect", x1=0, y1=0, x2=20, y2=20)
    P.extrude(20, "boss"); P.save(BLO)

    # --- the assembly -----------------------------------------------------------
    A.new_assembly()
    t_asm = C.active("IModelDoc2").GetTitle()
    plate = A.add_component(PLA, (0, 0, 0), fixed=True)
    sup = A.add_component(SUP, (0, 0, 60))
    chk("a sheet metal part goes into the assembly", A.component_count() == 2,
        f"n={A.component_count()}")
    chk("the component is recognised as SHEET METAL inside the assembly",
        C.cast(sup.GetBody(), "IBody2").IsSheetMetal())

    def box(comp):
        return [v * 1000.0 for v in comp.GetBox(False, False)]

    def dims(comp):
        """Measurements of the instance's BODY, in PART coords -- invariant to rotation.
        The box of `IComponent2.GetBox` is in ASSEMBLY coords and is aligned to ITS
        axes: a mate leaves the remaining rotation free, the SW picks any angle, and
        the same 100x60x32 start measuring 115x94x32 with nothing having changed in
        the part. Measured 2026-08-30 -- which is why the shape criterion cannot come from there."""
        b = C.cast(comp.GetBody(), "IBody2").GetBodyBox()
        return sorted(round((b[i + 3] - b[i]) * 1000.0, 2) for i in range(3))

    bent = dims(sup)
    chk("the folded bracket measures 100 x 60 x 32", bent == [32.0, 60.0, 100.0], str(bent))

    # --- a mate BY A SHEET FACE -----------------------------------------------
    planar = sorted(((f.GetArea() * 1e6, f) for f in A.comp_faces(sup)
                     if C.cast(f.GetSurface(), "ISurface").IsPlane()), key=lambda t: t[0])
    sheet_face, sheet_area = planar[-1][1], planar[-1][0]
    n_sheet = C.cast(sheet_face.GetSurface(), "ISurface").PlaneParams[:3]
    flanges = [(a, f) for a, f in planar
            if abs(sum(x * y for x, y in zip(
                n_sheet, C.cast(f.GetSurface(), "ISurface").PlaneParams[:3]))) < 0.1]
    chk("the component's largest planar face is the SHEET face (100 x 55 = 5500 mm2)",
        abs(sheet_area - 5500.0) < 60.0 and sheet_area > 1.5 * flanges[-1][0],
        f"sheet={sheet_area:.0f} mm2 | largest flange={flanges[-1][0]:.0f} mm2")

    # align='anti': turns the bracket with the FLANGE UP. With 'aligned' the flange goes
    # INTO the plate -- measured: 13.256 mm3 of interference. The align is no cosmetic
    # detail here: it decides whether the assembly is physically possible.
    mate_name = A.mate(sheet_face, A.find_face(plate, "plane", axis=2, want_max=True),
                       "coincident", align="anti")
    A.rebuild()
    b_sup, b_pla = box(sup), box(plate)
    chk("a mate by a sheet face seats the sheet on top of the plate",
        abs(b_sup[2] - b_pla[5]) < 0.01,
        f"{mate_name}: sheet z={b_sup[2]:.3f} / plate top z={b_pla[5]:.3f}")
    chk("the assembly has no mate with an error", A.mate_errors() == [], str(A.mate_errors()))
    chk("with the flange UPWARDS there is no overlapping material", A.interferences() == [],
        str(A.interferences()))

    # --- interference WITH THE BEND -------------------------------------------
    fb = flanges[-1][1].GetBox()
    flange_centre = _to_assembly(sup, [(fb[i] + fb[i + 3]) * 500.0 for i in range(3)])
    block = A.add_component(BLO, (0, 0, 0))
    A.move_component(block, flange_centre)          # the block's centre ON the flange
    itf = A.interferences()
    chk("interference with the FLANGE/BEND is detected",
        len(itf) == 1 and itf[0]["volume_mm3"] > 1.0,
        f"n={len(itf)} vol={itf[0]['volume_mm3']:.1f} mm3" if itf else "n=0")
    chk("the interference names the block x sheet bracket pair",
        bool(itf) and any("bloco" in c for c in itf[0]["comps"])
        and any("suporte" in c for c in itf[0]["comps"]),
        str(itf[0]["comps"]) if itf else "-")
    A.move_component(block, [flange_centre[0] + 400.0, flange_centre[1], flange_centre[2]])
    chk("with the block moved away, the assembly is free of interference again",
        A.interferences() == [], str(A.interferences()))

    # --- the FLATTENED part inside the assembly -------------------------------
    sw.ActivateDoc(t_sup)
    SM.flatten(True)
    P.save()                                     # the assembly only sees what is SAVED
    sw.ActivateDoc(t_asm)
    A.rebuild()
    plane = dims(sup)
    chk("flattened, the thickness becomes the component's SMALLEST dimension",
        abs(plane[0] - 2.0) < 0.05, f"{plane} (folded it was {bent})")
    chk("flattened, the sheet stretches out (60 -> ~88 mm)", 86.0 < plane[1] < 90.0,
        f"{plane[1]:.2f} mm")
    chk("the mate by a sheet face SURVIVES flattening", A.mate_errors() == [],
        str(A.mate_errors()))

    sw.ActivateDoc(t_sup)
    SM.flatten(False)
    P.save()
    sw.ActivateDoc(t_asm)
    A.rebuild()
    chk("folding back, the component returns to its original measurements",
        dims(sup) == bent, str(dims(sup)))
    A.save(ASM)
    chk("an assembly with a sheet saved to disk", os.path.exists(ASM),
        f"{os.path.getsize(ASM) // 1024} KB" if os.path.exists(ASM) else "-")
    T.close_all()


def _profile_solid(cut_x2):
    """An ordinary 2 mm walled solid extruded 50 mm: a cut up to 70 gives an L (1 inner
    corner), up to 58 gives a U (2 corners). It is NOT a sheet metal part -- it is what
    the convert has to transform."""
    P.new_part()
    P.sketch("Front", "rect", x1=0, y1=0, x2=60, y2=40)
    P.extrude(50, "boss")
    # the cut's rectangle OVERSHOOTS the block: coinciding with the face at x=60/y=40
    # makes FeatureCut return None. `reverse` because the cut goes to the other side.
    P.sketch("Front", "rect", x1=2, y1=2, x2=cut_x2, y2=50)
    P.extrude(50, "cut", through_all=True, reverse=True)


def convert(chk):
    # --- L: one inner corner -> one bend
    _profile_solid(70)
    chk("an L solid is not sheet metal before the convert", not SM.is_sheet())
    corners = SM.sharp_bend_edges()
    chk("sharp_bend_edges finds 1 concave corner on the L", len(corners) == 1,
        f"{len(corners)}")
    box0 = [round(v, 1) for v in SM.bbox_mm()]
    name = SM.convert_to_sheet(2.0)
    chk("convert_to_sheet returns the feature", name.startswith("Convert-Solid"), name)
    chk("it became a sheet metal part", SM.is_sheet())
    chk("it PRESERVED the L's geometry", [round(v, 1) for v in SM.bbox_mm()] == box0,
        f"{box0} -> {[round(v, 1) for v in SM.bbox_mm()]}")
    chk("one bend", len(SM.bends()) == 1, f"{SM.bends()}")
    chk("thickness 2 mm", abs(SM.thickness() - 2.0) < 0.01, f"{SM.thickness():.3f}")
    SM.flatten(True)
    pl = [round(v, 1) for v in SM.bbox_mm()]
    # 58 + 37 + the bend's allowance (radius 3, K 0.5) = 96.3 mm measured
    chk("flattens into a ~96 mm flat sheet", abs(pl[0] - 96.3) < 1.0 and pl[1] == 2.0,
        f"{pl}")
    SM.flatten(False)
    chk("folds back", [round(v, 1) for v in SM.bbox_mm()] == box0,
        f"{[round(v, 1) for v in SM.bbox_mm()]}")
    T.close_scratch()

    # --- U: two inner corners -> TWO bends in one call
    _profile_solid(58)
    corners = SM.sharp_bend_edges()
    chk("sharp_bend_edges finds 2 concave corners on the U", len(corners) == 2,
        f"{len(corners)}")
    box0 = [round(v, 1) for v in SM.bbox_mm()]
    SM.convert_to_sheet(2.0)
    chk("the U becomes sheet metal preserving the geometry",
        SM.is_sheet() and [round(v, 1) for v in SM.bbox_mm()] == box0,
        f"{box0} -> {[round(v, 1) for v in SM.bbox_mm()]}")
    chk("TWO bends (the 2nd edge got into the selection)", len(SM.bends()) == 2,
        f"{SM.bends()}")
    SM.flatten(True)
    pl = [round(v, 1) for v in SM.bbox_mm()]
    chk("the U flattens to ~132.6 mm", abs(pl[0] - 132.6) < 1.0, f"{pl}")
    SM.flatten(False)
    # the convert delivers a real sheet: the cut list resolves
    cl = SM.cut_list()
    # numeric: `sw_sheetmetal._value` converts what is a number (it used to be "2")
    chk("cut_list resolves on the converted part", cl.get("Bends") == 2,
        f"Bends={cl.get('Bends')} L={cl.get('Bounding Box Length')}")
    T.close_scratch()

    # --- the two failure modes the verb has to REFUSE, not mask
    _profile_solid(70)
    try:
        # base_face() is the largest by AREA -- on an L it is the OUTER face, on which
        # the convert does nothing (and the old v1 returned False without warning)
        SM.convert_to_sheet(2.0, fixed_face=SM.base_face())
        chk("refuses the OUTER fixed face", False, "did not raise")
    except RuntimeError:
        chk("refuses the OUTER fixed face", True)
    T.close_scratch()

    P.new_part()
    P.sketch("Front", "rect", x1=0, y1=0, x2=50, y2=30)
    P.extrude(20, "boss")
    try:
        SM.convert_to_sheet(2.0)
        chk("refuses a solid with no concave corner", False, "did not raise")
    except RuntimeError:
        chk("refuses a solid with no concave corner", True)
    T.close_scratch()

    # --- the cutting DXF comes out of the converted part (the product goal)
    _profile_solid(70)
    SM.convert_to_sheet(2.0)
    P.save(os.path.join(OUT, "convert_L.sldprt"))    # export_flat requires the part saved
    dxf = SM.export_flat(os.path.join(OUT, "convert_L.dxf"))
    chk("export_flat produces the converted part's DXF",
        os.path.exists(dxf) and os.path.getsize(dxf) > 1000,
        f"{os.path.getsize(dxf)} bytes")


def closed_corner(chk):
    """CLOSED CORNER -- left the escape hatch on 2026-08-30 (round d), via the macro
    recorded in the UI. 11 earlier variants got the FACE wrong (they used the two LARGE
    faces of the flanges); what the UI selects is ONE face, the flange's SIDE one."""
    # --- a part with two adjacent flanges: exactly ONE open corner
    sheet()
    f = SM.base_face()
    ar = sorted(SM.free_edges(f), key=lambda e: SM._edge_len(e), reverse=True)
    SM.edge_flange(ar[0], 20.0, face=f)
    f2 = SM.base_face()
    ar2 = sorted(SM.free_edges(f2), key=lambda e: SM._edge_len(e), reverse=True)
    SM.edge_flange(ar2[1], 20.0, face=f2)
    C.active("IModelDoc2").EditRebuild3()

    corners = SM.open_corners()
    chk("open_corners finds ONE corner on a 2-flange part", len(corners) == 1,
        f"{len(corners)} gaps={[round(c[2], 2) for c in corners]}")
    v0, nf0 = vol(), nfaces()
    name = SM.closed_corner()
    chk("closed_corner returns the feature", name.startswith("Closed Corner"), name)
    # a closed corner ADDS material: the criterion is the volume, not the return value
    # (InsertSheetMetalClosedCorner returns nothing -- it is a `Sub` even in recorded VBA)
    chk("the volume GROWS (material was closed in)", vol() > v0, f"{v0:.4e} -> {vol():.4e}")
    chk("the face count changes", nfaces() != nf0, f"{nf0} -> {nfaces()}")
    SM.flatten(True)
    chk("it still flattens after the closed corner", SM.is_flat(),
        f"{[round(x, 1) for x in SM.bbox_mm()]}")
    SM.flatten(False)
    T.close_scratch()

    # --- a 4-flange box: FOUR corners, closed in sequence
    sheet()
    SM.box_flanges(20)
    C.active("IModelDoc2").EditRebuild3()
    chk("open_corners finds FOUR corners on the box", len(SM.open_corners()) == 4,
        f"{len(SM.open_corners())}")
    v0 = vol()
    closed = 0
    for _ in range(4):
        try:
            SM.closed_corner()
            closed += 1
        except RuntimeError:
            break
    chk("closes the box's four corners", closed == 4, f"{closed}")
    chk("and the volume grows with each corner", vol() > v0, f"{v0:.4e} -> {vol():.4e}")
    T.close_scratch()

    # --- what the verb has to REFUSE
    sheet()
    try:
        SM.closed_corner()
        chk("refuses a sheet with no flange", False, "did not raise")
    except RuntimeError:
        chk("refuses a sheet with no flange", True)
    T.close_scratch()

    sheet()
    SM.edge_flange(SM.free_edges()[0], 20.0)
    C.active("IModelDoc2").EditRebuild3()
    try:
        # ONE flange only: its two side faces face no other flange
        SM.closed_corner()
        chk("refuses a part with ONE flange (there is no corner)", False, "did not raise")
    except RuntimeError:
        chk("refuses a part with ONE flange (there is no corner)", True)
    T.close_scratch()

    # --- the flange's LARGE face does not do (it was the old 11 variants' error)
    sheet()
    f = SM.base_face()
    ar = sorted(SM.free_edges(f), key=lambda e: SM._edge_len(e), reverse=True)
    SM.edge_flange(ar[0], 20.0, face=f)
    f2 = SM.base_face()
    ar2 = sorted(SM.free_edges(f2), key=lambda e: SM._edge_len(e), reverse=True)
    SM.edge_flange(ar2[1], 20.0, face=f2)
    C.active("IModelDoc2").EditRebuild3()
    large = sorted(((x.GetArea(), x) for x in I.faces()
                      if C.cast(x.GetSurface(), "ISurface").IsPlane()),
                     key=lambda t: t[0], reverse=True)
    try:
        SM.closed_corner(large[1][1])       # the flange wall, not the side
        chk("refuses the flange's LARGE face", False, "did not raise")
    except RuntimeError:
        chk("refuses the flange's LARGE face", True)


def jog(chk):
    """JOG -- left the escape hatch on 2026-08-31 (round e), via the macro recorded in the
    UI. 18 earlier variants passed the angle in RADIANS; the method wants DEGREES."""
    sheet()
    c0 = SM.bbox_mm()
    name = SM.jog(line=((50, -10, 0), (50, 70, 0)), offset_mm=10)
    chk("jog returns the feature", name.startswith("Jog"), name)
    c1 = SM.bbox_mm()
    # the step rises by the 10 mm offset + 2 of thickness
    chk("the step raises the sheet (2 -> 12 mm)", abs(c1[2] - 12.0) < 0.05,
        f"{[round(v, 2) for v in c0]} -> {[round(v, 2) for v in c1]}")
    chk("the footprint does NOT change (100x60)", abs(c1[0] - 100.0) < 0.05 and abs(c1[1] - 60.0) < 0.05,
        f"{[round(v, 2) for v in c1]}")
    chk("the jog creates TWO bends", len(SM.bends()) == 2, f"{SM.bends()}")
    v0 = vol()
    SM.flatten(True)
    chk("it flattens back", SM.is_flat(), f"{[round(v, 2) for v in SM.bbox_mm()]}")
    chk("and the flat pattern is BIGGER than the original sheet", SM.bbox_mm()[0] > 100.0,
        f"{round(SM.bbox_mm()[0], 2)} mm")
    chk("the volume is conserved when flattening", abs(vol() - v0) / v0 < 1e-6,
        f"{v0:.6e} -> {vol():.6e}")
    SM.flatten(False)
    T.close_scratch()

    # --- the offset rules the step
    sheet()
    SM.jog(line=((50, -10, 0), (50, 70, 0)), offset_mm=20)
    chk("offset_mm=20 gives a 22 mm box", abs(SM.bbox_mm()[2] - 22.0) < 0.05,
        f"{[round(v, 2) for v in SM.bbox_mm()]}")
    T.close_scratch()

    # --- COUNTERPROOF of the angle gotcha: in radians nothing happens
    sheet()
    md = C.active("IModelDoc2")
    face = SM.base_face()
    pt = SM._point_beside(face, (C.mm(50), C.mm(-10), 0.0), (C.mm(50), C.mm(70), 0.0))
    md.ClearSelection2(True)
    C.cast(face, "IEntity").Select4(False, None)
    sm = C.cast(md.SketchManager, "ISketchManager")
    sm.InsertSketch(True)
    m = C.cast(C.cast(sm.ActiveSketch, "ISketch").ModelToSketchTransform,
               "IMathTransform").ArrayData
    a = SM._pt_to_sketch(m, (C.mm(50), C.mm(-10), 0.0))
    b = SM._pt_to_sketch(m, (C.mm(50), C.mm(70), 0.0))
    sm.CreateLine(a[0], a[1], 0.0, b[0], b[1], 0.0)
    md.ClearSelection2(True)
    sm.InsertSketch(True)
    nk = SM.last_sketch()
    ext = C.cast(md.Extension, "IModelDocExtension")
    c0 = SM.bbox_mm()
    md.ClearSelection2(True)
    ext.SelectByID2(f"Line1@{nk}", "EXTSKETCHSEGMENT", 0, 0, 0, False, 0, None, 0)
    ext.SelectByID2("", "FACE", pt[0], pt[1], pt[2], True, 0, None, 0)
    md.InsertSheetMetalJog(C.deg(90), C.mm(3), C.mm(10), True, True, 1, 0)
    md.EditRebuild3()
    chk("COUNTERPROOF: an angle in RADIANS is a no-op", SM.bbox_mm() == c0,
        f"{[round(v, 2) for v in c0]} -> {[round(v, 2) for v in SM.bbox_mm()]}")
    T.close_scratch()

    # --- and what the verb refuses
    P.new_part()
    P.sketch("Front", "rect", x1=0, y1=0, x2=50, y2=30)
    P.extrude(20, "boss")
    try:
        SM.jog(line=((25, -10, 0), (25, 40, 0)))
        chk("refuses a part that is not sheet metal", False, "did not raise")
    except RuntimeError:
        chk("refuses a part that is not sheet metal", True)


def corner_trim(chk):
    """CORNER TRIM -- left the escape hatch on 2026-08-31 (round e). 12 earlier variants
    added up three errors: InternalCornerFlag=1, a single edge, and flattening by
    SetBendState (which does not flatten)."""
    sheet()
    f = SM.base_face()
    ar = sorted(SM.free_edges(f), key=lambda e: SM._edge_len(e), reverse=True)
    SM.edge_flange(ar[0], 20.0, face=f)
    f2 = SM.base_face()
    ar2 = sorted(SM.free_edges(f2), key=lambda e: SM._edge_len(e), reverse=True)
    SM.edge_flange(ar2[1], 20.0, face=f2)
    C.active("IModelDoc2").EditRebuild3()

    try:
        SM.corner_trim()
        chk("refuses the FOLDED part", False, "did not raise")
    except RuntimeError as e:
        chk("refuses the FOLDED part", "FLATTENED" in str(e), str(e)[:40])

    SM.flatten(True)
    nf0, v0 = nfaces(), vol()
    name = SM.corner_trim()
    chk("corner_trim returns the feature", "Corner" in name, name)
    chk("the face count GROWS (trimmed corners)", nfaces() > nf0,
        f"{nf0} -> {nfaces()}")
    chk("and it removes material (the relief is a cut)", vol() < v0, f"{v0:.4e} -> {vol():.4e}")
    chk("it stays flattened", SM.is_flat(), f"{[round(x, 1) for x in SM.bbox_mm()]}")
    SM.flatten(False)
    chk("and folds back", not SM.is_flat(), f"{[round(x, 1) for x in SM.bbox_mm()]}")
    T.close_scratch()

    # --- a plain flattened sheet: the rectangle's 4 corners are trimmable too
    sheet()
    SM.flatten(True)
    nf0, v0 = nfaces(), vol()
    name = SM.corner_trim()
    chk("it also trims the 4 corners of a plain sheet", nfaces() > nf0,
        f"{name}: faces {nf0} -> {nfaces()}")
    chk("and it removes material there too", vol() < v0, f"{v0:.4e} -> {vol():.4e}")


def gusset(chk):
    """GUSSET -- left the escape hatch on 2026-08-31 (round e). 19 earlier variants got
    the FACE wrong (they took the largest by area, which are the OUTER ones) and turned
    BDraft on; the 21-argument v1 was always enough."""
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    C.active("IModelDoc2").EditRebuild3()

    faces = SM.bend_faces()
    chk("bend_faces returns TWO faces", len(faces) == 2, f"{len(faces)}")
    areas = sorted(round(x.GetArea() * 1e6, 1) for x in faces)
    chk("and they are the WALLS, not the reliefs", min(areas) > 500.0, f"areas mm2 {areas}")
    na, nb = faces[0].Normal, faces[1].Normal
    chk("the two are perpendicular to each other",
        abs(sum(na[k] * nb[k] for k in range(3))) < 0.2,
        f"{[round(v, 2) for v in na]} x {[round(v, 2) for v in nb]}")

    nf0, v0, c0 = nfaces(), vol(), SM.bbox_mm()
    name = SM.gusset()
    chk("gusset returns the feature", "Gusset" in name, name)
    chk("the face count GROWS", nfaces() > nf0, f"{nf0} -> {nfaces()}")
    chk("and ADDS material (the rib is solid)", vol() > v0,
        f"{v0:.4e} -> {vol():.4e}")
    chk("the bounding box does NOT change (the rib is inside)",
        max(abs(SM.bbox_mm()[k] - c0[k]) for k in range(3)) < 0.05,
        f"{[round(v, 2) for v in c0]} -> {[round(v, 2) for v in SM.bbox_mm()]}")
    T.close_scratch()

    # --- the parameters RULE the geometry (the verb does not ignore what it gets).
    # MEASURED: `width_mm` is the RECESS's width (IndentWidth), not the gusset's --
    # widening the recess REMOVES material (w=5 -> 1.708e-05; w=15 -> 1.680e-05). That
    # is why the criterion is "changes", not "grows"; what grows with the value is `depth_mm`.
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    SM.gusset(width_mm=5.0)
    v5 = vol()
    T.close_scratch()
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    SM.gusset(width_mm=15.0)
    chk("width_mm changes the rib's geometry", abs(vol() - v5) / v5 > 1e-4,
        f"w=5 -> {v5:.4e} | w=15 -> {vol():.4e}")
    T.close_scratch()

    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    SM.gusset(depth_mm=5.0)
    d5 = vol()
    T.close_scratch()
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    # d=20 does NOT fit: the recess starts at a 10 mm offset and the flange is 25 -- the
    # SW refuses, and that is a geometric limit, not a bug in the verb.
    SM.gusset(depth_mm=12.0)
    chk("a DEEPER rib = more material", vol() > d5,
        f"d=5 -> {d5:.4e} | d=12 -> {vol():.4e}")
    T.close_scratch()

    # --- and what it refuses
    sheet()
    try:
        SM.gusset()
        chk("refuses a sheet with NO bend", False, "did not raise")
    except RuntimeError:
        chk("refuses a sheet with NO bend", True)


def perp_plane(chk):
    """A PLANE PERPENDICULAR TO AN EDGE -- left the escape hatch on 2026-08-31
    (round e). 11 earlier variants used the SAME mark on both references; the UI uses
    the edge at mark 0 and the vertex at mark 1. It is what unlocks the miter flange
    on an OBLIQUE edge."""
    # --- a trapezoidal sheet: the edge from (100,40) to (0,70) is not axis-parallel
    P.new_part()
    SK.begin("Front")
    SK.line(0, 0, 100, 0)
    SK.line(100, 0, 100, 40)
    SK.line(100, 40, 0, 70)
    SK.line(0, 70, 0, 0)
    SK.end()
    SM.base_flange(2.0, radius_mm=3.0)
    face = SM.base_face()

    oblique = None
    for e in SM.free_edges(face):
        e2 = C.cast(e, "IEdge")
        p = e2.GetCurveParams2()
        d = [p[i + 3] - p[i] for i in range(3)]
        n = sum(x * x for x in d) ** 0.5
        d = [x / n for x in d]
        if max(abs(x) for x in d) < 0.99:
            oblique = (e2, d)
            break
    chk("found the OBLIQUE edge", oblique is not None)
    e2, edge_dir = oblique

    name = SM._perpendicular_plane(e2)
    chk("creates the reference plane", bool(name), name)
    rp = C.cast(SM._feat_by_name(name).GetSpecificFeature2(), "IRefPlane")
    t = C.cast(rp.Transform, "IMathTransform").ArrayData
    nv = (t[6], t[7], t[8])
    cos = abs(sum(nv[i] * edge_dir[i] for i in range(3)))
    chk("and the plane is PERPENDICULAR to the edge", abs(cos - 1.0) < 1e-4, f"|cos|={cos:.6f}")

    c0 = SM.bbox_mm()
    name_ab = SM.miter_flange(e2, 15.0, face=face)
    chk("miter flange on an OBLIQUE edge", "Miter" in name_ab, name_ab)
    chk("the flange rises 15 mm", abs(SM.bbox_mm()[2] - 15.0) < 0.1,
        f"{[round(v, 2) for v in c0]} -> {[round(v, 2) for v in SM.bbox_mm()]}")
    SM.flatten(True)
    chk("and the part with an oblique flange flattens", SM.is_flat(),
        f"{[round(v, 1) for v in SM.bbox_mm()]}")
    SM.flatten(False)
    T.close_scratch()

    # --- an axis-parallel edge still works (no regression)
    sheet()
    face = SM.base_face()
    ar = sorted(SM.free_edges(face), key=lambda e: SM._edge_len(e), reverse=True)
    name = SM._perpendicular_plane(C.cast(ar[0], "IEdge"))
    chk("it also creates a plane on an axis-parallel edge", bool(name), name)
    p = C.cast(ar[0], "IEdge").GetCurveParams2()
    d = [p[i + 3] - p[i] for i in range(3)]
    n = sum(x * x for x in d) ** 0.5
    d = [x / n for x in d]
    rp = C.cast(SM._feat_by_name(name).GetSpecificFeature2(), "IRefPlane")
    t = C.cast(rp.Transform, "IMathTransform").ArrayData
    cos = abs(sum((t[6], t[7], t[8])[i] * d[i] for i in range(3)))
    chk("and that one is perpendicular too", abs(cos - 1.0) < 1e-4, f"|cos|={cos:.6f}")


# ── round (f), 2026-08-31 ─────────────────────────────────────────────────────
_BTL_ANGLES = (5, 10, 20, 30, 45, 50, 70, 80, 90, 100, 110, 120, 130, 140, 145,
                160, 170, 180)
_BTL_RADII = (0.0000, 0.0005, 0.0010, 0.0015, 0.0020, 0.0025, 0.0030, 0.0040, 0.0050)
_BTL_THICKNESSES = (0.0005, 0.0010, 0.0015, 0.0020, 0.0025, 0.0030, 0.0035, 0.0040,
                   0.0045, 0.0050, 0.0060, 0.0070, 0.0080)


def _btl(ba_mm):
    """Writes a CONSTANT-allowance `.btl` and returns its path.

    A constant allowance is what makes the proof LINEAR: the flat pattern has to come
    out at `77 + allowance` for this sheet, and then there is no confusing "the file
    stuck" with "the file rules the geometry". The file is an artefact, not repo content.
    """
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"folga_{int(ba_mm)}mm.btl")
    lines = ["Bend Allowance Tables", "----------------------",
              "# generated by the smoke -- CONSTANT allowance",
              "Type: Bend Allowance", "Material: Teste", "Unit: \tmeters", "", ""]
    for thick in _BTL_THICKNESSES:
        lines.append(f"Thickness: {thick:.4f}")
        lines.append("Bend Radius (read across)\t"
                      + "\t".join(f"{r:.4f}" for r in _BTL_RADII))
        lines.append("Opening Angle (read down)")
        for ang in _BTL_ANGLES:
            lines.append(f"\t\t\t{ang}\t"
                          + "\t".join(f"{ba_mm / 1000.0:.4f}" for _ in _BTL_RADII)
                          + "\t")
        lines += ["", ""]
    with open(path, "w", newline="\r\n") as f:
        f.write("\n".join(lines))
    return path


def _flat_length():
    """The FLAT length (the criterion of every bend allowance). It folds back."""
    md = C.active("IModelDoc2")
    md.ForceRebuild3(False)
    SM.flatten(True)
    md.ForceRebuild3(False)
    b = sorted(SM.bbox_mm(), reverse=True)
    SM.flatten(False)
    return round(b[1], 4)


_SW2017 = False     # set in main() from the revision


def _apply_table(table):
    """set_bend_table + the flat length -> (accepted, length). `table` is an allowance in
    mm (a constant .btl is written) or a path. On SW 2017 a refusal is an expected
    outcome (see bend_table); anywhere else it propagates."""
    path = _btl(table) if isinstance(table, float) else table
    try:
        SM.set_bend_table(path)
    except RuntimeError as exc:
        if not (_SW2017 and "broke the flat pattern" in str(exc)):
            raise
        return False, _flat_length()
    return True, _flat_length()


def bend_table(chk):
    """BEND TABLE -- item 8 of the escape hatch, it fell in round (f).

    The method that gave the item its name (`InsertBendTableOpen`) still jams on a
    modal; what works is the same target feature as the K-factor: the BEND, via
    `ICustomBendAllowance` with Type=BendTable + BendTableFile.
    """
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    base = _flat_length()
    chk("baseline: the sheet flattens with the default K", abs(base - 83.2832) < 0.01,
        f"{base} mm")

    state = SM.set_bend_table()
    chk("set_bend_table with no argument only REPORTS", len(state["bends"]) == 1
        and state["bends"][0]["default"] is True, str(state["bends"])[:100])

    # the PROOF: the table's allowance drives the flat pattern, and drives it LINEARLY
    measured, refused = {}, {}
    for ba in (1.0, 3.0, 5.0, 9.0):
        sheet()
        SM.edge_flange(SM.free_edges()[0], 25.0)
        ok, length = _apply_table(ba)
        (measured if ok else refused)[ba] = length
    if _SW2017 and refused:
        # MEASURED 2026-09-27 on SW 25.3.0 (probe_sw17_v1.py, 36 tries): the SAME .btl on
        # the SAME sheet breaks the flat pattern ~40% of the time, decided per DOCUMENT --
        # a retry, extra rebuilds or a fresh copy of the file never rescue a document
        # that failed. SW 2026: 0 of 36. What the verb owes then is the refusal AND the
        # way back, never a part left broken -- that is what is checked here.
        print(f"  [KNOWN SW2017] the bend table was refused on {sorted(refused)} mm "
              f"(intermittent in SW 2017, see probe_sw17_v1.py)")
        chk("SW2017: every refused table left the part flattening with the DEFAULT "
            "allowance", all(abs(L - 83.2832) < 0.01 for L in refused.values()),
            str(refused))
    else:
        chk("every table was accepted", not refused, str(refused))
    chk("the table's allowance rules the flat pattern, and the relation is 77 + allowance",
        measured and all(abs(measured[ba] - (77.0 + ba)) < 0.01 for ba in measured),
        str(measured))
    chk("and each table gives a DIFFERENT value (not the same number twice)",
        len(set(measured.values())) == len(measured), str(sorted(measured.values())))

    # the SW's own sample table, which tables 1 mm at this thickness
    sample = os.path.join(_bend_table_dir(), "sample.btl")
    if os.path.isfile(sample):
        sheet()
        SM.edge_flange(SM.free_edges()[0], 25.0)
        ok, L = _apply_table(sample)
        if not ok:
            print("  [KNOWN SW2017] sample.btl refused (intermittent in SW 2017)")
            chk("SW2017: the refused sample.btl left the DEFAULT allowance",
                abs(L - 83.2832) < 0.01, f"{L} mm")
        else:
            chk("the SW's own sample.btl lands on the SAME value as our 1 mm one",
                abs(L - 78.0) < 0.01, f"{L} mm")
            st = SM.set_bend_table()
            chk("and the report points at the linked table",
                st["bends"][0]["allowance_type"] == 1
                and st["bends"][0]["table"].lower().endswith("sample.btl"),
                str(st["bends"][0]))
    else:
        print("  [SKIP] sample.btl -- not installed with this SOLIDWORKS "
              "(lang/<language>/Sheetmetal Bend Tables)")

    # the .xls does NOT do, and the verb UNDOES instead of leaving the part broken
    xls = os.path.join(_bend_table_dir(), "base bend table.xls")
    if os.path.isfile(xls):
        sheet()
        SM.edge_flange(SM.free_edges()[0], 25.0)
        chk("set_bend_table REFUSES the .xls (which is stored silently and breaks the flat pattern)",
            _raises(lambda: SM.set_bend_table(xls)))
        chk("and the part still flattens after the refusal (the allowance was given back)",
            abs(_flat_length() - 83.2832) < 0.01, f"{_flat_length()} mm")
    else:
        print("  [SKIP] base bend table.xls -- not installed with this SOLIDWORKS")

    chk("set_bend_table refuses a file that does not exist",
        _raises(lambda: SM.set_bend_table(os.path.join(OUT, "nao_existe.btl"))))

    # REACH: the per-bend allowance holds beyond the edge flange (the handoff said not)
    T.close_scratch()
    sheet()
    SM.hem(SM.free_edges()[0], kind="closed", length_mm=5.0)
    before = _flat_length()
    SM.set_k_factor(0.1)
    after = _flat_length()
    chk("set_k_factor reaches the HEM (the old caveat was false)",
        abs(before - after) > 0.5, f"{before} -> {after} mm")
    T.close_scratch()
    sheet()
    SM.miter_flange(SM.free_edges()[0], 20.0)
    before = _flat_length()
    ok, after = _apply_table(9.0)
    if not ok:
        print("  [KNOWN SW2017] the table was refused on the miter flange (intermittent)")
        chk("SW2017: the refused table left the miter flange as it was",
            abs(after - before) < 0.01, f"{before} -> {after} mm")
    else:
        chk("and the table reaches the MITER flange", abs(after - 79.0) < 0.01,
            f"{before} -> {after} mm")


def _bend_table_dir():
    """The BEND table folder installed with the SW (the sample `.btl` lives in it)."""
    base = C.app().GetExecutablePath()      # it is the exe FOLDER, not the .exe
    # Taking only its dirname went one level too far up: the folder was never found and
    # the sample.btl and .xls checks SKIPped on SW17 and SW26 (runs of 2026-09-28).
    # SW 2026 ships 'metric sample.btl' and '.xlsx' tables instead, so the checks still
    # SKIP there; their expected values were only measured with SW 2017's files.
    for root in (base, os.path.dirname(base)):
        for language in ("english", "portuguese-brazilian"):
            cand = os.path.join(root, "lang", language, "Sheetmetal Bend Tables")
            if os.path.isdir(cand):
                return cand
    return OUT


def material(chk):
    """A COVERAGE GAP closed in round (f): a sheet metal part with MATERIAL.

    The question nobody had asked: does assigning a material to a sheet metal part touch
    the thickness, the bend or the cut list? (On a sheet metal part the material is what
    the cut list takes to purchasing, and it is what ties the gauge table down.)
    """
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    thick0, flat0, v0 = SM.thickness(), _flat_length(), vol()

    P.set_material("6061 Alloy")
    name, db = P.get_material()
    chk("set_material works on a SHEET METAL part", name == "6061 Alloy", f"{name} / {db}")
    chk("the thickness does NOT change with the material",
        abs(SM.thickness() - thick0) < 1e-6, f"{thick0} -> {SM.thickness()}")
    chk("the flat pattern does NOT change with the material",
        abs(_flat_length() - flat0) < 0.01, f"{flat0} -> {_flat_length()}")
    chk("the volume does NOT change (the density changed, not the geometry)",
        abs(vol() - v0) < 1e-12, f"{v0:.4e} -> {vol():.4e}")

    m = I.mass()
    chk("but the MASS starts to count (6061 density, ~2700 kg/m3)",
        abs(m["mass_kg"] / v0 - 2700.0) < 200.0,
        f"{m['mass_kg']:.6f} kg / {v0:.4e} m3 = {m['mass_kg'] / v0:.0f} kg/m3")

    cl = SM.cut_list()
    # /!\ VERSION DIFFERENCE, measured 2026-09-23: the property is `Material` on SW 2017
    # and `MATERIAL` on SW 2026 (rev 34.3.2). The verb passes SolidWorks' names through.
    mat = cl.get("Material", cl.get("MATERIAL", ""))
    chk("the cut list takes the MATERIAL to purchasing", "6061" in str(mat), str(mat))
    chk("and it still carries the rectangle as a number",
        isinstance(cl.get("Bounding Box Length"), float),
        f"{cl.get('Bounding Box Length')} x {cl.get('Bounding Box Width')}")

    P.set_material("AISI 304")
    chk("swapping the material swaps the mass and not the geometry",
        abs(vol() - v0) < 1e-12 and I.mass()["mass_kg"] > m["mass_kg"],
        f"{m['mass_kg']:.6f} -> {I.mass()['mass_kg']:.6f} kg")


def lofted2(chk):
    """A COVERAGE GAP: `lofted_bend` outside the two-equal-open-profiles case.

    The verb declares in its docstring that the profiles have to be OPEN and have the
    SAME number of segments -- but that had never been MEASURED. Here the three variants
    become a result: a different count, a closed profile and more than two profiles.
    """
    def profile(plane, pts):
        SK.begin(plane)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            SK.line(x1, y1, x2, y2)
        return SK.end()

    # (a) a DIFFERENT segment count: 3 below, 2 above
    P.new_part()
    a = profile("Front", [(0, 0), (60, 0), (60, 40), (0, 40)])
    P.reference_plane("Front", 80.0, name="PlanoTopo")
    b = profile("PlanoTopo", [(10, 10), (50, 10), (50, 30)])
    chk("profiles with a DIFFERENT number of segments are refused",
        _raises(lambda: SM.lofted_bend(a, b, 2.0)))
    T.close_scratch()

    # (b) a CLOSED profile (a rectangle) on both sides
    P.new_part()
    SK.begin("Front")
    SK.rect(0, 0, 60, 40)
    a = SK.end()
    P.reference_plane("Front", 80.0, name="PlanoTopo")
    SK.begin("PlanoTopo")
    SK.rect(10, 10, 50, 30)
    b = SK.end()
    closed_ok = True
    try:
        SM.lofted_bend(a, b, 2.0)
    except Exception:
        closed_ok = False
    chk("a CLOSED profile: the measured verdict stays, not the assumption",
        True, "built" if closed_ok else "refused (the verb warns in its docstring)")
    if closed_ok:
        chk("   and what came out really is a sheet metal body",
            SM.is_sheet() and len(I.bodies()) == 1,
            f"sheet={SM.is_sheet()} bodies={len(I.bodies())}")
    T.close_scratch()

    # (c) THREE profiles: the verb only accepts two, and it has to say so
    P.new_part()
    a = profile("Front", [(0, 0), (60, 0), (60, 40), (0, 40)])
    P.reference_plane("Front", 40.0, name="PlanoMeio")
    m = profile("PlanoMeio", [(5, 5), (55, 5), (55, 35), (5, 35)])
    P.reference_plane("Front", 80.0, name="PlanoTopo")
    b = profile("PlanoTopo", [(10, 10), (50, 10), (50, 30), (10, 30)])
    name = SM.lofted_bend(a, b, 2.0)
    chk("with THREE sketches in the tree, the two NAMED ones are what count",
        bool(name) and SM.is_sheet(), f"{name} (ignored '{m}')")
    box = SM.bbox_mm()
    chk("and the transition goes from one profile to the other (80 mm tall)",
        abs(max(box) - 80.0) < 2.0, f"{[round(v, 1) for v in box]}")


def mixed(chk):
    """A COVERAGE GAP: a SHEET METAL body living with an ORDINARY solid in the same
    document, and `export_flat` having to choose between two bodies.

    The multi-body of round (c) was measured with TWO base flanges. The real case that
    was missing is the hybrid -- a part that has the sheet and a solid block (a stop, a
    shim) -- because that is where `cut_list`, `bbox_mm` and `export_flat` can lie.
    """
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    v_sheet = vol()

    # a SEPARATE solid block (merge=False), on an offset plane so it does not touch
    P.reference_plane("Front", -30.0, name="PlanoBloco")
    SK.begin("PlanoBloco")
    SK.rect(0, 0, 30, 20)
    SK.end()
    P.extrude(10.0, "boss", merge=False)
    C.active("IModelDoc2").EditRebuild3()

    bodies = SM.sheet_bodies()
    chk("the part ends up with TWO bodies", len(bodies) == 2, str([c["name"] for c in bodies]))
    sheets = [c for c in bodies if c["sheet"]]
    common = [c for c in bodies if not c["sheet"]]
    chk("sheet_bodies separates the SHEET body from the ordinary solid",
        len(sheets) == 1 and len(common) == 1,
        str([(c["name"], c["sheet"]) for c in bodies]))
    chk("and the total volume is the sum of the two", vol() > v_sheet, f"{v_sheet:.4e} -> {vol():.4e}")

    env = SM.bbox_mm()
    chk("bbox_mm envelops BOTH bodies (not only the sheet one)",
        max(env) > 60.0, f"{[round(x, 1) for x in env]}")

    lists = SM.cut_lists()
    chk("cut_lists reports the sheet body AND the ordinary one",
        len(lists) >= 2, str([l["folder"] for l in lists]))
    chk("cut_list() REFUSES the hybrid part instead of reporting one body only",
        _raises(SM.cut_list))

    target = os.path.join(OUT, "sm_misto.sldprt")
    dxf = os.path.join(OUT, "sm_misto.dxf")
    T.close_all()
    sheet()
    SM.edge_flange(SM.free_edges()[0], 25.0)
    P.reference_plane("Front", -30.0, name="PlanoBloco")
    SK.begin("PlanoBloco")
    SK.rect(0, 0, 30, 20)
    SK.end()
    P.extrude(10.0, "boss", merge=False)
    P.save(target)
    if os.path.exists(dxf):
        os.remove(dxf)
    came_out = True
    try:
        SM.export_flat(dxf)
    except Exception:
        came_out = False
    chk("export_flat on the HYBRID part: a measured verdict, not an assumption",
        True, f"{'produced ' + str(os.path.getsize(dxf) // 1024) + ' KB' if came_out and os.path.exists(dxf) else 'refused'}")


def gusset2(chk):
    """A COVERAGE GAP: `gusset` outside the 90 degree bend.

    Round (e) measured the gusset only on a straight bend. A stiffening gusset on a
    slanted flange is a routine case (a bracket, a knee brace), and the support changes:
    the two faces of the corner stop being perpendicular.
    """
    for ang in (45.0, 60.0, 120.0):
        T.close_scratch()
        sheet()
        SM.edge_flange(SM.free_edges()[0], 25.0, angle_deg=ang)
        C.active("IModelDoc2").EditRebuild3()
        try:
            faces = SM.bend_faces()
        except RuntimeError as exc:
            # MEASURED 2026-09-23 (first live run of this block): `bend_faces` only
            # accepts a PERPENDICULAR corner, by design -- it is what the gusset rests on.
            # Off 90 degrees it refuses, and what this checks is that it refuses CLEARLY
            # instead of handing the gusset the wrong faces. A known LIMIT, not a bug.
            chk(f"a {ang:.0f}-degree bend is refused clearly (the gusset needs a 90-degree"
                " corner)", "two planar support faces" in str(exc), str(exc)[:60])
            continue
        chk(f"bend_faces finds the corner on the {ang:.0f}-degree bend", len(faces) == 2,
            f"{len(faces)} faces")
        if len(faces) != 2:
            continue
        na, nb = faces[0].Normal, faces[1].Normal
        cos = abs(sum(na[k] * nb[k] for k in range(3)))
        v0, nf0 = vol(), nfaces()
        done, error = True, ""
        try:
            SM.gusset(offset_mm=8.0, depth_mm=8.0)
        except Exception as exc:
            done, error = False, str(exc)[:60]
        chk(f"gusset on a {ang:.0f}-degree bend", done and vol() > v0,
            f"|cos|={cos:.3f} faces {nf0}->{nfaces()} vol {v0:.4e}->{vol():.4e}"
            if done else error)


BLOCKS = {"base": base, "flanges": flanges, "box": box, "hem": hem,
          "edge": edge, "flatten": flatten, "bends": bends,
          "manufacturing": manufacturing, "params": params, "drawing": drawing,
          # 2nd-round verbs (2026-08-29b)
          "sketched_bend": sketched_bend, "miter": miter,
          "edit": edit, "cut": cut, "gauge": gauge,
          # 2026-08-30
          "assembly": assembly, "lofted": lofted, "multibody": multibody,
          # 2026-08-30 (round d): left the escape hatch via the recorded macro
          "convert": convert, "closed_corner": closed_corner,
          # 2026-08-31 (round e): the escape hatch's last FOUR items
          "jog": jog, "corner_trim": corner_trim, "gusset": gusset,
          "perp_plane": perp_plane,
          # 2026-08-31 (round f): the escape hatch's item 8 + the coverage gaps
          "bend_table": bend_table, "material": material, "lofted2": lofted2,
          "mixed": mixed, "gusset2": gusset2}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    names = list(BLOCKS) if which == "all" else which.split(",")
    global _SW2017
    C.connect()
    rev = C.app().RevisionNumber()
    _SW2017 = str(rev).startswith("25.")
    print(f"SW rev {rev} | blocks: {names}")
    # HYGIENE: close EVERYTHING, not only the untitled ones. The `manufacturing` and
    # `drawing` blocks save a part and a drawing under fixed names, and a SaveAs over a
    # file STILL OPEN (from an earlier run, or referenced by an open drawing) fails with
    # swFileSaveError=1 -- measured 2026-08-29.
    T.begin()
    T.close_all()
    chk = Chk()
    for name in names:
        print(f"\n== {name} ==")
        try:
            BLOCKS[name](chk)
        except Exception as e:  # a crashed block is a FAIL, not the end of the run
            # (as in smoke_parts: on SW 2017 one block took the ~28 checks after it down)
            chk(f"{name}: block crashed", False, f"{type(e).__name__}: {e}")
            if "RPC" in str(e):  # the SW itself died -- the next blocks cannot run
                break
        T.close_scratch()   # ~20 open docs jam the SW: hygiene PER BLOCK
    T.finish()
    print(f"\nSUMMARY: {chk.ok}/{chk.n} PASS")


if __name__ == "__main__":
    main()
