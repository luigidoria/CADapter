"""
verbs_dwg.py -- TECHNICAL DRAWING verbs compiled into bundles.

Port of `solidworks/sw_drawing.py`. The expensive knowledge that stays here: the
default do SW e ANSI/polegada e SetupSheet5 SOMA formato (carimbo duplo) -> o path
is clean and NewSheet4 with the standard .slddrt + deleting the ANSI sheet; dimensions
require explicit mm; import_model_dims requires AllViews=True; AutoBalloon breaks if
the BOM is already on the sheet; SetFrameSymbols v1 (GCS = an INT enum).
"""

from __future__ import annotations

from .bundles import BundleBuilder as B
from .bundles import mm
from .verbs_part import VerbError, _req

TPL_DRAWING = 10
SAVE_SILENT = 1
PAPER = {"A": 0, "Av": 1, "B": 2, "C": 3, "D": 4, "E": 5,
         "A4": 6, "A4v": 7, "A3": 8, "A2": 9, "A1": 10, "A0": 11, "custom": 12}
TEMPLATE = dict(PAPER, none=13)
STANDARDS = ("iso", "din", "jis", "gb", "bsi", "gost_sh1", "gost_sh2", "ansi")
VIEW_NAMES = {"front": "*Front", "back": "*Back", "left": "*Left", "right": "*Right",
              "top": "*Top", "bottom": "*Bottom", "iso": "*Isometric",
              "dimetric": "*Dimetric", "trimetric": "*Trimetric"}
# swInsertAnnotation_e: 'dimensions' needs MARKED (32768) AND UNMARKED (524288)
ANN = {"dimensions": 8 | 32768 | 524288, "datums": 2, "gtols": 32, "notes": 64,
       "holes": 1048576, "axes": 512, "instance_counts": 16}
BOM_TYPE = {"top": 2, "parts": 1, "indented": 3}
BALLOON_LAYOUT = {"square": 1, "circle": 2, "top": 3, "bottom": 4, "right": 5, "left": 6}
GTOL_SYM = {"straightness": 14, "flatness": 15, "circularity": 16, "cylindricity": 17,
            "profile_line": 18, "profile_surface": 19, "angularity": 20,
            "perpendicularity": 21, "parallelism": 22, "position": 23,
            "concentricity": 24, "circular_runout": 25, "total_runout": 26,
            "symmetry": 13}
GTOL_MC = {"none": 0, "mmc": 1, "rfs": 2, "lmc": 3}
SF_TYPE = {"basic": 0, "machined": 1, "dont_machine": 2}
CM_SINGLE, CM_HOLE, CM_FILLETS, CM_SLOTS = 2, 1, 2, 4
ANN_CENTERMARK, ANN_NOTE = 13, 6
PREF_UNIT_SYSTEM, PREF_UNITS_LINEAR, PREF_UNITS_DECIMALS = 263, 47, 49
UNITSYS_MMGS, UNITSYS_IPS, LENGTH_MM = 5, 3, 0


def _units_ops(b: B, units: str, decimals: int = 2) -> None:
    """The default template is inches -> without this the dimensions come out in inch."""
    if units == "mm":
        b.call("ext", "SetUserPreferenceInteger", PREF_UNIT_SYSTEM, 0, UNITSYS_MMGS)
        b.call("ext", "SetUserPreferenceInteger", PREF_UNITS_LINEAR, 0, LENGTH_MM)
    elif units == "in":
        b.call("ext", "SetUserPreferenceInteger", PREF_UNIT_SYSTEM, 0, UNITSYS_IPS)
    else:
        raise VerbError(f"units '{units}' invalid ('mm'|'in')")
    b.call("ext", "SetUserPreferenceInteger", PREF_UNITS_DECIMALS, 0, int(decimals))


