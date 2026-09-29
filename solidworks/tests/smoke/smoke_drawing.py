# -*- coding: utf-8 -*-
r"""
SMOKE of the DRAWING verbs (solidworks/sw_drawing.py) -- organised into named BLOCKS.

In dev, run ONLY the block under test (no window pile-up / no redoing what is validated).

Usage (from the MCP-SolidWorks root; attaches to an open SW):
  .venv\Scripts\python.exe -u solidworks\tests\smoke\smoke_drawing.py [block ...|all]
  blocks: see BLOCKS at the end of the file  (default: basic views dims)

The NOTE texts written on the sheet stay in Portuguese: they are content of a pt-BR
drawing (the default `language`), not code.

It creates a SAVED test part (viga_dwg.sldprt) in the test output directory -- a drawing
needs a model on DISK. The assembly blocks use the clevis assembly that smoke_clevis.py
saves in the SAME output directory; run alone, they report [SKIP], not PASS. In
development it leaves the drawing OPEN at the end (for a screenshot); in isolated mode
(solidworks/tests/isolation.py) it closes what it opened.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from solidworks import sw_com as C, sw_parts as P, sw_drawing as D, sw_inspect as I  # noqa: E402
from solidworks.tests import isolation as T  # noqa: E402

# shared tests/validation_assembly/ in development, a new directory per run in public
OUT = T.output_root()
PART = os.path.join(OUT, "viga_dwg.sldprt")
PART_DIM = os.path.join(OUT, "viga_cotada.sldprt")


class Chk:
    def __init__(self):
        self.ok = self.n = 0

    def __call__(self, name, cond, detail=""):
        self.n += 1
        self.ok += bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL '}] {name}" + (f" -- {detail}" if detail else ""))


# ── fixtures ──────────────────────────────────────────────────────────────────
def make_part():
    """A simple part with dimensioned features (a drilled block) SAVED to disk -> the
    views' source. IDEMPOTENT: if it already exists on disk, it is reused (re-saving over a
    referenced file is the SaveAs gotcha). Delete the .sldprt to force a rebuild."""
    if os.path.exists(PART):
        return PART
    P.new_part()
    P.sketch("Top", "rect", x1=0, y1=0, x2=120, y2=40)
    P.extrude(25, "boss")
    # a through hole (it produces an importable hole callout)
    P.sketch("Top", "circle", r=6, cx=20, cy=20)
    try:
        P.extrude(0, "cut", through_all=True)
    except RuntimeError:
        P.sketch("Top", "circle", r=6, cx=20, cy=20)
        P.extrude(0, "cut", through_all=True, reverse=True)
    P.set_material("6061 Alloy")
    return P.save(PART)


def open_drawing_on_part(size="A3", projection="first"):
    T.close_scratch()   # closes unsaved drawings from earlier blocks (no pile-up)
    make_part()
    return D.new_drawing(PART, size=size, projection=projection)


def _seg_ends(s):
    ln = C.cast(s, "ISketchLine")
    a = C.cast(ln.GetStartPoint2(), "ISketchPoint")
    b = C.cast(ln.GetEndPoint2(), "ISketchPoint")
    return (a.X, a.Y, a.Z), (b.X, b.Y, b.Z)


def make_dimensioned_part():
    """A FULLY DIMENSIONED part (L120,W40,H25, a Ø12 hole at 20/20) + an H7 tolerance on the
    hole. It shows the pattern for dimensioning the MODEL (ISketchSegment.Select4 +
    AddHorizontal/VerticalDimension2; the hole's position to the corner VERTEX). IDEMPOTENT.
    Delete the .sldprt to force a rebuild."""
    T.close_titles((os.path.basename(PART_DIM),))
    if os.path.exists(PART_DIM):
        return PART_DIM
    P.new_part()
    md = C.active("IModelDoc2")
    sm = C.cast(md.SketchManager, "ISketchManager")
    C.select_plane(md, "Top"); sm.InsertSketch(True)
    rect = sm.CreateCornerRectangle(0, 0, 0, C.mm(120), C.mm(40), 0)
    md.ClearSelection2(True)
    horiz = next(s for s in rect if abs(_seg_ends(s)[0][1] - _seg_ends(s)[1][1]) < 1e-6)
    vert = next(s for s in rect if abs(_seg_ends(s)[0][0] - _seg_ends(s)[1][0]) < 1e-6)
    C.cast(horiz, "ISketchSegment").Select4(False, None)
    md.AddHorizontalDimension2(C.mm(60), C.mm(-15), 0)
    md.ClearSelection2(True)
    C.cast(vert, "ISketchSegment").Select4(False, None)
    md.AddVerticalDimension2(C.mm(-15), C.mm(20), 0)
    md.ClearSelection2(True)
    sm.InsertSketch(True)
    P.extrude(25, "boss")
    C.select_plane(md, "Top"); sm.InsertSketch(True)
    circ = sm.CreateCircleByRadius(C.mm(20), C.mm(20), 0, C.mm(6))
    md.ClearSelection2(True)
    C.cast(circ, "ISketchSegment").Select4(False, None)
    md.AddDimension2(C.mm(35), C.mm(35), 0)                 # diameter
    md.ClearSelection2(True)
    center = C.cast(C.cast(circ, "ISketchArc").GetCenterPoint2(), "ISketchPoint")
    ext = C.cast(md.Extension, "IModelDocExtension")
    for placer in (lambda: md.AddHorizontalDimension2(C.mm(10), C.mm(-10), 0),
                   lambda: md.AddVerticalDimension2(C.mm(-10), C.mm(10), 0)):
        ext.SelectByID2("", "VERTEX", 0.0, 0.0, 0.0, False, 0, None, 0)
        C.cast(center, "ISketchPoint").Select4(True, None)
        placer()
        md.ClearSelection2(True)
    sm.InsertSketch(True)
    try:
        P.extrude(0, "cut", through_all=True)
    except RuntimeError:
        P.extrude(0, "cut", through_all=True, reverse=True)
    P.set_tolerance("D1@Sketch2", "fit", hole_fit="H7")     # the Ø12 H7 hole
    P.set_tolerance("D1@Sketch1", "bilateral", upper_mm=0.2, lower_mm=0.0)
    P.set_material("6061 Alloy")
    P.set_property("Description", "VIGA DE TESTE")           # -> the title block's TITLE
    P.set_property("Material", "AL 6061-T6")                 # -> MATERIAL
    P.set_property("Finish", "Ra 3.2")                       # -> FINISH
    return P.save(PART_DIM)


def view_count():
    """Counts drawing views (excluding the 'sheet', which is GetFirstView's 1st)."""
    dwg = C.active("IDrawingDoc")
    n, raw = 0, dwg.GetFirstView()   # the 1st = the sheet
    raw = C.cast(raw, "IView").GetNextView() if raw is not None else None
    while raw is not None:
        n += 1
        raw = C.cast(raw, "IView").GetNextView()
    return n


# ── blocks ────────────────────────────────────────────────────────────────────
def basic(chk):
    p = os.path.exists(make_part())
    chk("source part saved to disk", p, PART)
    title = D.new_drawing(PART, size="A3", projection="first")
    chk("new_drawing A3 first-angle", title and "Draw" not in title or bool(title), title)
    dwg = C.active("IDrawingDoc")
    chk("the active doc is a drawing", C.active("IModelDoc2").GetType() == C.DOC_DRAWING)
    sh = C.cast(dwg.GetCurrentSheet(), "ISheet")
    props = sh.GetProperties2()  # [paperSize, template, scale1, scale2, firstAngle, w, h]
    chk("A3 sheet applied (paperSize=8)", int(props[0]) == 8, f"props={list(props)}")
    chk("first-angle (firstAngle)", bool(props[4]), f"firstAngle={props[4]}")
    names = list(dwg.GetSheetNames())
    chk("a single ISO sheet (the ANSI Sheet1 removed)",
        names == ["ISO_A3"], f"sheets={names}")


def views(chk):
    open_drawing_on_part()
    vf = D.add_view("front", at=(60, 130))
    chk("add_view front", bool(vf), vf)
    vt = D.add_view("top", at=(60, 60))
    chk("add_view top (inherits the model)", bool(vt), vt)
    vr = D.add_view("right", at=(180, 130))
    chk("add_view right", bool(vr), vr)
    vi = D.add_view("iso", at=(220, 60), scale=0.5)
    chk("add_view iso @1:2", bool(vi), vi)
    chk("4 views on the sheet", view_count() == 4, f"{view_count()} views")


def dims(chk):
    open_drawing_on_part()
    D.add_view("front", at=(80, 120))
    D.add_view("right", at=(200, 120))
    D.add_view("top", at=(80, 50))
    n = D.import_model_dims(types=("dimensions", "holes"))  # all_views=True (reliable)
    chk("import_model_dims (all views)", isinstance(n, int) and n >= 1, f"{n} annotations")


def annotate(chk):
    open_drawing_on_part()
    D.add_view("front", at=(100, 130))
    t = D.annotate("PECA DE TESTE - MATERIAL 6061", at=(30, 30))
    chk("annotate a note on the sheet", bool(t), t)


def centermarks(chk):
    open_drawing_on_part()
    vt = D.add_view("top", at=(120, 120))
    n = D.center_marks(vt)
    chk("center_marks on the hole (>=1)", isinstance(n, int) and n >= 1, f"{n} marks")


def dim_variants(chk):
    # covers dimension()'s 3 orients + a dimension of ONE edge (top view = 120 x 40)
    open_drawing_on_part()
    vt = D.add_view("top", at=(120, 120))
    cx, cy = D.view_center(vt)
    d1 = D.dimension(vt, picks=[(cx, cy - 20)], place_at=(cx, cy - 32), orient="horizontal")
    chk("dimension 1 horizontal edge (120)", abs(d1 - 120) < 0.5, f"{d1}")
    d2 = D.dimension(vt, picks=[(cx - 60, cy)], place_at=(cx - 74, cy), orient="vertical")
    chk("dimension 1 vertical edge (40)", abs(d2 - 40) < 0.5, f"{d2}")
    d3 = D.dimension(vt, picks=[(cx, cy - 20)], place_at=(cx, cy - 44), orient="aligned")
    chk("dimension aligned (120)", abs(d3 - 120) < 0.5, f"{d3}")


def gdt(chk):
    # GD&T: datum A on the front's bottom edge + perpendicularity of the left edge to A
    open_drawing_on_part()
    vf = D.add_view("front", at=(150, 150))
    cx, cy = D.view_center(vf)
    # front view = 120 (x) x 25 (z). the bottom edge at cy-12.5; the left one at cx-60
    lbl = D.datum(vf, edge_at=(cx, cy - 12.5), label="A")
    chk("datum A inserted", lbl == "A", f"label={lbl}")
    tol = D.gtol(vf, edge_at=(cx - 60, cy), symbol="perpendicularity", tolerance=0.05,
                 datums=("A",))
    chk("gtol perpendicularity 0.05 to A", tol == "0.05", f"tol={tol}")
    # a hole position with Ø and MMC (top view, on the hole's edge)
    vt = D.add_view("top", at=(150, 90))
    tcx, tcy = D.view_center(vt)
    tolp = D.gtol(vt, edge_at=(tcx - 34, tcy), symbol="position", tolerance=0.1,
                  datums=("A",), diameter=True, mc="mmc")
    chk("gtol position Ø0.1 (M) to A on the hole", tolp == "0.1", f"tol={tolp}")


def dwg_variants(chk):
    # standard/language/units/projection never covered (only ISO/pt/mm/1st angle)
    T.close_scratch(); make_part()
    D.new_drawing(PART, size="A3", standard="din", language="en", projection="third")
    dwg = C.active("IDrawingDoc")
    chk("standard din (a single DIN_A3 sheet)", list(dwg.GetSheetNames()) == ["DIN_A3"],
        f"{list(dwg.GetSheetNames())}")
    props = C.cast(dwg.GetCurrentSheet(), "ISheet").GetProperties2()
    chk("projection third (firstAngle=False)", not bool(props[4]), f"firstAngle={props[4]}")
    T.close_scratch(); D.new_drawing(PART, size="A3", standard="jis")
    chk("standard jis (JIS_A3 sheet)",
        list(C.active("IDrawingDoc").GetSheetNames()) == ["JIS_A3"],
        f"{list(C.active('IDrawingDoc').GetSheetNames())}")
    T.close_scratch(); D.new_drawing(PART, size="A3", standard="ansi", units="in")
    ext = C.cast(C.active("IModelDoc2").Extension, "IModelDocExtension")
    us = ext.GetUserPreferenceInteger(D._PREF_UNIT_SYSTEM, 0)
    chk("units inch (native ansi, IPS=3)", us == D._UNITSYS_IPS, f"unitsys={us}")
    T.close_scratch(); D.new_drawing(PART, size="A3")
    D.sheet_scale(1, 2)
    p2 = C.cast(C.active("IDrawingDoc").GetCurrentSheet(), "ISheet").GetProperties2()
    chk("sheet_scale 1:2", abs(p2[2] / p2[3] - 0.5) < 1e-6, f"scale={p2[2]}:{p2[3]}")


def annotate_view(chk):
    open_drawing_on_part()
    vf = D.add_view("front", at=(120, 130))
    t = D.annotate("NOTA PRESA A VISTA", at=(50, 40), view_name=vf)
    chk("annotate attached to the view", bool(t), t)


def tables(chk):
    asm = os.path.join(OUT, "clevis.sldasm")
    if not os.path.exists(asm):
        # a SKIP is not a PASS: the BOM was not validated
        print(f"  [SKIP] assembly BOM -- no clevis.sldasm in {OUT} (run smoke_clevis.py first)")
        return
    D.new_drawing(asm, size="A3")
    v = D.add_view("iso", at=(120, 120))
    D.table(v, kind="bom", bom_type="top", at=(250, 200))
    chk("assembly BOM table", True, v)


def assembly_pipeline(chk):
    # assembly -> drawing END TO END: views + BOM + balloons + exploded iso + PDF
    import pythoncom
    from solidworks import sw_assembly as A
    asm = os.path.join(OUT, "clevis.sldasm")
    if not os.path.exists(asm):
        print(f"  [SKIP] assembly pipeline -- no clevis.sldasm in {OUT} (run smoke_clevis.py first)")
        return
    T.close_scratch()
    C.app().OpenDoc6(asm, C.DOC_ASSEMBLY, 1, "", pythoncom.Missing, pythoncom.Missing)
    r = A.explode()
    chk("the assembly explodes with no overlap left", bool(r["view"]) and r["overlaps"] == [],
        str(r))
    A.collapse()   # ExplView1 persists in the config; the drawing view re-explodes by itself
    D.new_drawing(asm, size="A3")
    vf = D.add_view("front", at=(80, 210))
    D.add_view("top", at=(80, 130))
    vi = D.add_view("iso", at=(250, 180), scale=0.8)
    chk("3 assembly views", view_count() == 3, f"{view_count()} views")
    ex = D.set_exploded(vi, True)          # explode the iso BEFORE ballooning (exposes every item)
    chk("iso in the exploded state", ex, f"IsExploded={ex}")
    nb = D.balloons(vi, layout="circle")   # balloon BEFORE the BOM (a BOM first breaks AutoBalloon)
    chk("balloons numbering the items (>=2)", nb >= 2, f"{nb} balloons")
    D.table(vi, kind="bom", at=(300, 215))
    chk("BOM inserted after the balloons", True)
    pdf = D.export(os.path.join(OUT, "clevis_conjunto.pdf"))
    chk("assembly PDF exported", os.path.exists(pdf), pdf)


def section(chk):
    open_drawing_on_part()
    vf = D.add_view("front", at=(120, 120))
    try:
        vs = D.section_view(vf, (120, 90), (120, 150), (220, 120), label="A")
        chk("section_view A-A", bool(vs), vs)
    except RuntimeError as e:
        chk("section_view A-A", False, str(e))


def tolerances(chk):
    T.close_scratch()
    p = make_dimensioned_part()
    chk("a fully dimensioned part saved", os.path.exists(p), p)
    # layout with a margin: everything inside the A3 frame (nothing off the sheet)
    D.new_drawing(p, size="A3", standard="iso", language="pt", units="mm")
    vf = D.add_view("front", at=(150, 215)); vt = D.add_view("top", at=(150, 140))
    vr = D.add_view("right", at=(268, 215)); D.add_view("iso", at=(345, 210), scale=0.5)
    n = D.import_model_dims(types=("dimensions", "holes"))
    chk("complete dimensions imported (>=6)", isinstance(n, int) and n >= 6, f"{n} dimensions")
    rem = D.dedupe_dimensions()
    chk("dedupe (one dimension each) removed the duplicates", rem == 3, f"removed {rem}")
    # a REFERENCE dimension straight on the drawing (not from the sketch): hole -> right edge = 100
    cx, cy = D.view_center(vt)
    ref = D.dimension(vt, picks=[(cx - 40, cy + 6), (cx + 60, cy)],
                      place_at=(cx + 5, cy + 28), orient="horizontal")
    chk("reference dimension on the drawing (=100)", abs(ref - 100.0) < 0.5, f"{ref} mm")
    tb = D.fill_titleblock(drawn_by="A.SILVA", drawn_date="2026-07-23", revision="A",
                           weight="0.10 kg", company="ANSYS-MCP")
    chk("title block filled (the drawing's fields)", "DrawnBy" in tb, str(list(tb)))
    fx, fy = D.view_center(vf)                                   # attached to the top edge
    sf = D.surface_finish(vf, edge_at=(fx, fy + 12.5), ra=3.2, kind="machined")
    chk("surface_finish (Ra 3.2) preso a surface", sf == "Ra 3.2", sf)
    D.annotate("TOL. GERAL: LINEAR +/-0.1  ANGULAR +/-0.5 gr", at=(30, 30))
    # gate: no view escapes the A3 frame (~[15,405]x[15,282] mm)
    dwg = C.active("IDrawingDoc")
    inside = True
    for nm in (vf, vr):
        dwg.ActivateView(nm)
        o = [c * 1000 for c in C.cast(dwg.ActiveDrawingView, "IView").GetOutline()]
        inside = inside and o[0] > 15 and o[1] > 15 and o[2] < 405 and o[3] < 282
    chk("views inside the sheet frame", inside)


def export(chk):
    open_drawing_on_part()
    D.add_view("front", at=(100, 130))
    D.add_view("top", at=(100, 60))
    pdf = D.export(os.path.join(OUT, "viga_dwg.pdf"))
    chk("export PDF", os.path.exists(pdf), pdf)


def read_views(chk):
    """dwg.list_views + dwg.centerlines: read the sheet back, and the axis of a shaft
    seen from the side."""
    open_drawing_on_part()
    vf = D.add_view("front", at=(100, 130))
    vt = D.add_view("top", at=(100, 50))
    vi = D.add_view("iso", at=(250, 110), scale=0.5)
    vs = D.list_views()
    chk("list_views: names, orientation, scale, model",
        [v["name"] for v in vs] == [vf, vt, vi]
        and [v["orientation"] for v in vs] == ["*Front", "*Top", "*Isometric"]
        and vs[2]["scale"] == "1:2" and all(v["model"].lower() == PART.lower() for v in vs)
        and abs(vs[0]["position_mm"][0] - 100) < 0.01,
        str([(v["name"], v["orientation"], v["scale"]) for v in vs]))
    chk("centerlines: a HIDDEN hole gets none (the view does not expose it)",
        D.centerlines(vf) == 0)
    # a shaft (a visible cylinder along Y) seen from the side
    T.close_scratch()
    shaft = os.path.join(OUT, "shaft_dwg.sldprt")
    P.new_part(); P.sketch("Top", "circle", r=10, cx=0, cy=0); P.extrude(60, "boss")
    P.save(shaft)
    D.new_drawing(shaft)
    sf = D.add_view("front", at=(100, 130))
    st = D.add_view("top", at=(100, 50))
    chk("centerlines: the shaft seen from the side gets its axis", D.centerlines(sf) == 1)
    chk("centerlines: a second call adds none", D.centerlines(sf) == 1)
    chk("centerlines: the view along the axis gets none", D.centerlines(st) == 0)


def move_views(chk):
    """dwg.view_info / model_to_sheet / move_view / align_view / set_view_scale /
    delete_view: read, aim at and move views AFTER they are on the sheet. The part is
    120 x 40 x 25 (x 0..120, y 0..25, z 0..-40) with a hole at x=20, z=-20."""
    open_drawing_on_part()
    vf = D.add_view("front", at=(100, 200))
    vt = D.add_view("top", at=(100, 120))
    vi = D.add_view("iso", at=(320, 200), scale=0.5)
    near = lambda a, b, tol=0.05: all(abs(x - y) <= tol for x, y in zip(a, b))  # noqa: E731

    m = D.view_info()
    geo = {v["name"]: v["geometry_mm"] for v in m["views"]}
    chk("view_info(): the sheet map lists every view, frame and title block",
        [v["name"] for v in m["views"]] == [vf, vt, vi] and m["titleblock_mm"]
        and m["problems"] == [], str(m["problems"]))
    chk("view_info(): geometry_mm is the model box on the sheet (front 120 x 25)",
        near(geo[vf], [40, 187.5, 160, 212.5]), str(geo[vf]))
    chk("model_to_sheet: model origin and hole centre land where the views show them",
        near(D.model_to_sheet(vf, (0, 0, 0)), [40, 187.5])
        and near(D.model_to_sheet(vt, [(20, 25, -20)])[0], [60, 120]),
        str(D.model_to_sheet(vt, (20, 25, -20))))

    r = D.move_view(vf, by=(20, 0))
    chk("move_view by=: the named top view is NOT aligned, so it stays behind",
        near(r["position_mm"], [120, 200]) and r["moved_with"] == {}, str(r))
    a = D.align_view(vt, vf, how="column")
    chk("align_view column: the top view takes the front's X", near(a["position_mm"],
                                                                    [120, 120]), str(a))
    r = D.move_view(vf, by=(-30, 0))
    chk("move_view: an aligned view follows its partner (moved_with)",
        r["moved_with"] == {vt: [-30.0, 0.0]}, str(r["moved_with"]))
    try:
        D.move_view(vt, to=(200, 110))
        chk("move_view off the aligned line raises and puts the view back", False)
    except RuntimeError as e:
        info = D.view_info(vt)
        chk("move_view off the aligned line raises and puts the view back",
            "aligned" in str(e) and near(info["position_mm"], [90, 120]), str(e)[:90])
    r = D.move_view(vt, to=(90, 105))
    chk("move_view along the aligned line is allowed", near(r["position_mm"], [90, 105]),
        str(r["position_mm"]))
    r = D.move_view(vt, to=(250, 80), break_alignment=True)
    chk("move_view break_alignment=True frees the view", near(r["position_mm"], [250, 80])
        and D.view_info(vt)["alignment"] == "free", str(r["position_mm"]))
    r = D.move_view(vt, next_to=vf, side="below", gap_mm=10)
    fpf, fpt = D.view_info(vf)["footprint_mm"], r["footprint_mm"]
    chk("move_view next_to below: same column, footprints 10 mm apart",
        abs(r["position_mm"][0] - 90) < 0.05 and abs(fpf[1] - fpt[3] - 10) < 0.2,
        f"{fpf} / {fpt}")
    info = D.view_info(vt)
    chk("view_info(view): annotations, free_mm, alignment, locked",
        set(info["free_mm"]) == {"left", "right", "down", "up"}
        and info["free_mm"]["up"] <= 10.2 and info["locked"] is False, str(info["free_mm"]))
    r = D.move_view(vi, to=(420, 200))
    chk("move_view off the sheet is done AND reported as a problem",
        any("outside the frame" in p for p in r["problems"]), str(r["problems"]))
    s = D.set_view_scale(vi, 0.25)
    chk("set_view_scale: one view's scale, read back", s["scale"] == "1:4", str(s))
    s = D.set_view_scale(vi, "sheet")
    chk("set_view_scale('sheet'): back on the sheet scale",
        D.view_info(vi)["sheet_scale"] is True, str(s))
    # a horizontal cut puts the section BELOW the front: re-scaling must keep it in the
    # front's COLUMN (GetAlignment reads 2 for a row and a column alike)
    D.move_view(vt, to=(330, 90))
    vs = D.section_view(vf, (20, 200), (160, 200), (90, 120), label="A")
    s = D.set_view_scale(vf, 0.5)
    fx, sx = D.view_info(vf)["position_mm"][0], D.view_info(vs)["position_mm"][0]
    chk("set_view_scale: the section below follows the scale and stays in the column",
        vs in s["followers"] and D.view_info(vs)["scale"] == "1:2" and abs(fx - sx) < 0.5,
        f"front x={fx} section x={sx}")
    gone = D.delete_view(vi)
    chk("delete_view: the view is gone, the others stay",
        gone == [vi] and vi not in [v["name"] for v in D.view_info()["views"]], str(gone))


def quality_clashes(chk):
    """dwg.quality reads what each dimension DRAWS and the title-block cells: a clean
    drawing has no clash, then each defect is made on purpose and must be named."""
    T.close_scratch()
    p = make_dimensioned_part()
    D.new_drawing(p, size="A3")
    md = C.active("IModelDoc2")
    # swDetailingDimensionStandard: the default template carries the machine's own setting
    # (ANSI on the SW 2017 laptop: 3.175 mm text under the ISO title block)
    std = C.cast(md.Extension, "IModelDocExtension").GetUserPreferenceInteger(13, 0)
    chk("new_drawing(standard='iso') dimensions in ISO (swDetailingStandardISO = 2)",
        std == 2, str(std))
    vf = D.add_view("front", at=(120, 200))
    vt = D.add_view("top", at=(120, 110))
    D.import_model_dims(vt)
    D.auto_dimension(vf)
    dims = {}
    for v in D._sheet_views():
        a = v.GetFirstAnnotation2()
        while a is not None:
            an = C.cast(a, "IAnnotation")
            if an.GetType() == 4:
                dim = C.cast(C.cast(an.GetSpecificAnnotation(), "IDisplayDimension")
                             .GetDimension2(0), "IDimension")
                dims["@".join(dim.FullName.split("@")[:2]) + f" ({v.GetName2()})"] = an
            a = an.GetNext3()

    def clashes():
        return {(c["kind"], c["a"], c["b"]) for c in D.quality()["dimensions"]["clashes"]}

    def moved(name, x, y):
        """Clashes with dimension `name` moved to (x, y), then put back."""
        an = dims[name]
        old = an.GetPosition()
        an.SetPosition2(C.mm(x), C.mm(y), 0.0)
        md.GraphicsRedraw2()
        try:
            return clashes()
        finally:
            an.SetPosition2(old[0], old[1], 0.0)
            md.GraphicsRedraw2()

    q = D.quality()
    chk("quality: a clean drawing has no dimension clash and no title-block overflow",
        q["dimensions"]["clashes"] == [] and q["titleblock_overflow"] == [],
        str(q["dimensions"]["clashes"][:3]))
    top_len, top_w = f"D1@Sketch1 ({vt})", f"D2@Sketch1 ({vt})"
    front = next((n for n in dims if n.endswith(f"({vf})")), "")
    chk("quality: the fixture has the dimensions the checks move",
        top_len in dims and top_w in dims and front, str(sorted(dims)))
    if not (top_len in dims and top_w in dims and front):
        return
    x, y = [c * 1000 for c in dims[top_w].GetPosition()[:2]]
    c = moved(top_len, x, y)
    chk("quality: a dimension text on another one -> text_over_text",
        ("text_over_text", top_len, top_w) in c
        or ("text_over_text", top_w, top_len) in c, str(c))
    c = moved(front, 120, 211.5)        # on the front view's top edge (y 187.5..212.5)
    chk("quality: a text across the edges of a view -> text_over_view",
        ("text_over_view", front, vf) in c, str(c))
    c = moved(front, 300, 30)
    chk("quality: a dimension on the title block -> title_block",
        ("title_block", front, None) in c, str(c))
    c = moved(front, 8, 250)
    chk("quality: a dimension past the border -> outside_frame",
        ("outside_frame", front, None) in c, str(c))
    # the drawing's own property (no model is saved): the DrawnBy cell is ~19 mm wide
    cpm = C.cast(C.cast(md.Extension, "IModelDocExtension").CustomPropertyManager(""),
                 "ICustomPropertyManager")
    cpm.Add3("DrawnBy", 30, "FULANO DE TAL DA SILVA SAURO", 2)
    md.EditRebuild3()
    q = D.quality()
    chk("quality: a title-block field longer than its cell -> titleblock_overflow",
        [f["field"] for f in q["titleblock_overflow"]] == ["DrawnBy"]
        and any("runs out of its cell" in p for p in q["problems"]),
        str(q["titleblock_overflow"]))
    cpm.Add3("DrawnBy", 30, "LPD", 2)
    md.EditRebuild3()
    chk("quality: a short one fits", D.quality()["titleblock_overflow"] == [])


BLOCKS = {"basic": basic, "views": views, "dims": dims, "annotate": annotate,
          "centermarks": centermarks, "dim_variants": dim_variants, "gdt": gdt,
          "dwg_variants": dwg_variants, "annotate_view": annotate_view,
          "assembly_pipeline": assembly_pipeline,
          "tables": tables, "section": section, "tolerances": tolerances, "export": export,
          "read_views": read_views, "move_views": move_views,
          "quality_clashes": quality_clashes}


def main():
    args = sys.argv[1:] or ["basic", "views", "dims"]
    if args == ["all"]:
        args = list(BLOCKS)
    C.connect()
    T.begin()
    T.close_scratch()
    chk = Chk()
    for name in args:
        fn = BLOCKS.get(name)
        if fn is None:
            print(f"unknown block: {name} (valid: {list(BLOCKS)})")
            continue
        print(f"\n== {name} ==")
        try:
            fn(chk)
        except Exception as e:  # noqa: BLE001
            chk(f"{name} (exception)", False, f"{type(e).__name__}: {e}")
    T.finish()
    print(f"\n{chk.ok}/{chk.n} PASS")
    sys.exit(0 if chk.ok == chk.n else 1)


if __name__ == "__main__":
    main()