# -- sheet / standard --------------------------------------------------------
def new_drawing(p: dict) -> dict:
    """A sheet in the STANDARD (titleblock + border + units). Default: A3, ISO, pt-BR, mm, 1st angle."""
    size = str(p.get("size", "A3"))
    std = str(p.get("standard", "iso")).lower()
    if std not in STANDARDS:
        raise VerbError(f"standard '{std}' invalid ({list(STANDARDS)})")
    units = str(p.get("units", "mm"))
    first = str(p.get("projection", "first")) == "first"
    scale = p.get("scale") or [1, 1]

    b = B("dwg.new_drawing")
    tpl = b.call("app", "GetUserPreferenceStringValue", TPL_DRAWING)
    b.guard(tpl, "nonempty", "no default drawing template configured in SolidWorks")
    doc = b.call("app", "NewDocument", tpl, PAPER.get(size, 8), 0.0, 0.0)
    b.guard(doc, "not_null", "NewDocument (drawing) failed")

    if std == "ansi":
        sheet = b.call("dwg", "GetCurrentSheet", cast="ISheet")
        name = b.call(sheet, "GetName")
        ok = b.call("dwg", "SetupSheet5", name, PAPER.get(size, 8), TEMPLATE.get(size, 8),
                    float(scale[0]), float(scale[1]), first, "", 0.0, 0.0, "", False)
        b.guard(ok, "truthy", f"SetupSheet5 failed (size={size})")
    else:
        # NewSheet4 is born WITHOUT the template ANSI titleblock -> load the standard and drop the old sheet
        sfp = b.helper("sheetformat_path", size=size, standard=std,
                       language=str(p.get("language", "pt")))
        novo = f"{std.upper()}_{size.upper()}"
        ok = b.call("dwg", "NewSheet4", novo, PAPER.get(size, 8), TEMPLATE["custom"],
                    float(scale[0]), float(scale[1]), first, sfp,
                    0.0, 0.0, "", 0.0, 0.0, 0.0, 0.0, 0, 0)
        b.guard(ok, "truthy", f"NewSheet4 failed (size={size}, standard={std})")
        b.call("dwg", "ActivateSheet", novo)
        b.helper("delete_other_sheets", keep=novo)
    _units_ops(b, units, int(p.get("decimals", 2)))
    return b.build(b.call("doc", "GetTitle"), p)


def set_units(p: dict) -> dict:
    b = B("dwg.set_units")
    _units_ops(b, str(p.get("units", "mm")), int(p.get("decimals", 2)))
    return b.build(str(p.get("units", "mm")), p)


def sheet_scale(p: dict) -> dict:
    """GOTCHA: use ISheet.SetScale -- SetupSheet5 with paperSize=0 RESETS the sheet."""
    num, den = float(p.get("num", 1)), float(p.get("den", 1))
    b = B("dwg.sheet_scale")
    sheet = b.call("dwg", "GetCurrentSheet", cast="ISheet")
    b.guard(sheet, "not_null", "no active sheet")
    b.call(sheet, "SetScale", num, den, True, True)
    b.call("doc", "EditRebuild3")
    return b.build([num, den], p)


# ── views ────────────────────────────────────────────────────────────────────
def add_view(p: dict) -> dict:
    kind = str(p.get("kind", "front")).lower()
    if kind not in VIEW_NAMES:
        raise VerbError(f"kind '{kind}' invalid ({sorted(VIEW_NAMES)})")
    model = str(_req(p, "model_path"))
    at = p.get("at") or [150, 150]
    b = B("dwg.add_view")
    v = b.call("dwg", "CreateDrawViewFromModelView3", model, VIEW_NAMES[kind],
               mm(at[0]), mm(at[1]), 0.0, cast="IView")
    b.guard(v, "not_null",
            f"add_view '{kind}' failed (is the model SAVED and does the path exist? {model})")
    if float(p.get("scale", 0) or 0) > 0:
        b.set(v, "ScaleDecimal", float(p["scale"]))
    return b.build(b.call(v, "GetName2"), p)


def view_names(p: dict) -> dict:
    b = B("dwg.view_names")
    return b.build(b.helper("view_names"), p)


def sheet_info(p: dict) -> dict:
    """The SHEET measured: its scale, and per view the name, the position, the scale
    and how many dimensions and annotations it has.

    IT IS THE DRAWING ADDRESS, and the reason it is a gate. In a drawing document
    the MODEL does not change: `inspect.mass` returns volume 0.0 on every step
    (measured 2026-08-19), so the part criterion -- volume and feature tree -- measures
    nothing on a sheet. What changes is the number of VIEWS and of DIMENSIONS, and
    until now there was only `dwg.view_names`: you could list the views and could not
    count anything inside them. A check that pretends to check is worse than none.

        dwg_sheet_info()
        -> {"sheet": "Draw1 - ISO_A3", "scale": [1, 2], "n_views": 3,
            "total_dims": 11, "total_annotations": 14,
            "views": [{"name": "Drawing View1", "at_mm": [110, 200],
                        "scale": [1, 2], "dims": 5, "annotations": 6}, ...]}
    """
    b = B("dwg.sheet_info")
    return b.build(b.helper("sheet_info"), p)


def view_center(p: dict) -> dict:
    """(x,y) mm of the view center -- use it to aim at edges in dimension/gtol/datum."""
    b = B("dwg.view_center")
    return b.build(b.helper("view_center", view=str(_req(p, "view_name"))), p)


def section_view(p: dict) -> dict:
    parent = str(_req(p, "parent_view"))
    p1, p2, at = _req(p, "p1"), _req(p, "p2"), _req(p, "at")
    b = B("dwg.section_view")
    b.call("dwg", "ActivateView", parent)
    seg = b.call("sm", "CreateLine", mm(p1[0]), mm(p1[1]), 0.0,
                 mm(p2[0]), mm(p2[1]), 0.0, cast="ISketchSegment")
    b.guard(seg, "not_null", "section_view: did not draw the section line")
    b.helper("select_sketch_entities", entities=[seg])
    v = b.call("dwg", "CreateSectionViewAt5", mm(at[0]), mm(at[1]), 0.0,
               str(p.get("label", "A")), 0 if p.get("aligned", True) else 1, None, 0.0,
               cast="IView")
    b.guard(v, "not_null",
            "section_view failed (does the section line cross the parent view geometry?)")
    b.call("doc", "ClearSelection2", True)
    return b.build(b.call(v, "GetName2"), p)


def set_exploded(p: dict) -> dict:
    b = B("dwg.set_exploded")
    v = b.helper("activate_view", view=str(_req(p, "view_name")))
    b.guard(v, "not_null", f"view '{p.get('view_name')}' not found")
    b.call(v, "ShowExploded", bool(p.get("exploded", True)))
    b.call("doc", "EditRebuild3")
    return b.build(b.call(v, "IsExploded"), p)


# ── dimensioning ───────────────────────────────────────────────────────────────────
def import_model_dims(p: dict) -> dict:
    """Dimensioning FROM THE MODEL (honest). GOTCHA: requires AllViews=True (with False
    SW aims at a single view that IT picks and returns 0)."""
    types = p.get("types") or ["dimensions", "holes"]
    mask = 0
    for t in types:
        if t not in ANN:
            raise VerbError(f"type '{t}' invalid ({sorted(ANN)})")
        mask |= ANN[t]
    b = B("dwg.import_model_dims")
    n = b.helper("import_model_dims", mask=mask,
                 all_views=bool(p.get("all_views", True)),
                 view=str(p.get("view_name", "")))
    return b.build(n, p)


def dedupe_dimensions(p: dict) -> dict:
    """One dimension each (the standard): removes the ones repeated across views."""
    b = B("dwg.dedupe_dimensions")
    return b.build(b.helper("dedupe_dimensions"), p)


def dimension(p: dict) -> dict:
    """A REFERENCE dimension straight on the drawing: picks PROJECTED geometry by coordinate."""
    picks = _req(p, "picks")
    place_at = _req(p, "place_at")
    orient = str(p.get("orient", "aligned")).lower()
    placer = {"aligned": "AddDimension2", "horizontal": "AddHorizontalDimension2",
              "vertical": "AddVerticalDimension2"}.get(orient)
    if placer is None:
        raise VerbError(f"orient '{orient}' invalid (aligned|horizontal|vertical)")
    b = B("dwg.dimension")
    b.call("dwg", "ActivateView", str(_req(p, "view_name")))
    b.call("doc", "ClearSelection2", True)
    for i, (px, py) in enumerate(picks):
        ok = b.helper("pick_edge", x=px, y=py, append=i > 0)
        b.guard(ok, "truthy",
                f"no edge/vertex found at ({px}, {py}) -- aim from view_center")
    dim = b.call("doc", placer, mm(place_at[0]), mm(place_at[1]), 0.0,
                 cast="IDisplayDimension")
    b.guard(dim, "not_null", "dimension: no dimension created (did the picks miss the geometry?)")
    d = b.call(dim, "GetDimension2", 0, cast="IDimension")
    b.call("doc", "ClearSelection2", True)
    return b.build({"$ref": b.get(d, "SystemValue").lstrip("$"), "scale": 1000.0}, p)


def center_marks(p: dict) -> dict:
    """GOTCHA: UseDocumentDefaults=False e no-op silencioso; GetCenterMarkCount mente."""
    b = B("dwg.center_marks")
    v = b.helper("activate_view", view=str(_req(p, "view_name")))
    b.guard(v, "not_null", f"view '{p.get('view_name')}' not found")
    opt = ((CM_HOLE if p.get("holes", True) else 0)
           | (CM_FILLETS if p.get("fillets", False) else 0)
           | (CM_SLOTS if p.get("slots", True) else 0))
    slots = bool(p.get("slots", True))
    b.call(v, "AutoInsertCenterMarks2", CM_SINGLE, opt, slots, slots, True,
           0.0, 0.0, True, True, 0.0)
    b.call("doc", "EditRebuild3")
    return b.build(b.helper("count_annotations", view=str(p["view_name"]),
                            type=ANN_CENTERMARK), p)


# ── GD&T and finish ─────────────────────────────────────────────────────────
def datum(p: dict) -> dict:
    at = _req(p, "edge_at")
    b = B("dwg.datum")
    b.call("dwg", "ActivateView", str(_req(p, "view_name")))
    ok = b.helper("pick_edge", x=at[0], y=at[1])
    b.guard(ok, "truthy", f"datum: no edge found at {at} (aim at the surface)")
    tag = b.call("doc", "InsertDatumTag2", cast="IDatumTag")
    b.guard(tag, "not_null", "InsertDatumTag2 returned None (was an edge selected?)")
    b.call(tag, "SetLabel", str(p.get("label", "A")))
    if p.get("place_at"):
        ann = b.call(tag, "GetAnnotation", cast="IAnnotation")
        b.call(ann, "SetPosition", mm(p["place_at"][0]), mm(p["place_at"][1]), 0.0)
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(b.call(tag, "GetLabel"), p)


def gtol(p: dict) -> dict:
    """GOTCHA: SetFrameSymbols v1 (GCS = an INT enum); the v2 wants a gtol-font string."""
    symbol = str(_req(p, "symbol"))
    if symbol not in GTOL_SYM:
        raise VerbError(f"symbol '{symbol}' invalid ({sorted(GTOL_SYM)})")
    mc = str(p.get("mc", "none"))
    if mc not in GTOL_MC:
        raise VerbError(f"mc '{mc}' invalid ({sorted(GTOL_MC)})")
    at = _req(p, "edge_at")
    ds = list(p.get("datums") or []) + ["", "", ""]
    tol = str(_req(p, "tolerance"))

    b = B("dwg.gtol")
    b.call("dwg", "ActivateView", str(_req(p, "view_name")))
    ok = b.helper("pick_edge", x=at[0], y=at[1])
    b.guard(ok, "truthy", f"gtol: no edge found at {at} (aim at the surface)")
    g = b.call("doc", "InsertGtol", cast="IGtol")
    b.guard(g, "not_null", "InsertGtol returned None (was an edge selected?)")
    b.call(g, "SetFrameValues2", 1, tol, "", ds[0], ds[1], ds[2])
    b.call(g, "SetFrameSymbols", 1, GTOL_SYM[symbol], bool(p.get("diameter", False)),
           GTOL_MC[mc], False, 0, 0, 0, 0)
    if p.get("place_at"):
        b.call(g, "SetPosition", mm(p["place_at"][0]), mm(p["place_at"][1]), 0.0)
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(b.call(g, "GetFrameValues", 1), p)


def surface_finish(p: dict) -> dict:
    kind = str(p.get("kind", "machined"))
    if kind not in SF_TYPE:
        raise VerbError(f"kind '{kind}' invalid ({sorted(SF_TYPE)})")
    at = _req(p, "edge_at")
    ra = str(p.get("ra", 3.2))
    off = float(p.get("text_offset", 8.0))
    b = B("dwg.surface_finish")
    b.call("dwg", "ActivateView", str(_req(p, "view_name")))
    ok = b.helper("pick_edge", x=at[0], y=at[1])
    b.guard(ok, "truthy", f"surface_finish: no edge found at {at}")
    sym = b.call("ext", "InsertSurfaceFinishSymbol3", SF_TYPE[kind], 0,
                 mm(at[0]), mm(at[1] + off), 0.0, 0, 0, "", "", "", "", ra, "", "",
                 cast="ISFSymbol")
    b.guard(sym, "not_null", "InsertSurfaceFinishSymbol3 returned None")
    ann = b.call(sym, "GetAnnotation", cast="IAnnotation")
    b.call(ann, "SetPosition", mm(at[0]), mm(at[1] + off), 0.0)
    b.call("doc", "ClearSelection2", True)
    return b.build(f"Ra {ra}", p)


# ── notes, table, speech bubbles, stamp ────────────────────────────────────────
def annotate(p: dict) -> dict:
    at = p.get("at") or [50, 50]
    b = B("dwg.annotate")
    if p.get("view_name"):
        b.call("dwg", "ActivateView", str(p["view_name"]))
    note = b.call("doc", "InsertNote", str(_req(p, "text")), cast="INote")
    b.guard(note, "not_null", "InsertNote returned None")
    ann = b.call(note, "GetAnnotation", cast="IAnnotation")
    b.call(ann, "SetPosition", mm(at[0]), mm(at[1]), 0.0)
    b.call("doc", "ClearSelection2", True)
    return b.build(str(p["text"]), p)


def balloons(p: dict) -> dict:
    """GOTCHAS: AutoBalloon5 is a no-op; a BOM ALREADY on the sheet BREAKS AutoBalloon
    (balloon BEFORE the BOM); count through an annotation walk."""
    layout = str(p.get("layout", "circle"))
    if layout not in BALLOON_LAYOUT:
        raise VerbError(f"layout '{layout}' invalid ({sorted(BALLOON_LAYOUT)})")
    view = str(_req(p, "view_name"))
    b = B("dwg.balloons")
    b.call("dwg", "ActivateView", view)
    b.call("doc", "ClearSelection2", True)
    ok = b.call("ext", "SelectByID2", view, "DRAWINGVIEW", 0, 0, 0, False, 0, None, 0)
    b.guard(ok, "truthy", f"balloons: did not select view '{view}'")
    b.call("dwg", "AutoBalloon", BALLOON_LAYOUT[layout])
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(b.helper("count_annotations", view=view, type=ANN_NOTE), p)


def table(p: dict) -> dict:
    """A BOM anchored to an ASSEMBLY view. Balloon BEFORE inserting the BOM."""
    at = p.get("at") or [300, 200]
    b = B("dwg.table")
    v = b.helper("activate_view", view=str(_req(p, "view_name")))
    b.guard(v, "not_null", f"view '{p.get('view_name')}' not found")
    tbl = b.call(v, "InsertBomTable4", False, mm(at[0]), mm(at[1]),
                 int(p.get("anchor", 1)), BOM_TYPE.get(str(p.get("bom_type", "top")), 2),
                 "", "", False, 0, False)
    b.guard(tbl, "not_null", "table(bom) failed (does the view reference a SAVED assembly?)")
    return b.build(str(p["view_name"]), p)


def fill_titleblock(p: dict) -> dict:
    """The DRAWING $PRP fields. TITLE/MATERIAL/FINISH come from the MODEL ($PRPSHEET)
    -> set them on the part with part.set_property before creating the views."""
    campos = {"DrawnBy": p.get("drawn_by", ""), "CheckedBy": p.get("checked_by", ""),
              "DrawnDate": p.get("drawn_date", ""), "CheckedDate": p.get("checked_date", ""),
              "Revision": p.get("revision", ""), "Weight": p.get("weight", ""),
              "COMPANYNAME": p.get("company", "")}
    aplicados = {k: v for k, v in campos.items() if v}
    if not aplicados:
        raise VerbError("nenhum campo informado (drawn_by, checked_by, revision, ...)")
    b = B("dwg.fill_titleblock")
    cpm = b.call("ext", "CustomPropertyManager", "", cast="ICustomPropertyManager")
    b.guard(cpm, "not_null", "CustomPropertyManager returned Nothing")
    for k, val in aplicados.items():
        b.call(cpm, "Add3", k, 30, str(val), 1)
    b.call("doc", "EditRebuild3")
    return b.build(aplicados, p)


# ── out ─────────────────────────────────────────────────────────────────────
def save(p: dict) -> dict:
    path = str(p.get("path", "") or "")
    b = B("dwg.save")
    if path:
        b.helper("close_other_doc", path=path)
        ok = b.call("doc", "SaveAs", path)
        b.guard(ok, "truthy", f"SaveAs (drawing) failed: {path}")
        return b.build(path, p)
    b.call("doc", "Save3", SAVE_SILENT)
    return b.build(b.call("doc", "GetPathName"), p)


def export(p: dict) -> dict:
    """Format from the extension (.pdf/.dxf/.dwg)."""
    path = str(_req(p, "path"))
    b = B("dwg.export")
    b.helper("close_other_doc", path=path)
    ok = b.call("doc", "SaveAs", path)
    b.guard(ok, "truthy", f"export (drawing) failed: {path}")
    return b.build(path, p)


VERBS = {
    "dwg.new_drawing": new_drawing, "dwg.set_units": set_units,
    "dwg.sheet_scale": sheet_scale, "dwg.add_view": add_view,
    "dwg.view_names": view_names, "dwg.view_center": view_center,
    "dwg.sheet_info": sheet_info,
    "dwg.section_view": section_view, "dwg.set_exploded": set_exploded,
    "dwg.import_model_dims": import_model_dims,
    "dwg.dedupe_dimensions": dedupe_dimensions, "dwg.dimension": dimension,
    "dwg.center_marks": center_marks, "dwg.datum": datum, "dwg.gtol": gtol,
    "dwg.surface_finish": surface_finish, "dwg.annotate": annotate,
    "dwg.balloons": balloons, "dwg.table": table,
    "dwg.fill_titleblock": fill_titleblock,
    "dwg.save": save, "dwg.export": export,
}
