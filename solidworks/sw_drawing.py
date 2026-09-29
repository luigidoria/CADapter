# -*- coding: utf-8 -*-
"""
sw_drawing -- layer 1: TECHNICAL DRAWING verbs (dwg.*). See solidworks/README.md.

Closes the DRAWING family: the STANDARD becomes a parameter (ISO/ANSI sheet = format +
titleblock; 1st/3rd angle projection), then views, dimensioning FROM THE MODEL (an honest
dimension, it came from the part), annotations, tables (BOM) and export (PDF/DXF). As in
the rest of the package (see sw_parts): early binding via sw_com; many methods return
None/a tuple even when they succeed -> take the entity from the tree/return and re-cast.

Units: mm at the boundary (SHEET positions in mm), converted by sw_com.mm.

The `DrawingDoc` is the SAME doc as the `ModelDoc2` (cast per interface). Standard named
views ('*Front' etc.) avoid depending on the projection angle to place the 3 views.
"""
import glob
import math
import os
from typing import Literal

from . import sw_com as C

# ── enums (exact values from the swconst 2017 typelib; see the probe in the 2026-07-23
#    session) ──
# swDwgPaperSizes_e / swDwgTemplates_e: the standard SIZES share the same int in both
# enums (A=0..E=5, A4=6, A4v=7, A3=8, A2=9, A1=10, A0=11). 'custom'/'none' diverge.
PAPER = {"A": 0, "Av": 1, "B": 2, "C": 3, "D": 4, "E": 5,
         "A4": 6, "A4v": 7, "A3": 8, "A2": 9, "A1": 10, "A0": 11, "custom": 12}
# swDwgTemplates_e: SetupSheet5's TemplateIn -> loads SW's default SHEET FORMAT (border +
# titleblock) for that size = the "standard". 'none'=13 (no format).
TEMPLATE = {"A": 0, "Av": 1, "B": 2, "C": 3, "D": 4, "E": 5,
            "A4": 6, "A4v": 7, "A3": 8, "A2": 9, "A1": 10, "A0": 11,
            "custom": 12, "none": 13}

# swInsertAnnotation_e (bitmask for InsertModelAnnotations3's Types param). 'dimensions'
# brings in dimensions MARKED (32768) AND UNMARKED (524288) for drawing -- feature
# dimensions (e.g. an extrude depth) are not marked by default; without 524288 it returns 0.
_ANN = {"dimensions": 8 | 32768 | 524288, "datums": 2, "gtols": 32, "notes": 64,
        "holes": 1048576, "axes": 512, "instance_counts": 16}
# swImportModelItemsSource_e
FROM_ENTIRE_MODEL = 0
# swBomType_e / swBOMConfigurationAnchorType_e
_BOM = {"top": 2, "parts": 1, "indented": 3}
ANCHOR_TOPLEFT, ANCHOR_TOPRIGHT, ANCHOR_BOTLEFT, ANCHOR_BOTRIGHT = 1, 2, 3, 4
# swCreateSectionViewAtOptions_e
SECT_NOT_ALIGNED = 1
# units (swUserPreferenceIntegerValue_e -> value): SW's DEFAULT drawing template is
# ANSI/inch; for a metric standard we set the drawing doc to mm explicitly.
_PREF_UNIT_SYSTEM, _PREF_UNITS_LINEAR, _PREF_UNITS_DECIMALS = 263, 47, 49
_PREF_MASS_UNIT = 259   # swUnitsMassPropMass
_UNITSYS_MMGS, _UNITSYS_IPS = 5, 3
_LENGTH_MM = 0  # swLengthUnit_e.swMM

# Standard named views (the string for CreateDrawViewFromModelView3).
_VIEW_NAMES = {"front": "*Front", "back": "*Back", "left": "*Left", "right": "*Right",
               "top": "*Top", "bottom": "*Bottom", "iso": "*Isometric",
               "dimetric": "*Dimetric", "trimetric": "*Trimetric"}

# Source model of the views (remembered between calls: the 1st view pins it, the rest
# inherit).
_last_model = ""


# ── helpers ───────────────────────────────────────────────────────────────────
def _dwg():
    """The active IDrawingDoc (the same doc as IModelDoc2, cast)."""
    return C.active("IDrawingDoc")


def _md():
    return C.active("IModelDoc2")


def _sheet_name():
    """Name of the current sheet (needed for SetupSheet5)."""
    sh = C.cast(_dwg().GetCurrentSheet(), "ISheet")
    return sh.GetName()


# ── sheet format by STANDARD (the .slddrt files SW installs) ─────────────────
# SW's DEFAULT .drwdot template embeds an ANSI-A/inch titleblock that the API does NOT
# remove (SetupSheet5 ADDS a format -> a double titleblock). The clean path (discovered
# live): NewSheet4 is born WITHOUT that titleblock -> load the standard's .slddrt onto a
# new sheet and delete the original ANSI sheet (DeleteSelection2). See the `drawing-*`
# recipes.
_STANDARDS = ("iso", "din", "jis", "gb", "bsi", "gost_sh1", "gost_sh2", "ansi")


def _sheetformat_path(size: str, standard: str, language: str) -> str:
    """Resolve the sheet-format .slddrt (standard) installed by SW. Derives the root from
    the default template (independent of the SW version). `language`='pt' prefers the
    Brazilian-Portuguese titleblock, falling back to english. Raises if not found."""
    tpl = C.app().GetUserPreferenceStringValue(C.TPL_DRAWING)
    base = os.path.dirname(os.path.dirname(tpl))          # ...\SOLIDWORKS <ver>
    langroot = os.path.join(base, "lang")
    langs = ["Portuguese-Brazilian", "english"] if language == "pt" else ["english"]
    fname = f"{size.rstrip('v').lower()} - {standard.lower()}.slddrt"
    tried = []
    for lg in langs:
        p = os.path.join(langroot, lg, "sheetformat", fname)
        tried.append(p)
        if os.path.exists(p):
            return p
    raise RuntimeError(
        f"sheet format not found for size={size} standard={standard}: {tried}")


# ── sheet / standard ──────────────────────────────────────────────────────────
def set_units(units: str = "mm", *, decimals: int = 2) -> str:
    """Set the unit system of the active DRAWING ('mm' = metric MMGS | 'in' = IPS inch).
    SW's DEFAULT drawing template is ANSI/inch -> without this, imported dimensions come
    out in inches. Returns the unit that was applied."""
    ext = C.cast(_md().Extension, "IModelDocExtension")
    if units == "mm":
        ext.SetUserPreferenceInteger(_PREF_UNIT_SYSTEM, 0, _UNITSYS_MMGS)
        ext.SetUserPreferenceInteger(_PREF_UNITS_LINEAR, 0, _LENGTH_MM)
    elif units == "in":
        ext.SetUserPreferenceInteger(_PREF_UNIT_SYSTEM, 0, _UNITSYS_IPS)
    else:
        raise ValueError(f"units '{units}' is invalid ('mm'|'in').")
    ext.SetUserPreferenceInteger(_PREF_UNITS_DECIMALS, 0, int(decimals))
    return units


def _delete_other_sheets(dwg, keep: str):
    """Delete every sheet != keep (the only method that works on SW 2017: select the sheet
    as 'SHEET' and DeleteSelection2 -- EditDelete does NOT remove a sheet)."""
    md = _md()
    for s in list(dwg.GetSheetNames()):
        if s == keep:
            continue
        md.ClearSelection2(True)
        ext = C.cast(md.Extension, "IModelDocExtension")
        if ext.SelectByID2(s, "SHEET", 0, 0, 0, False, 0, None, 0):
            ext.DeleteSelection2(0)


def new_drawing(model_path: str = "", *, size: str = "A3", projection: str = "first",
                units: str = "mm", standard: str = "iso", language: str = "pt",
                scale=(1, 1), decimals: int = 2) -> str:
    """Create a new drawing with a SHEET in the requested standard (titleblock + border +
    units).
      size       -> 'A4'|'A3'|'A2'|'A1'|'A0'|'A'..'E' (suffix 'v'=portrait).
      projection -> 'first' (ISO/1st angle) | 'third' (ANSI/3rd angle).
      units      -> 'mm' (metric, DEFAULT) | 'in'. SW's default template is inches.
      standard   -> titleblock AND dimensioning standard: 'iso'(DEFAULT)|'din'|'jis'|'gb'|
                    'bsi'|'gost_sh1'|'gost_sh2'|'ansi'. For 'ansi' it uses the template's
                    native format.
      language   -> 'pt' (Brazilian-Portuguese titleblock, DEFAULT) | 'en'.
      scale      -> (num, den) of the sheet scale (1:1 by default).
      decimals   -> decimal places of the dimensions (2 by default).
      model_path -> if given, is remembered as the source model of the next views.
    For standards != ANSI: it loads the .slddrt format onto a NEW (clean) sheet and
    deletes the ANSI sheet the template embeds (otherwise you get TWO titleblocks).
    Returns the title. Does NOT save (the loop keeps the user's file in-session)."""
    global _last_model
    std = standard.lower()
    if std not in _STANDARDS:
        raise ValueError(f"standard '{standard}' is invalid ({list(_STANDARDS)}).")
    sw = C.app()
    tpl = sw.GetUserPreferenceStringValue(C.TPL_DRAWING)  # default .drwdot template
    if not tpl:
        raise RuntimeError("no default drawing template configured in SW.")
    if sw.NewDocument(tpl, PAPER.get(size, 8), 0.0, 0.0) is None:
        raise RuntimeError("NewDocument (drawing) failed.")
    dwg = _dwg()
    if std == "ansi":
        # the template's native format (already ANSI); only adjust paper/scale/angle.
        if not dwg.SetupSheet5(_sheet_name(), PAPER.get(size, 8), TEMPLATE[size],
                               float(scale[0]), float(scale[1]), projection == "first",
                               "", 0.0, 0.0, "", False):
            raise RuntimeError(f"SetupSheet5 failed (size={size}).")
    else:
        sfp = _sheetformat_path(size, std, language)
        newname = f"{std.upper()}_{size.upper()}"
        ok = dwg.NewSheet4(newname, PAPER.get(size, 8), TEMPLATE["custom"],
                           float(scale[0]), float(scale[1]), projection == "first",
                           sfp, 0.0, 0.0, "", 0.0, 0.0, 0.0, 0.0, 0, 0)
        if not ok:
            raise RuntimeError(f"NewSheet4 failed (size={size}, standard={std}).")
        dwg.ActivateSheet(newname)
        _delete_other_sheets(dwg, keep=newname)   # removes the embedded ANSI titleblock
    # the DIMENSIONING standard is a document option, not part of the sheet format: the
    # format above only draws the border and title block, and the default template's
    # standard is whatever that machine was configured with. MEASURED 2026-09-28: on the
    # SW 2017 test laptop the template dimensioned in ANSI (3.175 mm text, vertical
    # dimensions written horizontally inside the line) under an ISO title block; forcing
    # ANSI on SW 2026 reproduces it exactly. Set before set_units: switching the standard
    # resets the decimal places ('120' became '120.00').
    _set_drafting_standard(std)
    set_units(units, decimals=decimals)
    hide_reference_geometry()
    if model_path:
        _last_model = model_path
    return _md().GetTitle()


# swUserPreferenceIntegerValue_e.swDetailingDimensionStandard -> swDetailingStandard_e
_PREF_DIM_STANDARD = 13
_DIM_STANDARD = {"ansi": 1, "iso": 2, "din": 3, "jis": 4, "bsi": 5, "gost_sh1": 6,
                 "gost_sh2": 6, "gb": 7}


def _set_drafting_standard(std: str):
    ext = C.cast(_md().Extension, "IModelDocExtension")
    want = _DIM_STANDARD[std]
    ext.SetUserPreferenceInteger(_PREF_DIM_STANDARD, 0, want)
    got = ext.GetUserPreferenceInteger(_PREF_DIM_STANDARD, 0)
    if got != want:
        raise RuntimeError(f"new_drawing: the dimensioning standard reads {got} after "
                           f"asking {want} ({std}).")


# swUserPreferenceToggle_e: the reference items a model view would otherwise show (the
# blue origin triads and sketch lines seen on every early PDF), and "hide all types"
_HIDE_TYPES = (4, 5, 6, 13, 196, 219, 664)   # axes, planes, origins, csys, sketches,
_HIDE_ALL_TYPES = 198                          # reference points, sketch planes


def hide_reference_geometry() -> bool:
    """Hide origins, planes, axes, sketches and reference points in every view of the
    active drawing (View > Hide All Types). A fabrication drawing shows the part, not
    the modelling scaffolding. new_drawing already calls it. Returns True."""
    ext = C.cast(_md().Extension, "IModelDocExtension")
    for pref in _HIDE_TYPES:
        ext.SetUserPreferenceToggle(pref, 0, False)
    ext.SetUserPreferenceToggle(_HIDE_ALL_TYPES, 0, True)
    return True


def sheet_scale(num: float = 1, den: float = 1) -> tuple:
    """Readjust the SHEET scale (num:den). Returns (num, den).
    GOTCHA: use `ISheet.SetScale` -- SetupSheet5 with paperSize=0/custom template RESETS
    the sheet and does NOT apply the scale (leaves it 1:1)."""
    sh = C.cast(_dwg().GetCurrentSheet(), "ISheet")
    sh.SetScale(float(num), float(den), False, False)
    return (num, den)


# ── views ─────────────────────────────────────────────────────────────────────
def _resolve_model(model_path: str) -> str:
    global _last_model
    m = model_path or _last_model
    if not m:
        raise RuntimeError(
            "no source model: pass model_path (path of the SAVED part/assembly) on the "
            "first view, or in new_drawing.")
    _last_model = m
    return m


_DISPLAY = {"hidden_visible": 1, "hidden_removed": 2, "wireframe": 0, "shaded": 3,
            "shaded_edges": 7}   # swDisplayMode_e


def add_view(kind: str = "front", at=(150, 150), *, model_path: str = "",
             scale: float = 0.0, display: str = "") -> str:
    """Insert a standard NAMED view of the model onto the sheet.
      kind  -> 'front'|'back'|'left'|'right'|'top'|'bottom'|'iso'|'dimetric'|'trimetric'.
      at    -> (x, y) on the SHEET in mm (the sheet's bottom-left corner = origin).
                Use arrange() afterwards instead of computing positions by hand.
      model_path -> path of the SAVED part/assembly (mandatory on the 1st view; inherited
                    afterwards).
      scale -> view scale (0 = inherit the sheet's). E.g. 0.5 = 1:2.
      display -> 'hidden_visible' (dashed hidden edges; DEFAULT for orthographic views of a
                 part, the holes and bores show through) | 'hidden_removed' (DEFAULT for
                 pictorial views and assemblies) | 'wireframe' | 'shaded' | 'shaded_edges'.
    Returns the view name (to dimension it / attach a section later)."""
    kk = kind.lower()
    if kk not in _VIEW_NAMES:
        raise ValueError(f"kind '{kind}' is invalid ({sorted(_VIEW_NAMES)}).")
    model = _resolve_model(model_path)
    dwg = _dwg()
    v = dwg.CreateDrawViewFromModelView3(model, _VIEW_NAMES[kk],
                                         C.mm(at[0]), C.mm(at[1]), 0.0)
    if v is None:
        raise RuntimeError(
            f"add_view '{kind}' failed (model did not open / invalid path?): {model}")
    v = C.cast(v, "IView")
    if scale > 0:
        # the view's own scale (detached from the sheet). ScaleDecimal as a decimal fraction.
        v.ScaleDecimal = scale
    if not display:
        pictorial = kk in ("iso", "dimetric", "trimetric")
        display = "hidden_removed" if pictorial or model.lower().endswith(".sldasm") \
            else "hidden_visible"
    if display not in _DISPLAY:
        raise ValueError(f"display '{display}' is invalid ({sorted(_DISPLAY)}).")
    v.SetDisplayMode3(False, _DISPLAY[display], False, True)
    return v.GetName2()


def flat_pattern_view(at=(150, 150), *, model_path: str = "",
                      config: str = "Default", hide_bend_lines: bool = False,
                      flip: bool = False, bend_notes: bool = True) -> str:
    """View of a sheet metal part's FLAT PATTERN (the drawing that goes to the cutter).

    `bend_notes` turns on the bend notes (UP/DOWN + angle) over each bend line.
    Returns the view name. The part must be SAVED (like every drawing view).

    Sibling verb to sw_sheetmetal.export_flat: that one delivers the raw DXF geometry,
    this one delivers the DIMENSIONABLE flat pattern inside a drawing in the standard.
    """
    model = _resolve_model(model_path)
    dwg = _dwg()
    v = dwg.CreateFlatPatternViewFromModelView3(
        model, config, C.mm(at[0]), C.mm(at[1]), 0.0,
        bool(hide_bend_lines), bool(flip))
    if v is None:
        # MEASURED 2026-09-25: with the part NOT open in SW this returns None (the same
        # part, open, builds the view) -- open it, give the focus back, try again.
        title = _md().GetTitle()
        C.app().OpenDoc6(model, C.DOC_PART, 1, "", C.pythoncom.Missing, C.pythoncom.Missing)
        C.app().ActivateDoc(title)
        dwg = _dwg()
        v = dwg.CreateFlatPatternViewFromModelView3(
            model, config, C.mm(at[0]), C.mm(at[1]), 0.0,
            bool(hide_bend_lines), bool(flip))
    if v is None:
        raise RuntimeError(
            f"flat_pattern_view failed (is the part sheet metal? saved?): {model}")
    v = C.cast(v, "IView")
    if bend_notes:
        v.ShowSheetMetalBendNotes = True
    return v.GetName2()


def _view(view_name: str = ""):
    """The active IView (or the one named `view_name`) -- the default for the other
    functions here."""
    dwg = _dwg()
    if view_name:
        dwg.ActivateView(view_name)
    return C.cast(dwg.ActiveDrawingView, "IView")


def bend_lines(view_name: str = ""):
    """BEND lines of a flat pattern view: [(x1,y1,x2,y2)] in mm on the sheet.

    Useful for anchoring a dimension/annotation to the bend (the bend line is what the
    bench marks on the sheet). Counts zero if the view is not a flat pattern.
    """
    v = _view(view_name)
    if not v.GetBendLineCount():
        return []
    # GOTCHA: `IGetBendLines(n)` takes the COUNT and returns the array (it is not indexed
    # by position) -- calling it with an index raises 'Invalid number of parameters'. The
    # clean path is `GetBendLines()`, which already returns the tuple.
    out = []
    for seg in (v.GetBendLines() or []):
        ln = C.cast(seg, "ISketchLine")
        a = C.cast(ln.GetStartPoint2(), "ISketchPoint")
        b = C.cast(ln.GetEndPoint2(), "ISketchPoint")
        out.append((a.X * 1000.0, a.Y * 1000.0, b.X * 1000.0, b.Y * 1000.0))
    return out


def _bend_table_template() -> str:
    """The installed `bendtable-standard.sldbndtbt`, or "" when there is none.

    /!\\ VERSION DIFFERENCE, measured 2026-09-23: SW 2017 accepted an EMPTY template and
    used its default; SW 2026 (rev 34.3.2) raises "The server threw an exception" on the
    same call, and builds the table as soon as the file is passed explicitly. It was the
    first live run of the sheet-metal smoke on SW 2026 that found it.
    `GetExecutablePath` returns the install FOLDER, not the .exe.
    """
    root = C.app().GetExecutablePath()
    for lang in ("english", "*"):
        hits = glob.glob(os.path.join(root, "lang", lang, "bendtable-standard.sldbndtbt"))
        if hits:
            return hits[0]
    return ""


def bend_table(view_name: str = "", at=None, *, start: str = "1",
               template: str = "") -> str:
    """BEND TABLE of the flat pattern view (one row per bend: direction, angle, radius).
    It is the natural complement of the cut list: that one states the material rectangle,
    this one states what to do at the press brake.

    The view must be a FLAT PATTERN one (`flat_pattern_view`). `at` = top-left corner
    in mm; None (DEFAULT) puts it in the top-right corner inside the frame (the old fixed
    default ran off the A3 sheet). Returns the name of the annotation created.
    """
    v = _view(view_name)
    if not v.IsFlatPatternView():
        raise RuntimeError(
            f"'{view_name or v.GetName2()}' is not a flat pattern view "
            "(use flat_pattern_view).")
    x, y = at if at is not None else (250, 250)
    t = v.InsertBendTable(False, C.mm(x), C.mm(y), 1, start,
                          template or _bend_table_template())
    if t is None:
        raise RuntimeError("InsertBendTable returned None.")
    _md().EditRebuild3()
    if at is None:
        _stack_tables_top_right()
    return C.cast(C.cast(t, "IBendTableAnnotation").BendTable,
                  "IBendTable").GetFeature().Name


# NOTE (cut list note -> ESCAPE HATCH): `IView.InsertCutListPropertyNote` raises 'The
# server threw an exception' on SW 2017 through pywin32 (measured 2026-08-29), with the
# flat pattern view active. To bring the fabrication data onto the drawing, use
# `sw_sheetmetal.cut_list()` + `annotate(...)`.

# ── dimensioning from the model (an HONEST dimension: it came from the part) ──
def import_model_dims(view_name: str = "", *, types=("dimensions", "holes"),
                      all_views: bool = True) -> int:
    """Import the MODEL's annotations into the drawing (feature dimensions, hole callouts,
    datums...). The cheap and honest path (the dimension was born in the model).
      all_views=True (DEFAULT) -> hands each dimension to the view(s) that show it.
        This is the RELIABLE path. With all_views=False SW inserts into a single view that
        IT picks (not always the active one) and returns 0 if that view is not on the sheet.
      view_name -> only has an effect with all_views=False (activates the view first; even
        then SW may prefer another view for a specific dimension).
      types     -> subset of {'dimensions','holes','datums','gtols','notes','axes'}.
        'dimensions' picks up feature dimensions (which come marked-for-drawing, flag 32768).
    Returns the count of inserted annotations (0 = nothing importable in the present views)."""
    dwg = _dwg()
    if view_name and not all_views:
        dwg.ActivateView(view_name)
    mask = 0
    for t in types:
        if t not in _ANN:
            raise ValueError(f"type '{t}' is invalid ({sorted(_ANN)}).")
        mask |= _ANN[t]
    res = dwg.InsertModelAnnotations3(FROM_ENTIRE_MODEL, mask,
                                      bool(all_views), False, False, False)
    # returns a Variant = array of the annotations (or None if nothing).
    if res is None:
        return 0
    try:
        return len(res)
    except TypeError:
        return 1


# ── REFERENCE dimensions on the drawing (they do not come from the sketch/model) ──
def _view_names(dwg):
    """Names of the drawing views (excludes the 'sheet', which is the 1st of
    GetFirstView)."""
    out, raw = [], dwg.GetFirstView()
    raw = C.cast(raw, "IView").GetNextView() if raw is not None else None
    while raw is not None:
        v = C.cast(raw, "IView")
        out.append(v.GetName2())
        raw = v.GetNextView()
    return out


def list_views() -> list:
    """READ the drawing's views (also a drawing made outside this MCP), every sheet.
    Each view: {name, sheet, type (standard|named|projected|section|detail|auxiliary|
    relative|...), orientation ('*Front', '*Isometric', '' for derived views), parent
    (the view it was projected/cut from), model (path), configuration, position_mm
    (centre on the sheet), outline_mm [xmin, ymin, xmax, ymax], scale 'n:d' and
    scale_ratio [n, d], exploded, dims and annotations (how many the view holds)}. Use
    the names with the other dwg.* verbs. The counts are what CHANGES on a drawing: the
    model's volume and tree stay the same at every step (measured 2026-08-19), so a check
    of a drawing step counts views, dims and annotations.
    To aim at an edge, start from position_mm (the exact centre of the view's geometry)
    plus the model's size times the scale; the outline carries a margin (measured ~6 mm
    each side at 1:1), so a pick on the outline misses the edge.
    MEASURED: opening a drawing can leave one of its MODELS as the active document --
    activate the drawing first (its title) if this says it is not one."""
    md = _md()
    if md.GetType() != C.DOC_DRAWING:
        raise RuntimeError(f"list_views: the active document '{md.GetTitle()}' is not a "
                           "drawing -- activate the drawing first")
    out, dwg = [], _dwg()
    for sheet in dwg.GetViews() or []:
        sheet = list(sheet or [])
        if not sheet:
            continue
        sheet_name = C.cast(sheet[0], "IView").Name     # [0] is the sheet itself
        for raw in sheet[1:]:
            v = C.cast(raw, "IView")
            kind = C.enum_names("swDrawingViewTypes_e").get(v.Type, str(v.Type))
            kind = kind.replace("swDrawing", "").replace("View", "").lower() or str(v.Type)
            ratio = [float(x) for x in (v.ScaleRatio or (1.0, 1.0))]
            base = v.GetBaseView()
            n_dims, dd = 0, v.GetFirstDisplayDimension5()
            while dd is not None:
                n_dims += 1
                dd = C.cast(dd, "IDisplayDimension").GetNext5()
            n_ann, ann = 0, v.GetFirstAnnotation2()
            while ann is not None:
                n_ann += 1
                ann = C.cast(ann, "IAnnotation").GetNext3()
            out.append({
                "name": v.GetName2(), "sheet": sheet_name, "type": kind,
                "orientation": v.GetOrientationName() or "",
                "parent": C.cast(base, "IView").GetName2() if base is not None else None,
                "model": v.GetReferencedModelName(),
                "configuration": v.ReferencedConfiguration,
                "position_mm": [round(x * 1000.0, 3) for x in v.Position],
                "outline_mm": [round(x * 1000.0, 3) for x in (v.GetOutline() or ())],
                "scale": f"{ratio[0]:g}:{ratio[1]:g}", "scale_ratio": ratio,
                "exploded": bool(v.IsExploded()),
                "dims": n_dims, "annotations": n_ann,
            })
    return out


def view_center(view_name: str):
    """(x,y) mm of the CENTER of a view's geometry on the sheet (useful for aiming at
    edges)."""
    dwg = _dwg()
    dwg.ActivateView(view_name)
    pos = C.cast(dwg.ActiveDrawingView, "IView").Position
    return (pos[0] * 1000.0, pos[1] * 1000.0)


def dimension(view_name: str, picks, place_at, *,
              orient: Literal["aligned", "horizontal", "vertical"] = "aligned") -> float:
    """Create a REFERENCE dimension on the drawing by selecting PROJECTED geometry (it
    need not exist in the sketch/model). `picks` = list of (x,y) on the SHEET (mm) where to
    pick edges/vertices (1 edge = its length; 2 entities = the distance between them).
    `place_at` = (x,y) where to place the dimension. `orient`: 'aligned' | 'horizontal' |
    'vertical'.
    Aim at the edges starting from `view_center(view)` +/- half the part's projected size.
    Returns the value read (mm). Escape hatch: curved/complex geometry -> sw_call."""
    dwg = _dwg()
    md = _md()
    dwg.ActivateView(view_name)
    ext = C.cast(md.Extension, "IModelDocExtension")
    md.ClearSelection2(True)
    for i, (px, py) in enumerate(picks):
        ok = ext.SelectByID2("", "EDGE", C.mm(px), C.mm(py), 0.0, i > 0, 0, None, 0)
        if not ok:  # if no edge was found, try a vertex at the same point
            ext.SelectByID2("", "VERTEX", C.mm(px), C.mm(py), 0.0, i > 0, 0, None, 0)
    placer = {"aligned": md.AddDimension2, "horizontal": md.AddHorizontalDimension2,
              "vertical": md.AddVerticalDimension2}.get(orient)
    if placer is None:
        raise ValueError(f"orient '{orient}' is invalid (aligned|horizontal|vertical).")
    dim = placer(C.mm(place_at[0]), C.mm(place_at[1]), 0.0)
    md.ClearSelection2(True)
    if dim is None:
        raise RuntimeError("dimension: no dimension created (did the picks miss the "
                           "geometry?).")
    return round(C.cast(dim, "IDisplayDimension").GetDimension2(0).SystemValue * 1000.0, 3)


# swDimensionType_e
_DIM_RADIAL, _DIM_DIAMETER, _DIM_HOR, _DIM_VER = 5, 6, 11, 12


def dedupe_dimensions(*, aligned: bool = True) -> int:
    """Remove DUPLICATE dimensions, so each size is stated ONCE (the drawing standard).
      - the same MODEL dimension shown on several views (`import_model_dims(all_views=
        True)` puts it on every view that shows it) -> kept on the first view;
      - aligned=True (DEFAULT) also removes: the same diameter/radius twice in one view
        (a hole pattern imports one per instance), and the REFERENCE duplicates
        auto_dimension makes -- the same horizontal value twice in one view or in two
        views stacked in one column (front/top share X and are dimensioned from the same
        left edge, so an equal value is the same position), the same vertical value in
        one view or one row. Run it after arrange() -- alignment is read from the
        positions. Kept: the first (the front view first).
    Returns how many it removed. (Selection: `IAnnotation.Select2` + `EditDelete` --
    Select3(None) fails.)"""
    dwg = _dwg()
    md = _md()
    seen, removed = set(), 0
    kept = []      # (x, y, type, value) of the reference dimensions kept so far
    for v in _sheet_views():
        px, py = _pos_mm(v)
        dups, dd = [], v.GetFirstDisplayDimension5()
        while dd is not None:
            ddc = C.cast(dd, "IDisplayDimension")
            dim = C.cast(ddc.GetDimension2(0), "IDimension")
            fn = dim.FullName
            if fn in seen:
                dups.append(ddc)
            else:
                seen.add(fn)
                kind, val = ddc.Type2, round(dim.SystemValue * 1000.0, 3)
                ref = fn.startswith("RD")
                if aligned:
                    same = any(
                        k == kind and abs(val2 - val) < 1e-3 and (
                            # one size per hole diameter / radius in a view (a pattern of
                            # 300 holes imported 300 identical diameters)
                            (kind in (_DIM_DIAMETER, _DIM_RADIAL) and (x, y) == (px, py))
                            # baseline reference dimensions share their datum edge: an
                            # equal value in the same view or in an aligned view is the
                            # same position
                            or (ref and r2 and kind == _DIM_HOR and abs(x - px) < 0.5)
                            or (ref and r2 and kind == _DIM_VER and abs(y - py) < 0.5))
                        for x, y, k, val2, r2 in kept)
                    if same:
                        dups.append(ddc)
                    else:
                        kept.append((px, py, kind, val, ref))
            dd = ddc.GetNext5()
        for ddc in dups:
            md.ClearSelection2(True)
            if C.cast(ddc.GetAnnotation(), "IAnnotation").Select2(False, 0):
                md.EditDelete()
                removed += 1
    md.ClearSelection2(True)
    return removed


# ── titleblock (notes linked to $PRP/$PRPSHEET properties) ────────────────────
def _sheet_model():
    """The IModelDoc2 the first view of the current sheet shows (None if no view)."""
    sheet = next(iter(_dwg().GetViews() or []), None)
    for raw in list(sheet or [])[1:]:
        m = C.cast(raw, "IView").ReferencedDocument
        if m is not None:
            return C.cast(m, "IModelDoc2")
    return None


def fill_titleblock(*, title: str = "", material: str = "auto", weight: str = "auto",
                    finish: str = "", drawn_by: str = "", checked_by: str = "",
                    drawn_date: str = "", checked_date: str = "", revision: str = "",
                    company: str = "", save_model: bool = True) -> dict:
    """Fill the title block. Call it AFTER the first view (it needs the model).
    From the MODEL shown on the sheet (the ISO format reads them as `$PRPSHEET`):
      title    -> TITLE (the model's 'Description').
      material -> 'auto' (DEFAULT) links to the material assigned to the part
                  ("SW-Material"), or a text; skipped on an assembly.
      weight   -> 'auto' (DEFAULT) links to the model mass ("SW-Mass", in the model's
                  mass unit), or a text like '1.2 kg'.
      finish   -> FINISH.
    From the DRAWING (`$PRP`): drawn_by, checked_by, drawn_date, checked_date, revision,
    company. The drawing NUMBER is its file name: save before exporting.
    save_model=True (DEFAULT) saves the model after writing its properties -- they live
    in the model file, and a drawing reopened against the unsaved model showed an empty
    title block (measured 2026-09-25).
    Returns the fields applied."""
    applied = {}
    model = _sheet_model()
    if model is not None:
        mfile = os.path.basename(model.GetPathName())
        is_part = model.GetType() == C.DOC_PART
        mcpm = C.cast(C.cast(model.Extension, "IModelDocExtension").CustomPropertyManager(""),
                      "ICustomPropertyManager")
        # the mass link evaluates in the model's mass unit; the unit text after the link
        # is kept by SW ('"SW-Mass@x" g' -> '1557.49 g', measured 2026-09-25)
        unit = C.enum_names("swUnitsMassPropMass_e").get(
            C.cast(model.Extension, "IModelDocExtension").GetUserPreferenceInteger(
                _PREF_MASS_UNIT, 0), "")
        unit = {"Milligrams": "mg", "Grams": "g", "Kilograms": "kg",
                "Pounds": "lb"}.get(unit.rsplit("_", 1)[-1], "")
        mfields = {"Description": title, "Finish": finish,
                   "Material": (f'"SW-Material@{mfile}"' if material == "auto" and is_part
                                else "" if material == "auto" else material),
                   "Weight": (f'"SW-Mass@{mfile}" {unit}'.strip() if weight == "auto"
                              else weight)}
        for k, val in mfields.items():
            if val:
                mcpm.Add3(k, 30, str(val), 1)
                applied[k] = val
        if save_model and applied and model.GetPathName():
            C.save_as(model, model.GetPathName())
    elif title or finish or material not in ("", "auto"):
        raise RuntimeError("fill_titleblock: the sheet has no view yet -- add a view first "
                           "(title, material and finish live in the model).")
    ext = C.cast(_md().Extension, "IModelDocExtension")
    cpm = C.cast(ext.CustomPropertyManager(""), "ICustomPropertyManager")
    fields = {"DrawnBy": drawn_by, "CheckedBy": checked_by, "DrawnDate": drawn_date,
              "CheckedDate": checked_date, "Revision": revision, "COMPANYNAME": company}
    for k, val in fields.items():
        if val:
            cpm.Add3(k, 30, str(val), 1)
            applied[k] = val
    _md().EditRebuild3()
    return applied


# ── annotations ───────────────────────────────────────────────────────────────
def annotate(text: str, at=(50, 50), *, kind: str = "note", view_name: str = "") -> str:
    """Annotate the drawing.
      kind='note' -> free text note at (x,y) on the SHEET (mm). With view_name, pins the
                     note to that view (otherwise it sits on the sheet).
    Returns the text (annotations have no useful stable name). Specialized symbols
    (weld/surface-finish/heavy GD&T) stay in the escape hatch + recipe."""
    if kind != "note":
        raise ValueError("kind is 'note' only for now (weld/surface/gd&t -> escape hatch).")
    dwg = _dwg()
    if view_name:
        dwg.ActivateView(view_name)
    md = _md()
    note = md.InsertNote(text)
    if note is None:
        raise RuntimeError("annotate: InsertNote returned None.")
    ann = C.cast(C.cast(note, "INote").GetAnnotation(), "IAnnotation")
    ann.SetPosition(C.mm(at[0]), C.mm(at[1]), 0.0)
    md.ClearSelection2(True)
    return text


# ── surface finish (roughness symbol) ────────────────────────────────────────
# swSFSymType_e / swSFLaySym_e
_SF_BASIC, _SF_MACHINED, _SF_DONT_MACHINE = 0, 1, 2
_SF_LAY_NONE = 0
_LEADER_STRAIGHT = 1   # swLeaderStyle_e


def _outside(view_name: str, pick, side: str):
    """A point OUTSIDE the view's outline for an annotation whose leader goes back to
    `pick`: 'right' (at the pick's height) or 'top' (above the pick). Right and top are
    free because auto_dimension puts its dimensions on the left and below."""
    dwg = _dwg()
    dwg.ActivateView(view_name)
    ol = [x * 1000.0 for x in C.cast(dwg.ActiveDrawingView, "IView").GetOutline()]
    frame, _tb = _sheet_frame()
    if side == "top" and ol[3] + 22.0 > frame[3]:
        side = "right_high"          # no room above the view: go right, above a gtol
    if side == "right":
        return (ol[2] + 12.0, pick[1] + 8.0)
    if side == "right_high":
        return (ol[2] + 12.0, pick[1] + 22.0)
    return (pick[0] + 10.0, ol[3] + 10.0)


def surface_finish(view_name: str, edge_at, ra: float = 3.2, *,
                   kind: Literal["basic", "machined", "dont_machine"] = "machined",
                   place_at=None, text_offset: float = 8.0) -> str:
    """Pin a SURFACE FINISH (roughness) symbol to an EDGE/surface of a view -- NOT
    floating in space (the titleblock already covers the GENERAL finish).
      view_name -> the view holding the surface.
      edge_at   -> (x,y) on the SHEET (mm) ON the edge to mark (aim from
                   `view_center(view)` +/- half the part's projected height/width).
      ra        -> roughness Ra (e.g. 3.2, 1.6). kind: 'machined'|'basic'|'dont_machine'.
      place_at  -> (x,y) on the SHEET (mm) for the symbol; None (DEFAULT) puts it ABOVE
                   the view with a leader to the edge (a symbol sitting on the geometry
                   covered the bore it marked). text_offset is the old fixed offset, used
                   only with place_at=False.
    The Insert ignores LocX/LocY, but with the edge SELECTED the symbol ANCHORS to it (its
    tip lands on the surface). Returns 'Ra <ra>'."""
    symtype = {"machined": _SF_MACHINED, "basic": _SF_BASIC,
               "dont_machine": _SF_DONT_MACHINE}.get(kind)
    if symtype is None:
        raise ValueError(f"kind '{kind}' is invalid (machined|basic|dont_machine).")
    dwg = _dwg()
    md = _md()
    dwg.ActivateView(view_name)
    ext = C.cast(md.Extension, "IModelDocExtension")
    md.ClearSelection2(True)
    if not ext.SelectByID2("", "EDGE", C.mm(edge_at[0]), C.mm(edge_at[1]), 0.0,
                           False, 0, None, 0):
        raise RuntimeError(f"surface_finish: no edge found at {edge_at} (aim at the "
                           f"surface).")
    sym = ext.InsertSurfaceFinishSymbol3(
        symtype, 0, C.mm(edge_at[0]), C.mm(edge_at[1] + text_offset), 0.0, _SF_LAY_NONE, 0,
        "", "", "", "", str(ra), "", "")
    if sym is None:
        raise RuntimeError("surface_finish: InsertSurfaceFinishSymbol3 returned None.")
    ann = C.cast(C.cast(sym, "ISFSymbol").GetAnnotation(), "IAnnotation")
    if place_at is False:
        place_at = (edge_at[0], edge_at[1] + text_offset)
    elif place_at is None:
        place_at = _outside(view_name, edge_at, "top")
    if place_at != (edge_at[0], edge_at[1] + text_offset):
        # without a leader the symbol stays glued to the edge and ignores the position
        ann.SetLeader3(_LEADER_STRAIGHT, 0, True, False, False, False)
    ann.SetPosition(C.mm(place_at[0]), C.mm(place_at[1]), 0.0)
    md.ClearSelection2(True)
    return f"Ra {ra}"


# ── center marks (hole standard: a cross at the center of each hole/circle) ───
# swCenterMarkStyle_e (Single=2) / swAutoInsertCenterMarkTypes_e (Hole=1,Fillets=2,Slots=4)
_CM_SINGLE = 2
_CM_HOLE, _CM_FILLETS, _CM_SLOTS = 1, 2, 4
_ANN_CENTERMARK = 13  # swAnnotationType_e.swCenterMarkSym


def _count_center_marks(v) -> int:
    """Count a view's center marks by walking the annotations (GetType==13).
    SW 2017 GOTCHA: `IView.GetCenterMarkCount()` returns 0 even with marks inserted -- the
    honest path is to count the annotations of type swCenterMarkSym."""
    n, a = 0, v.GetFirstAnnotation2()
    while a is not None:
        an = C.cast(a, "IAnnotation")
        if an.GetType() == _ANN_CENTERMARK:
            n += 1
        a = an.GetNext3()
    return n


def center_marks(view_name: str, *, holes: bool = True, fillets: bool = False,
                 slots: bool = True) -> int:
    """Insert CENTER MARKS (crosses) automatically on a view's holes/circles (the hole
    standard). AutoInsertCenterMarks2 sweeps the view and marks whatever `holes`/`fillets`/
    `slots` ask for. Returns the mark count in the view.
    SW 2017 GOTCHA: with UseDocumentDefaults=False (manual size/gap) the method returns
    True and inserts NOTHING (a silent no-op) -> we use the document defaults, which work."""
    dwg = _dwg()
    dwg.ActivateView(view_name)
    v = C.cast(dwg.ActiveDrawingView, "IView")
    opt = (_CM_HOLE if holes else 0) | (_CM_FILLETS if fillets else 0) | (_CM_SLOTS if slots else 0)
    v.AutoInsertCenterMarks2(_CM_SINGLE, opt, bool(slots), bool(slots), True,
                             0.0, 0.0, True, True, 0.0)
    _md().EditRebuild3()
    return _count_center_marks(v)


# ── GD&T (first-class datum + geometric tolerance frame) ─────────────────────
# swGtolGeomCharSymbol_e (geometric characteristic)
_GTOL_SYM = {"straightness": 14, "flatness": 15, "circularity": 16, "cylindricity": 17,
             "profile_line": 18, "profile_surface": 19, "angularity": 20,
             "perpendicularity": 21, "parallelism": 22, "position": 23,
             "concentricity": 24, "circular_runout": 25, "total_runout": 26,
             "symmetry": 13}
# swGtolMatCondition_e (material condition)
_GTOL_MC = {"none": 0, "mmc": 1, "rfs": 2, "lmc": 3}


def _pick_edge(ext, at):
    """Select the edge (or a fallback vertex) at (x,y) on the sheet (mm). Returns
    True/False."""
    if ext.SelectByID2("", "EDGE", C.mm(at[0]), C.mm(at[1]), 0.0, False, 0, None, 0):
        return True
    return ext.SelectByID2("", "VERTEX", C.mm(at[0]), C.mm(at[1]), 0.0, False, 0, None, 0)


def datum(view_name: str, edge_at, label: str = "A", *, place_at=None) -> str:
    """Insert a first-class DATUM (a tag, e.g. 'A') pinned to an edge/face of a view.
      edge_at  -> (x,y) on the SHEET (mm) ON the edge that becomes the reference (aim via
                  view_center).
      label    -> the datum letter ('A','B',...).
      place_at -> (x,y) on the SHEET (mm) to MOVE the tag away from the drawing (with a
                  leader back to the edge).
    Flow: select the edge -> InsertDatumTag2 -> SetLabel. Returns the label applied."""
    dwg = _dwg()
    md = _md()
    dwg.ActivateView(view_name)
    ext = C.cast(md.Extension, "IModelDocExtension")
    md.ClearSelection2(True)
    if not _pick_edge(ext, edge_at):
        raise RuntimeError(f"datum: no edge found at {edge_at} (aim at the surface).")
    tag = md.InsertDatumTag2()
    if tag is None:
        raise RuntimeError("datum: InsertDatumTag2 returned None (was an edge selected?).")
    dt = C.cast(tag, "IDatumTag")
    dt.SetLabel(label)
    if place_at is not None:
        ann = C.cast(dt.GetAnnotation(), "IAnnotation")
        ann.SetPosition(C.mm(place_at[0]), C.mm(place_at[1]), 0.0)
    md.ClearSelection2(True)
    md.EditRebuild3()
    return C.cast(tag, "IDatumTag").GetLabel()


def gtol(view_name: str, edge_at, symbol: Literal[tuple(_GTOL_SYM)], tolerance, *, datums=(),
         diameter: bool = False, mc: Literal[tuple(_GTOL_MC)] = "none", place_at=None) -> str:
    """Insert a GEOMETRIC TOLERANCE frame (GD&T) pinned to an edge of a view.
      symbol    -> the characteristic: 'flatness'|'perpendicularity'|'parallelism'|
                   'position'|'concentricity'|'cylindricity'|'circularity'|'straightness'|
                   'symmetry'|'angularity'|'circular_runout'|'total_runout'|
                   'profile_line'|'profile_surface'.
      tolerance -> the tolerance field value (mm; e.g. 0.05).
      edge_at   -> (x,y) on the SHEET (mm) ON the edge being controlled.
      datums    -> datum refs in order (e.g. ('A',) or ('A','B','C')).
      diameter  -> True puts the diameter symbol in the tolerance (e.g. hole position).
      mc        -> material condition of the tolerance: none|mmc|rfs|lmc.
      place_at  -> (x,y) on the SHEET (mm) to PLACE the frame, with a leader back to the
                   edge. None (DEFAULT) puts it to the RIGHT of the view at the edge's
                   height; False leaves it where SW creates it (stuck to the geometry).
    Returns the tolerance text applied. GOTCHA: use SetFrameSymbols (v1, GCS = an INT
    enum); v2 wants GCS as a string from the gtol font. Frame 1 = the only frame."""
    if symbol not in _GTOL_SYM:
        raise ValueError(f"symbol '{symbol}' is invalid ({sorted(_GTOL_SYM)}).")
    dwg = _dwg()
    md = _md()
    dwg.ActivateView(view_name)
    ext = C.cast(md.Extension, "IModelDocExtension")
    md.ClearSelection2(True)
    if not _pick_edge(ext, edge_at):
        raise RuntimeError(f"gtol: no edge found at {edge_at} (aim at the surface).")
    g = md.InsertGtol()
    if g is None:
        raise RuntimeError("gtol: InsertGtol returned None (was an edge selected?).")
    g = C.cast(g, "IGtol")
    ds = list(datums) + ["", "", ""]
    tol = str(tolerance)
    g.SetFrameValues2(1, tol, "", ds[0], ds[1], ds[2])
    g.SetFrameSymbols(1, _GTOL_SYM[symbol], bool(diameter), _GTOL_MC[mc], False, 0, 0, 0, 0)
    if place_at is None:
        place_at = _outside(view_name, edge_at, "right")
    if place_at is not False:
        # move the frame away from the drawing (the leader stays pinned to the picked edge).
        g.SetPosition(C.mm(place_at[0]), C.mm(place_at[1]), 0.0)
    md.ClearSelection2(True)
    md.EditRebuild3()
    # read the applied frame back (honest): GetFrameValues(1) = [tol1, tol2, dat1, dat2, dat3].
    vals = g.GetFrameValues(1)
    return str(vals[0]) if vals else tol


# ── tables (BOM) ──────────────────────────────────────────────────────────────
def _sheet_frame():
    """(frame, titleblock) rectangles of the current sheet, in mm."""
    dwg = _dwg()
    props = C.cast(dwg.GetCurrentSheet(), "ISheet").GetProperties2()
    w, h = props[5] * 1000, props[6] * 1000
    frame = [_FRAME_MM[0], _FRAME_MM[1], w - _FRAME_MM[2], h - _FRAME_MM[3]]
    sv = C.cast(dwg.GetFirstView(), "IView")
    _fields, tb = _titleblock(sv)
    if tb is not None:
        tb = [tb[0] - 2, frame[1], frame[2], tb[3] + 2]
    return frame, tb


def _all_tables():
    """Every table annotation on the current sheet (sheet-level and view-level)."""
    dwg, out, seen = _dwg(), [], set()
    raw = dwg.GetFirstView()
    while raw is not None:
        v = C.cast(raw, "IView")
        for t in v.GetTableAnnotations() or []:
            ta = C.cast(t, "ITableAnnotation")
            key = tuple(round(x, 6) for x in C.cast(ta.GetAnnotation(),
                                                    "IAnnotation").GetPosition())
            if key not in seen:
                seen.add(key)
                out.append(ta)
        raw = v.GetNextView()
    return out


def _move_table(ta, x_left, y_top):
    ann = C.cast(ta.GetAnnotation(), "IAnnotation")
    ann.SetPosition(C.mm(x_left), C.mm(y_top), 0.0)


def _stack_tables_top_right():
    """Put every table in the frame's top-right corner, stacked downwards. Returns the
    rectangles they occupy."""
    frame, _tb = _sheet_frame()
    y, rects = frame[3], []
    for ta in _all_tables():
        r = _table_rect(ta)
        w, h = r[2] - r[0], r[3] - r[1]
        _move_table(ta, frame[2] - w, y)
        rects.append([frame[2] - w, y - h, frame[2], y])
        y -= h + 2
    _md().GraphicsRedraw2()
    return rects


def table(view_name: str, *, kind: str = "bom", at=None,
          bom_type: str = "top", anchor: int = ANCHOR_TOPLEFT) -> str:
    """Insert a TABLE anchored to a view (typically an ASSEMBLY one).
      kind='bom' -> bill of materials. bom_type: 'top'|'parts'|'indented'.
      at -> (x,y) of the table's top-left corner on the sheet, in mm. None (DEFAULT) puts
            it in the top-right corner INSIDE the frame (a fixed position overflowed the
            sheet as soon as the table was wider than guessed).
    Returns the table-view name. Hole/weld/revision tables -> escape hatch for now."""
    if kind != "bom":
        raise ValueError("kind is 'bom' only for now (hole/weld/revision -> escape hatch).")
    dwg = _dwg()
    dwg.ActivateView(view_name)
    v = C.cast(dwg.ActiveDrawingView, "IView")
    if v is None:
        raise RuntimeError(f"table: view '{view_name}' not found/active.")
    # the Configuration argument must NAME the view's configuration: with "" SW builds a
    # BOM with the header only and NO rows, and returns it as a success (measured
    # 2026-09-25 on clevis: "" -> 1 row, "Default" -> header + 3 items).
    x, y = at if at is not None else (300, 200)
    tbl = v.InsertBomTable4(False, C.mm(x), C.mm(y), anchor,
                            _BOM.get(bom_type, 2), v.ReferencedConfiguration or "", "",
                            False, 0, False)
    if tbl is None:
        raise RuntimeError("table(bom) failed (does the view reference a SAVED assembly?).")
    if at is None:
        _stack_tables_top_right()
    return view_name


# ── item balloons (number the components on the assembly view) ───────────────
_ANN_NOTE = 6  # swAnnotationType_e.swNote (a balloon is a note)
# swBalloonLayoutType_e (how the balloons are arranged around the view)
_BALLOON_LAYOUT = {"square": 1, "circle": 2, "top": 3, "bottom": 4, "right": 5, "left": 6}


def balloons(view_name: str, *,
             layout: Literal["square", "circle", "top", "bottom", "right", "left"] = "circle",
             offset_mm: float = 12.0) -> int:
    """Insert BALLOONS numbering the items (components) on an ASSEMBLY view (AutoBalloon).
    AutoBalloon GENERATES its own item numbers (1 balloon per visible component); prefer
    the EXPLODED view (call set_exploded first) to expose every item. `layout`:
    square|circle|top|bottom|left|right. `offset_mm`: how far outside the view outline
    the balloons end up (they are pulled in from where AutoBalloon parks them). Returns
    the balloon count.
    SW 2017 GOTCHAS: (a) AutoBalloon5(BalloonOptions) is a no-op through pywin32 (returns
    None) -> use AutoBalloon(layout). (b) A BOM ALREADY on the sheet BREAKS AutoBalloon (it
    places a single balloon) -> balloon BEFORE inserting the BOM. (c) Counting is done by
    an annotation walk (GetType==swNote); GetFirstNote/GetNext does not list them all
    reliably."""
    if layout not in _BALLOON_LAYOUT:
        raise ValueError(f"layout '{layout}' is invalid ({sorted(_BALLOON_LAYOUT)}).")
    dwg = _dwg()
    md = _md()
    dwg.ActivateView(view_name)
    ext = C.cast(md.Extension, "IModelDocExtension")
    md.ClearSelection2(True)
    if not ext.SelectByID2(view_name, "DRAWINGVIEW", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError(f"balloons: could not select view '{view_name}'.")
    dwg.AutoBalloon(_BALLOON_LAYOUT[layout])
    md.ClearSelection2(True)
    md.EditRebuild3()
    v = C.cast(dwg.ActiveDrawingView, "IView")
    # AutoBalloon parks the balloons far out (leaders of 100+ mm that made the view take
    # half the sheet): pull each one in along its own direction to `offset_mm` outside
    # the view outline
    ol = [x * 1000.0 for x in v.GetOutline()]
    cx, cy = (ol[0] + ol[2]) / 2, (ol[1] + ol[3]) / 2
    hw, hh = (ol[2] - ol[0]) / 2 + offset_mm, (ol[3] - ol[1]) / 2 + offset_mm
    n, a = 0, v.GetFirstAnnotation2()
    while a is not None:
        an = C.cast(a, "IAnnotation")
        if an.GetType() == _ANN_NOTE and \
                C.cast(an.GetSpecificAnnotation(), "INote").IsBomBalloon():
            n += 1
            x, y = [p * 1000.0 for p in an.GetPosition()[:2]]
            dx, dy = x - cx, y - cy
            k = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
            if k < 1.0:
                an.SetPosition(C.mm(cx + dx * k), C.mm(cy + dy * k), 0.0)
        a = an.GetNext3()
    md.EditRebuild3()
    return n


def set_exploded(view_name: str, exploded: bool = True) -> bool:
    """Show a drawing view in the EXPLODED state (for an assembly drawing). Requires the
    ASSEMBLY to have an explode view (call asm.explode() before creating the views).
    Returns the state (IView.IsExploded)."""
    dwg = _dwg()
    dwg.ActivateView(view_name)
    v = C.cast(dwg.ActiveDrawingView, "IView")
    v.ShowExploded(bool(exploded))
    _md().EditRebuild3()
    return bool(v.IsExploded())


# ── section view ──────────────────────────────────────────────────────────────
def section_view(parent_view: str, p1, p2, at, *, label: str = "A",
                 aligned: bool = True) -> str:
    """Create a SECTION VIEW from a section line on `parent_view`.
      p1, p2 -> ends of the section line, in mm on the SHEET (crossing the parent view).
      at     -> (x,y) where the section view is placed, in mm on the sheet.
      label  -> the section letter ('A' -> "SECTION A-A").
    Draws the line, selects it and calls CreateSectionViewAt5. Returns the view name.
    (A sensitive flow: the line must cross the parent view's geometry.)"""
    dwg = _dwg()
    md = _md()
    dwg.ActivateView(parent_view)
    v = C.cast(dwg.ActiveDrawingView, "IView")
    # MEASURED 2026-09-25: a view's sketch is NOT in sheet coordinates -- its origin is
    # the view Position and its unit is the MODEL's (sheet = position + sketch * scale).
    # Sheet coordinates passed straight put the cut outside the part, and SW then shows
    # the whole part behind the plane: a "section" with no hatch and no cut feature.
    ox, oy = v.Position[0], v.Position[1]
    k = v.ScaleDecimal or 1.0
    sx = lambda p: ((C.mm(p[0]) - ox) / k, (C.mm(p[1]) - oy) / k)  # noqa: E731
    (x1, y1), (x2, y2) = sx(p1), sx(p2)
    sm = C.cast(md.SketchManager, "ISketchManager")
    seg = sm.CreateLine(x1, y1, 0.0, x2, y2, 0.0)
    if seg is None:
        raise RuntimeError("section_view: did not draw the section line.")
    C.cast(seg, "ISketchSegment").Select4(False, None)
    opts = 0 if aligned else SECT_NOT_ALIGNED
    v = dwg.CreateSectionViewAt5(C.mm(at[0]), C.mm(at[1]), 0.0, label, opts, None, 0.0)
    md.ClearSelection2(True)
    if v is None:
        raise RuntimeError(
            "section_view failed (does the section line cross the parent view's "
            "geometry?).")
    return C.cast(v, "IView").GetName2()


# NOTE (detail view -> ESCAPE HATCH): `CreateDetailViewAt4` CREATES the view (returns
# 'Detail View B (3:1)') from a sketch circle drawn on the parent view, BUT on SW 2017
# through pywin32 the boundary->content link does not form reliably: the detail view comes
# out EMPTY and the callout circle floats loose (tested 2026-07-24 with the circle
# demonstrably over the hole, correct sheet coords, Showtype circle/profile, Style
# standard). SW's interactive detail does more than CreateDetailViewAt4 (it associates the
# boundary with the parent view's scope). Use sw_call + the detail view UI (or a recorded
# macro) when you need a FILLED detail. Same goes for: aligned/projected (UnfoldedViewAt),
# auxiliary, break.


# ── centerlines (the axis of every hole/shaft seen from the side) ─────────────
_ANN_CENTERLINE = 15          # swAnnotationType_e.swCenterLine
_VIEW_ENTITY_FACE = 3         # swViewEntityType_e.swViewEntityType_Face (2 is Vertex)


def _count_centerlines(v) -> int:
    n, a = 0, v.GetFirstAnnotation2()
    while a is not None:
        an = C.cast(a, "IAnnotation")
        n += an.GetType() == _ANN_CENTERLINE
        a = an.GetNext3()
    return n


def centerlines(view_name: str) -> int:
    """Insert a CENTERLINE on every VISIBLE cylindrical face (shaft, boss, pin) that a
    view shows from the SIDE -- the axis line the drawing standard asks for. A cylinder
    seen end-on gets none (use center_marks), and neither does a HIDDEN hole (measured:
    the view does not expose it -- cut it with section_view and run this on the
    section). A view that already has centerlines is left as it is, so calling it twice
    adds nothing. Returns the view's centerline count."""
    dwg, md = _dwg(), _md()
    dwg.ActivateView(view_name)
    v = C.cast(dwg.ActiveDrawingView, "IView")
    before = _count_centerlines(v)
    if before:
        return before
    for raw in v.GetVisibleEntities2(None, _VIEW_ENTITY_FACE) or []:
        face = C.cast(raw, "IFace2")
        if not C.cast(face.GetSurface(), "ISurface").IsCylinder():
            continue
        md.ClearSelection2(True)
        if C.cast(face, "IEntity").Select4(False, None):
            dwg.InsertCenterLine2()
    md.ClearSelection2(True)
    md.EditRebuild3()
    return _count_centerlines(v)


# ── automatic dimensions (for a model whose sketches carry no dimensions) ─────
_AUTODIM_SCHEME = {"baseline": 1, "ordinate": 2, "chain": 3}
_AUTODIM_ALL, _AUTODIM_BELOW, _AUTODIM_LEFT = 1, -1, -1
_ALIGN_AUTO_ARRANGE = 0   # swAlignDimensionType_e


def _annotated(v) -> bool:
    """Does the view carry placed annotations (dimensions, balloons, datums, gtols,
    finish symbols)? A section label alone does not count."""
    a = v.GetFirstAnnotation2()
    while a is not None:
        an = C.cast(a, "IAnnotation")
        kind = an.GetType()
        if kind in (2, 4, 5, 7) or (kind == 6 and C.cast(
                an.GetSpecificAnnotation(), "INote").IsBomBalloon()):
            return True
        a = an.GetNext3()
    return False


def _count_dims(v) -> int:
    n, a = 0, v.GetFirstAnnotation2()
    while a is not None:
        an = C.cast(a, "IAnnotation")
        n += an.GetType() == _ANN_DIMENSION
        a = an.GetNext3()
    return n


def _view_label(v):
    """The note that labels a derived view ('SECTION A-A', 'CORTE A-A', 'DETAIL B')."""
    a = v.GetFirstAnnotation2()
    while a is not None:
        an = C.cast(a, "IAnnotation")
        if an.GetType() == _ANN_NOTE:
            note = C.cast(an.GetSpecificAnnotation(), "INote")
            if not note.IsBomBalloon() and v.Type in (_VIEW_SECTION, _VIEW_DETAIL):
                return an
        a = an.GetNext3()
    return None


def tidy_dimensions(view_name: str = "") -> int:
    """Re-space the dimensions of a view (or of every view) with SW's Auto Arrange: the
    stacked dimensions get even spacing, off the geometry and off each other, and a
    derived view's label ('SECTION A-A') moves below them. auto_dimension already does
    it; call it after import_model_dims. Returns how many dimensions it arranged."""
    md = _md()
    ext = C.cast(md.Extension, "IModelDocExtension")
    views = [_view(view_name)] if view_name else _sheet_views()
    total = 0
    for v in views:
        md.ClearSelection2(True)
        n, dd = 0, v.GetFirstDisplayDimension5()
        while dd is not None:
            ddc = C.cast(dd, "IDisplayDimension")
            # Select2 appends; Select3(True, None) selects nothing (measured 2026-09-25)
            n += bool(C.cast(ddc.GetAnnotation(), "IAnnotation").Select2(True, 0))
            dd = ddc.GetNext5()
        if n:
            ext.AlignDimensions(_ALIGN_AUTO_ARRANGE, 0.0)
        md.ClearSelection2(True)
        total += n
        label = _view_label(v)
        if label is not None:
            ol = [x * 1000.0 for x in v.GetOutline()]
            low = ol[1]
            a = v.GetFirstAnnotation2()
            while a is not None:
                an = C.cast(a, "IAnnotation")
                if an.GetType() == _ANN_DIMENSION:
                    low = min(low, an.GetPosition()[1] * 1000.0)
                a = an.GetNext3()
            label.SetPosition(C.mm((ol[0] + ol[2]) / 2.0), C.mm(low - 6.0), 0.0)
    md.EditRebuild3()
    return total


def auto_dimension(view_name: str, *, scheme: str = "baseline") -> int:
    """Dimension a view AUTOMATICALLY from its projected geometry (SW's Auto Dimension):
    overall sizes, feature positions from the view's left/bottom edges, hole diameters
    (with 'n X' when repeated). The dimensions go BELOW and LEFT of the view -- run
    arrange() afterwards so the views make room for them.
    Use it when the model does not carry the dimensions (import_model_dims brings only
    what the model has: a part made by these verbs has almost none).
    scheme: 'baseline' (DEFAULT, every dimension from one datum edge) | 'chain' |
    'ordinate'. Returns how many dimensions the view gained.
    MEASURED 2026-09-25 (SW 2026): AutoDimension returns swAutodimStatusBadOptionValue AND
    creates the dimensions -- the return code is not the evidence, the count is."""
    if scheme not in _AUTODIM_SCHEME:
        raise ValueError(f"scheme '{scheme}' is invalid ({sorted(_AUTODIM_SCHEME)}).")
    dwg, md = _dwg(), _md()
    dwg.ActivateView(view_name)
    v = C.cast(dwg.ActiveDrawingView, "IView")
    before = _count_dims(v)
    ext = C.cast(md.Extension, "IModelDocExtension")
    md.ClearSelection2(True)
    if not ext.SelectByID2(view_name, "DRAWINGVIEW", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError(f"auto_dimension: could not select view '{view_name}'.")
    s = _AUTODIM_SCHEME[scheme]
    dwg.AutoDimension(_AUTODIM_ALL, s, _AUTODIM_BELOW, s, _AUTODIM_LEFT)
    md.ClearSelection2(True)
    md.EditRebuild3()
    tidy_dimensions(view_name)
    return _count_dims(v) - before


# ── layout: scale and place the views like a drafter ─────────────────────────
# ISO 5455 recommended scales, largest first
_ISO_SCALES = [(10, 1), (5, 1), (2, 1), (1, 1), (1, 2), (1, 5), (1, 10), (1, 20), (1, 50),
               (1, 100), (1, 200)]
_PICTORIAL = ("*Isometric", "*Dimetric", "*Trimetric")
_VIEW_SECTION, _VIEW_DETAIL, _VIEW_PROJECTED, _VIEW_AUX = 2, 3, 4, 5  # swDrawingViewTypes_e
# where a named view goes relative to the front view: (dx, dy) sign, 1st angle
_PROJ_FIRST = {"*Top": (0, -1), "*Bottom": (0, 1), "*Left": (1, 0), "*Right": (-1, 0)}


def _sheet_views():
    """IViews of the current sheet (not the sheet itself)."""
    out, raw = [], _dwg().GetFirstView()
    raw = C.cast(raw, "IView").GetNextView() if raw is not None else None
    while raw is not None:
        v = C.cast(raw, "IView")
        out.append(v)
        raw = v.GetNextView()
    return out


# how far each kind of annotation is DRAWN from its position point, in mm (left, bottom,
# right, top). The position is an anchor, not the symbol: a surface-finish symbol stands
# ~15 mm above it and a gtol frame runs ~40 mm to its right (read off the PDFs).
_ANN_EXTENT = {4: (5, 4, 5, 4),       # dimension text
               2: (6, 6, 6, 10),      # datum tag
               5: (1, 5, 42, 5),      # gtol frame
               7: (8, 3, 12, 16)}     # surface finish


def _ann_rect(an):
    """Sheet rectangle (mm) an annotation takes, or None when it has no position."""
    pos = an.GetPosition()
    if not pos:
        return None
    x, y = pos[0] * 1000, pos[1] * 1000
    kind = an.GetType()
    if kind == 6:                          # a note (balloons, labels) knows its extent
        ext = C.cast(an.GetSpecificAnnotation(), "INote").GetExtent()
        if ext:
            return _rect_mm(ext[0] * 1000, ext[1] * 1000, ext[3] * 1000, ext[4] * 1000)
    dl, db, dr, dt = _ANN_EXTENT.get(kind, (5, 4, 5, 4))
    return [x - dl, y - db, x + dr, y + dt]


def _footprint(v, reserve=0.0):
    """Rectangle (mm) a view really takes: its outline plus every annotation it owns
    (dimensions, notes, balloons, gtol, finish symbols, the section label), plus `reserve`
    on the left and bottom for dimensions still to come."""
    ol = v.GetOutline()
    r = _rect_mm(ol[0] * 1000, ol[1] * 1000, ol[2] * 1000, ol[3] * 1000)
    a = v.GetFirstAnnotation2()
    while a is not None:
        an = C.cast(a, "IAnnotation")
        ar = _ann_rect(an)
        if ar:
            r = [min(r[0], ar[0]), min(r[1], ar[1]), max(r[2], ar[2]), max(r[3], ar[3])]
        a = an.GetNext3()
    return [r[0] - reserve, r[1] - reserve, r[2], r[3]]


def _pos_mm(v):
    p = v.Position
    return p[0] * 1000.0, p[1] * 1000.0


def _set_pos(v, x, y):
    # MEASURED 2026-09-25: a plain tuple is marshalled shifted -- (150, 180) lands as
    # (0, 150). An explicit VT_ARRAY|VT_R8 variant lands where it is asked.
    v.Position = C.win32.VARIANT(C.pythoncom.VT_ARRAY | C.pythoncom.VT_R8,
                                 [C.mm(x), C.mm(y)])


def _layout(views, anchor, frame, blocked, gap, reserve, first_angle):
    """Try to place the views at the CURRENT scale. Returns {view name: (x, y)} or None
    when they do not fit. The front view anchors a block with its projected views
    (aligned, in 1st/3rd angle order) and the sections cut from it; the rest (pictorial,
    flat pattern, unaligned) go to the free space, top-right first."""
    info = {}
    for v in views:
        px, py = _pos_mm(v)
        fp = _footprint(v, reserve if (v.GetOrientationName() or "") not in _PICTORIAL
                        else 0.0)
        info[v.GetName2()] = (v, (fp[0] - px, fp[1] - py, fp[2] - px, fp[3] - py), (px, py))
    aname = anchor.GetName2()
    ax, ay = info[aname][2]
    chains = {(1, 0): [], (-1, 0): [], (0, -1): [], (0, 1): []}
    others = []
    for name, (v, _rel, (px, py)) in info.items():
        if name == aname:
            continue
        orient = v.GetOrientationName() or ""
        base = v.GetBaseView()
        parent = C.cast(base, "IView").GetName2() if base is not None else None
        if orient in _PROJ_FIRST:
            dx, dy = _PROJ_FIRST[orient]
            chains[(dx, dy) if first_angle else (-dx, -dy)].append(name)
        elif parent == aname and v.Type in (_VIEW_SECTION, _VIEW_PROJECTED):
            # an aligned section sits in the front's row (vertical cut) or column
            # (horizontal cut); SW aligns it on the silhouette, not on the view centre,
            # so compare the offsets instead of demanding equality. A PROJECTED view
            # (a drawing made in SW's UI) is aligned the same way: sent to the free space
            # it stayed in its row, SW silently keeping the aligned coordinate (measured
            # 2026-09-28)
            if abs(py - ay) <= abs(px - ax):
                chains[(1, 0) if px >= ax else (-1, 0)].append(name)
            else:
                chains[(0, -1) if py <= ay else (0, 1)].append(name)
        else:
            others.append(name)
    rel = {aname: (0.0, 0.0)}
    fr = info[aname][1]
    for (dx, dy), names in chains.items():
        edge = {(1, 0): fr[2], (-1, 0): fr[0], (0, -1): fr[1], (0, 1): fr[3]}[(dx, dy)]
        for name in names:
            o = info[name][1]
            if dx == 1:
                rel[name] = (edge + gap - o[0], 0.0)
                edge = rel[name][0] + o[2]
            elif dx == -1:
                rel[name] = (edge - gap - o[2], 0.0)
                edge = rel[name][0] + o[0]
            elif dy == -1:
                rel[name] = (0.0, edge - gap - o[3])
                edge = rel[name][1] + o[1]
            else:
                rel[name] = (0.0, edge + gap - o[1])
                edge = rel[name][1] + o[3]

    def rect(name, x, y):
        o = info[name][1]
        return [x + o[0], y + o[1], x + o[2], y + o[3]]

    # a row view that hangs down may collide with the column below: push the column down
    row = [n for n in rel if rel[n][1] == 0.0]
    for _ in range(6):
        clash = [(c, r) for c in chains[(0, -1)] for r in row if r != aname
                 and _rects_overlap(rect(c, *rel[c]), rect(r, *rel[r]))]
        if not clash:
            break
        for c in chains[(0, -1)]:
            rel[c] = (rel[c][0], rel[c][1] - 10.0)
    block = [min(rect(n, *p)[0] for n, p in rel.items()),
             min(rect(n, *p)[1] for n, p in rel.items()),
             max(rect(n, *p)[2] for n, p in rel.items()),
             max(rect(n, *p)[3] for n, p in rel.items())]
    # the block goes to the top-left corner of the frame
    ox, oy = frame[0] + gap - block[0], frame[3] - gap - block[3]
    placed = {n: (x + ox, y + oy) for n, (x, y) in rel.items()}
    rects = [rect(n, *p) for n, p in placed.items()]
    if any(not _inside(r, frame) or any(_rects_overlap(r, b) for b in blocked)
           for r in rects):
        return None
    # the others: largest first, scanning the free space from the top-right corner
    for name in sorted(others, key=lambda n: -(info[n][1][2] - info[n][1][0])
                       * (info[n][1][3] - info[n][1][1])):
        o = info[name][1]
        w, h = o[2] - o[0], o[3] - o[1]
        spot = None
        y = frame[3] - gap
        while spot is None and y - h >= frame[1]:
            x = frame[2] - gap
            while x - w >= frame[0]:
                r = [x - w, y - h, x, y]
                if not any(_rects_overlap(r, b, shrink=-gap / 2) for b in blocked + rects):
                    spot = r
                    break
                x -= 4.0
            y -= 4.0
        if spot is None:
            return None
        placed[name] = (spot[0] - o[0], spot[1] - o[1])
        rects.append(spot)
    return placed


def arrange(*, gap_mm: float = 8.0, scale: str = "auto", reserve_mm: float = 0.0) -> dict:
    """LAYOUT the current sheet like a drafter: pick the sheet scale and place every view.
      - the FRONT view anchors a block with the views projected from it (top/bottom/left/
        right in the sheet's 1st or 3rd angle order, aligned with the front) and the
        sections cut from it, in the frame's top-left corner;
      - pictorial views (iso), flat patterns and the rest take the free space, top-right
        first;
      - tables (BOM, bend table) go to the frame's top-right corner, and nothing is placed
        over them or over the title block.
    Each view is measured with its annotations (dimensions, balloons, labels), so run it
    again after adding dimensions -- that second call is what keeps them readable.
      scale -> 'auto' (DEFAULT): the LARGEST ISO 5455 scale (10:1 ... 1:200) at which
               everything fits; 'keep': only move the views.
      reserve_mm -> room kept on the left and bottom of each orthographic view for
               dimensions that will be added later (e.g. 25 before auto_dimension).
    Views with their own scale (an iso at 0.5) keep their ratio to the sheet scale; a
    pictorial one is shrunk (x0.75, x0.5) before the sheet scale drops. Sections follow
    their parent's scale and stay aligned with it.
    Returns {scale: 'n:d', views: {name: [x, y]}, tables: [rects]}; raises when nothing
    fits even at 1:200."""
    md, dwg = _md(), _dwg()
    sheet = C.cast(dwg.GetCurrentSheet(), "ISheet")
    props = sheet.GetProperties2()
    first_angle = bool(props[4])
    frame, tb = _sheet_frame()
    tables = _stack_tables_top_right()
    blocked = ([tb] if tb else []) + tables
    views = _sheet_views()
    if not views:
        raise RuntimeError("arrange: the sheet has no view.")
    anchor = next((v for v in views if (v.GetOrientationName() or "") == "*Front"),
                  next((v for v in views if (v.GetOrientationName() or "")
                        not in _PICTORIAL), views[0]))
    s0 = props[2] / props[3]
    byname = {v.GetName2(): v for v in views}
    # a section / projected / auxiliary view keeps its PARENT's scale (SW creates a section
    # with a scale of its own, so on a sheet-scale change it stayed behind, measured)
    derived = {}
    for v in views:
        base = v.GetBaseView()
        if v.Type in (_VIEW_SECTION, _VIEW_PROJECTED, _VIEW_AUX) and base is not None:
            derived[v.GetName2()] = C.cast(base, "IView").GetName2()
    own = {v.GetName2(): v.ScaleDecimal / s0 for v in views
           if not v.UseSheetScale and v.GetName2() not in derived}

    pictorial = {v.GetName2() for v in views
                 if (v.GetOrientationName() or "") in _PICTORIAL}

    def apply(num, den, shrink=1.0):
        sheet.SetScale(float(num), float(den), False, False)
        for v in views:
            if v.GetName2() in own:
                k = shrink if v.GetName2() in pictorial else 1.0
                v.ScaleDecimal = own[v.GetName2()] * num / den * k
        for name, parent in derived.items():
            if parent in byname:
                _follow_parent_scale(byname[name], byname[parent])
        md.EditRebuild3()

    if scale == "keep":
        candidates = [(props[2], props[3])]
    elif scale == "auto":
        candidates = list(_ISO_SCALES)
        # start from the scale the current footprints suggest, then step down
        fps = [_footprint(v, reserve_mm) for v in views]
        need_w = sum(f[2] - f[0] for f in fps) + gap_mm * (len(fps) + 1)
        need_h = max(f[3] - f[1] for f in fps) * 2 + gap_mm * 3
        room = min((frame[2] - frame[0]) / need_w, (frame[3] - frame[1]) / need_h) * 2
        limit = s0 * room
        if any(_annotated(v) for v in views):
            # never ENLARGE an annotated sheet: SW scales the offsets of dimensions and
            # balloons with the view, so they spread out and the layout gets worse
            # (measured) -- choose the scale first (reserve_mm), then only shrink
            limit = min(limit, s0)
        start = next((i for i, (n, d) in enumerate(candidates) if n / d <= limit + 1e-9), 0)
        candidates = candidates[start:]
    else:
        raise ValueError(f"scale '{scale}' is invalid ('auto'|'keep').")
    # a pictorial view with its own scale is an illustration: shrink it before the
    # orthographic views lose scale (the clevis fitted at 1:1 once its iso went 0.8 -> 0.5)
    shrinks = (1.0, 0.75, 0.5) if scale == "auto" and pictorial & set(own) else (1.0,)
    for num, den in candidates:
        placed = None
        for k in shrinks:
            if scale == "auto":
                apply(num, den, k)
            placed = _layout(views, anchor, frame, blocked, gap_mm, reserve_mm,
                             first_angle)
            if placed is not None:
                break
        if placed is None:
            continue
        # the anchor first: views aligned to it follow it along their alignment axis
        order = [anchor.GetName2()] + [n for n in placed if n != anchor.GetName2()]
        for name in order:
            _set_pos(byname[name], *placed[name])
        md.EditRebuild3()
        md.GraphicsRedraw2()
        # report where the views ARE: an aligned view keeps SW's aligned coordinate
        # (a section sits on the silhouette), whatever was asked
        return {"scale": f"{num:g}:{den:g}",
                "views": {n: [round(c, 1) for c in _pos_mm(byname[n])] for n in placed},
                "tables": tables}
    if scale == "keep":
        # only the current scale was tried: saying "even at the smallest scale" here sent
        # the caller to a larger sheet when shrinking was enough (SW 2017, 2026-09-27:
        # the annotations added after a tight 1:1 layout no longer fit at 1:1)
        raise RuntimeError(
            f"arrange: the views do not fit at the current scale {props[2]:g}:{props[3]:g} "
            "(scale='keep' does not change it) -- call arrange() to let it shrink the "
            "scale, or use a larger sheet (new_drawing size=).")
    raise RuntimeError("arrange: the views do not fit on this sheet even at the smallest "
                       "scale -- use a larger sheet (new_drawing size=).")


# ── one view at a time: read it, aim at it, move it ─────────────────────────
# swAlignViewTypes_e. MEASURED 2026-09-28 (SW 2026): AlignWithView(ROW) keeps the view in
# its partner's row (same Y), AlignWithView(COLUMN) in its column (same X). GetAlignment
# does NOT echo the axis back: it reads 2 for ANY aligned view (row or column, projected
# or aligned by hand), 1 for a view others align to and 0 for a free one -- so the axis
# is inferred from where the view sits relative to its partner. AlignVerticalTo /
# AlignHorizontalTo return None and change nothing.
_ALIGN_ROW, _ALIGN_COLUMN = 2, 3
_ALIGN_STATE = {0: "free", 1: "free", 2: "aligned"}


def _align_axis(v, partner) -> int:
    """ROW when the view sits beside its partner, COLUMN when above/below it."""
    (x, y), (px, py) = _pos_mm(v), _pos_mm(partner)
    return _ALIGN_ROW if abs(y - py) <= abs(x - px) else _ALIGN_COLUMN


def _follow_parent_scale(v, parent):
    """Give a derived view (section, projected, auxiliary) its parent's scale."""
    aligned = v.GetAlignment() == 2
    axis = _align_axis(v, parent)
    v.ScaleDecimal = parent.ScaleDecimal
    if aligned:
        # re-scaling an aligned section keeps an offset (it scales about its own origin:
        # the section sat half the part's height off the front, measured) -- drop the
        # alignment and align it again. The axis comes from the layout, not from
        # GetAlignment, which reads 2 for a row AND for a column (see _ALIGN_ROW).
        v.RemoveAlignment()
        v.AlignWithView(axis, parent)


def _find_view(view_name: str):
    views = _sheet_views()
    for v in views:
        if v.GetName2() == view_name:
            return v
    raise ValueError(f"view '{view_name}' is not on the current sheet "
                     f"({[v.GetName2() for v in views]})")


def _to_sheet(v, p):
    """Model point (mm) -> sheet point (mm) through the view's ModelToViewTransform.
    MEASURED 2026-09-28: ArrayData = [3x3 rotation, translation (m), scale], applied to a
    ROW vector (p * R * scale + t); checked on front, top, right (2:1) and iso (1:2)."""
    a = list(C.cast(v.ModelToViewTransform, "IMathTransform").ArrayData)
    x, y, z = (c / 1000.0 for c in p)
    s = a[12]
    return ((x * a[0] + y * a[3] + z * a[6]) * s + a[9]) * 1000.0, \
           ((x * a[1] + y * a[4] + z * a[7]) * s + a[10]) * 1000.0


def _geometry_rect(v):
    """Sheet rectangle (mm) of the MODEL's bounding box seen in the view, or None where
    the model box does not describe what the view shows (detail, flat pattern). Tighter
    than GetOutline, which carries a margin (~6 mm at 1:1)."""
    if v.Type == _VIEW_DETAIL or v.IsFlatPatternView():
        return None
    doc = v.ReferencedDocument
    if doc is None:
        return None
    kind = C.cast(doc, "IModelDoc2").GetType()
    if kind == C.DOC_PART:
        box = C.cast(doc, "IPartDoc").GetPartBox(True)
    elif kind == C.DOC_ASSEMBLY:
        box = C.cast(doc, "IAssemblyDoc").GetBox(0)
    else:
        return None
    if not box:
        return None
    lo, hi = [c * 1000.0 for c in box[:3]], [c * 1000.0 for c in box[3:6]]
    pts = [_to_sheet(v, (x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
           for z in (lo[2], hi[2])]
    return _rect_mm(min(p[0] for p in pts), min(p[1] for p in pts),
                    max(p[0] for p in pts), max(p[1] for p in pts))


def _obstacles(skip=()):
    """(frame, [(label, rect)]) of the current sheet: title block, tables and every view
    footprint except the names in `skip`."""
    frame, tb = _sheet_frame()
    obs = [("title block", tb)] if tb else []
    obs += [("table", _table_rect(ta)) for ta in _all_tables()]
    obs += [(f"view {v.GetName2()}", _footprint(v)) for v in _sheet_views()
            if v.GetName2() not in skip]
    return frame, obs


def _placement_problems(name, fp, frame, obs):
    probs = []
    if not _inside(fp, frame):
        probs.append(f"view {name} (with its annotations) runs outside the frame {fp}")
    for label, r in obs:
        if _rects_overlap(fp, r, shrink=1.0):
            probs.append(f"view {name} overlaps the {label}" if label == "title block"
                         else f"view {name} overlaps {label}")
    return probs


def _free_mm(fp, frame, obs):
    """How far a footprint can travel in each direction before it touches the frame, the
    title block, a table or another view."""
    free = {"left": fp[0] - frame[0], "right": frame[2] - fp[2],
            "down": fp[1] - frame[1], "up": frame[3] - fp[3]}
    for _label, r in obs:
        if _rects_overlap(fp, r):
            continue
        if r[1] < fp[3] and fp[1] < r[3]:          # shares the row
            if r[0] >= fp[2]:
                free["right"] = min(free["right"], r[0] - fp[2])
            elif r[2] <= fp[0]:
                free["left"] = min(free["left"], fp[0] - r[2])
        if r[0] < fp[2] and fp[0] < r[2]:          # shares the column
            if r[1] >= fp[3]:
                free["up"] = min(free["up"], r[1] - fp[3])
            elif r[3] <= fp[1]:
                free["down"] = min(free["down"], fp[1] - r[3])
    return {k: round(max(val, 0.0), 1) for k, val in free.items()}


def _annotations(v):
    out, a = [], v.GetFirstAnnotation2()
    kinds = C.enum_names("swAnnotationType_e")
    while a is not None:
        an = C.cast(a, "IAnnotation")
        kind = an.GetType()
        item = {"kind": kinds.get(kind, str(kind)).replace("sw", "", 1).lower()}
        if kind == _ANN_DIMENSION:
            dim = C.cast(C.cast(an.GetSpecificAnnotation(), "IDisplayDimension")
                         .GetDimension2(0), "IDimension")
            item["name"] = dim.FullName
            item["value"] = round(dim.SystemValue * (57.29577951308232
                                                     if dim.GetType() == 1 else 1000.0), 3)
        elif kind == _ANN_NOTE:
            item["text"] = C.cast(an.GetSpecificAnnotation(), "INote").GetText()
        pos = an.GetPosition()
        if pos:
            item["position_mm"] = [round(pos[0] * 1000, 1), round(pos[1] * 1000, 1)]
        r = _ann_rect(an)
        if r:
            item["rect_mm"] = [round(c, 1) for c in r]
        out.append(item)
        a = an.GetNext3()
    return out


def _view_summary(v):
    base = v.GetBaseView()
    geo = _geometry_rect(v)
    ratio = [float(x) for x in (v.ScaleRatio or (1.0, 1.0))]
    return {"name": v.GetName2(),
            "type": C.enum_names("swDrawingViewTypes_e").get(v.Type, str(v.Type))
            .replace("swDrawing", "").replace("View", "").lower(),
            "orientation": v.GetOrientationName() or "",
            "parent": C.cast(base, "IView").GetName2() if base is not None else None,
            "position_mm": [round(c, 2) for c in _pos_mm(v)],
            "scale": f"{ratio[0]:g}:{ratio[1]:g}", "sheet_scale": bool(v.UseSheetScale),
            "alignment": _ALIGN_STATE.get(v.GetAlignment(), str(v.GetAlignment())),
            "locked": bool(v.PositionLocked),
            "geometry_mm": geo,
            "outline_mm": _rect_mm(*[c * 1000 for c in v.GetOutline()]),
            "footprint_mm": [round(c, 1) for c in _footprint(v)]}


def view_info(view_name: str = "") -> dict:
    """INSPECT the sheet or one view of it, as it is NOW (after insertions, moves, dims).
    Rectangles are [xmin, ymin, xmax, ymax] in sheet mm (origin = sheet bottom-left).
    Every view carries:
      position_mm  -> the centre of its geometry (what move_view moves);
      geometry_mm  -> the model's bounding box seen in the view (None for detail and flat
                      pattern views); tighter than outline_mm, which carries ~6 mm margin;
      footprint_mm -> outline plus every annotation it owns: the room it REALLY takes;
      alignment    -> 'aligned' (SW keeps it in the row/column of another view: it moves
                      only along that line, and follows that view) | 'free';
      locked, scale 'n:d', sheet_scale (uses the sheet's), type, orientation, parent.
    view_name='' -> the sheet map: {sheet, scale, frame_mm, titleblock_mm, tables_mm,
      views: [...], problems}. problems = views running out of the frame or overlapping
      the title block, a table or each other (footprints).
    view_name    -> that view, plus annotations [{kind, name/value or text, position_mm,
      rect_mm}], free_mm {left, right, down, up} (how far it can move before touching
      the frame, the title block, a table or another view) and its own problems.
    Use it before move_view, and model_to_sheet to aim at a point of the model."""
    md = _md()
    if md.GetType() != C.DOC_DRAWING:
        raise RuntimeError(f"view_info: the active document '{md.GetTitle()}' is not a "
                           "drawing -- activate the drawing first")
    if view_name:
        v = _find_view(view_name)
        frame, obs = _obstacles(skip=(view_name,))
        out = _view_summary(v)
        out["annotations"] = _annotations(v)
        out["free_mm"] = _free_mm(out["footprint_mm"], frame, obs)
        out["problems"] = _placement_problems(view_name, out["footprint_mm"], frame, obs)
        return out
    sheet = C.cast(_dwg().GetCurrentSheet(), "ISheet")
    props = sheet.GetProperties2()
    frame, tb = _sheet_frame()
    tables = [_table_rect(ta) for ta in _all_tables()]
    views = [_view_summary(v) for v in _sheet_views()]
    probs = []
    obs = ([("title block", tb)] if tb else []) + [("table", r) for r in tables]
    for i, v in enumerate(views):
        probs += _placement_problems(v["name"], v["footprint_mm"], frame, obs)
        for w in views[:i]:
            if _rects_overlap(v["footprint_mm"], w["footprint_mm"], shrink=1.0):
                probs.append(f"views {w['name']} and {v['name']} overlap")
    return {"sheet": sheet.GetName(), "scale": f"{props[2]:g}:{props[3]:g}",
            "frame_mm": frame, "titleblock_mm": tb, "tables_mm": tables,
            "views": views, "problems": probs}


def model_to_sheet(view_name: str, points) -> list:
    """Where a MODEL point (x, y, z in mm, the model's own coordinates) lands on the
    sheet in a view: [x, y] sheet mm. `points` = one (x, y, z) or a list of them (then a
    list comes back). Use it to aim dimension/datum/gtol picks at an edge or a hole
    instead of estimating from the view centre. Exact for every view that shows the
    model through a plain projection (standard, named, projected, section, auxiliary,
    pictorial); a detail view is cropped, so check the result against its outline."""
    v = _find_view(view_name)
    single = len(points) == 3 and all(isinstance(c, (int, float)) for c in points)
    pts = [points] if single else list(points)
    out = [[round(c, 3) for c in _to_sheet(v, p)] for p in pts]
    return out[0] if single else out


_SIDES = ("right", "left", "above", "below")


def move_view(view_name: str, to=None, *, by=None, next_to: str = "",
              side: Literal[_SIDES] = "right", gap_mm: float = 8.0,
              break_alignment: bool = False) -> dict:
    """MOVE one view on the sheet and report where everything really went. Give ONE of:
      to=(x, y)    -> put the view's geometry centre there (sheet mm);
      by=(dx, dy)  -> shift it;
      next_to=name -> put it beside that view (side 'right'|'left'|'above'|'below'), gap_mm
                      between the footprints (annotations included), centred on the
                      other view's row/column -- how a projected view sits.
    An ALIGNED view (a projected view, a section, one aligned with align_view) moves only
    along its row/column: a move off that line raises (the view is put back) unless
    break_alignment=True, which frees it first. Views aligned to this one follow it
    (reported in moved_with). A view with a locked position raises.
    Returns {view, position_mm, moved_with {name: [dx, dy]}, footprint_mm, free_mm,
    problems}; problems lists what now runs out of the frame or overlaps (the move is
    kept -- fix it, or run arrange())."""
    if sum(x is not None and x != "" for x in (to, by, next_to)) != 1:
        raise ValueError("move_view: give exactly one of to=, by= or next_to=.")
    md = _md()
    v = _find_view(view_name)
    if v.PositionLocked:
        raise RuntimeError(f"move_view: the position of view '{view_name}' is locked "
                           "(Lock View Position) -- unlock it in SOLIDWORKS first.")
    x0, y0 = _pos_mm(v)
    if to is not None:
        tx, ty = float(to[0]), float(to[1])
    elif by is not None:
        tx, ty = x0 + float(by[0]), y0 + float(by[1])
    else:
        if side not in _SIDES:
            raise ValueError(f"side '{side}' is invalid ({list(_SIDES)}).")
        other = _find_view(next_to)
        ox, oy = _pos_mm(other)
        of, fp = _footprint(other), _footprint(v)
        rel = (fp[0] - x0, fp[1] - y0, fp[2] - x0, fp[3] - y0)
        tx, ty = {"right": (of[2] + gap_mm - rel[0], oy),
                  "left": (of[0] - gap_mm - rel[2], oy),
                  "above": (ox, of[3] + gap_mm - rel[1]),
                  "below": (ox, of[1] - gap_mm - rel[3])}[side]
    before = {w.GetName2(): _pos_mm(w) for w in _sheet_views()}
    if break_alignment and v.GetAlignment() == 2:
        v.RemoveAlignment()
    _set_pos(v, tx, ty)
    md.EditRebuild3()
    lx, ly = _pos_mm(v)
    if abs(lx - tx) > 0.05 or abs(ly - ty) > 0.05:
        # MEASURED 2026-09-28: SW does not refuse a move that breaks an alignment -- it
        # keeps the aligned coordinate and reports nothing (asked y=120, stayed at 180).
        # A silent partial move is worse than none: put it back and say why.
        _set_pos(v, x0, y0)
        md.EditRebuild3()
        held = "X" if abs(lx - tx) > 0.05 else "Y"
        free = "Y" if held == "X" else "X"
        raise RuntimeError(
            f"move_view: '{view_name}' is aligned with another view, so SW keeps its {held} "
            f"and moves it only along {free}: asked ({tx:.1f}, {ty:.1f}), it went to "
            f"({lx:.1f}, {ly:.1f}); it was put back at ({x0:.1f}, {y0:.1f}). Move it "
            "along its line, move the view it is aligned with (it follows), or pass "
            "break_alignment=True.")
    moved = {}
    for w in _sheet_views():
        n = w.GetName2()
        if n != view_name and n in before:
            (bx, by_), (ax, ay) = before[n], _pos_mm(w)
            if abs(ax - bx) > 0.05 or abs(ay - by_) > 0.05:
                moved[n] = [round(ax - bx, 2), round(ay - by_, 2)]
    md.GraphicsRedraw2()
    frame, obs = _obstacles(skip=(view_name, *moved))
    fp = [round(c, 1) for c in _footprint(v)]
    probs = _placement_problems(view_name, fp, frame, obs)
    for n in moved:
        w = _find_view(n)
        probs += _placement_problems(n, _footprint(w), *_obstacles(skip=(n, view_name,
                                                                         *moved)))
    return {"view": view_name, "position_mm": [round(lx, 2), round(ly, 2)],
            "moved_with": moved, "footprint_mm": fp,
            "free_mm": _free_mm(fp, frame, obs), "problems": probs}


def align_view(view_name: str, to: str = "", *,
               how: Literal["column", "row", "none"] = "column") -> dict:
    """ALIGN a view with another one, so it stays in that view's column (how='column':
    same X -- a top/bottom view under the front) or row (how='row': same Y -- a
    left/right view), and follows it when that view moves. how='none' frees the view.
    add_view places NAMED views, which SW does not align: after moving the front view
    the top view stays behind unless it is aligned. Checks the result by reading the
    positions back. Returns {view, to, how, position_mm, alignment}."""
    md = _md()
    v = _find_view(view_name)
    if how == "none":
        v.RemoveAlignment()
        md.EditRebuild3()
        if v.GetAlignment() == 2:
            raise RuntimeError(f"align_view: '{view_name}' is still aligned (SW kept a "
                               "projection alignment it would not remove).")
        return {"view": view_name, "to": None, "how": "none",
                "position_mm": [round(c, 2) for c in _pos_mm(v)], "alignment": "free"}
    if how not in ("column", "row"):
        raise ValueError(f"how '{how}' is invalid ('column'|'row'|'none').")
    if not to or to == view_name:
        raise ValueError("align_view: give the view to align with (to=).")
    partner = _find_view(to)
    if v.GetAlignment() == 2:
        v.RemoveAlignment()
    v.AlignWithView(_ALIGN_COLUMN if how == "column" else _ALIGN_ROW, partner)
    md.EditRebuild3()
    (x, y), (px, py) = _pos_mm(v), _pos_mm(partner)
    ok = v.GetAlignment() == 2 and (abs(x - px) < 0.05 if how == "column"
                                    else abs(y - py) < 0.05)
    if not ok:
        raise RuntimeError(f"align_view: '{view_name}' did not align with '{to}' "
                           f"({how}): it sits at ({x:.1f}, {y:.1f}), '{to}' at "
                           f"({px:.1f}, {py:.1f}).")
    md.GraphicsRedraw2()
    return {"view": view_name, "to": to, "how": how,
            "position_mm": [round(x, 2), round(y, 2)], "alignment": "aligned"}


def set_view_scale(view_name: str, scale="sheet") -> dict:
    """Change ONE view's scale: a number (0.5 = 1:2, 2 = 2:1) or 'sheet' (follow the sheet
    scale again). The views derived from it (sections, projected) take the same scale and
    stay aligned. The view keeps its centre; its annotations scale with it, so check the
    returned problems (or run arrange(scale='keep')). For the WHOLE sheet use
    sheet_scale() or arrange(). Returns {view, scale 'n:d', followers, footprint_mm,
    problems}."""
    md = _md()
    v = _find_view(view_name)
    if scale == "sheet":
        v.UseSheetScale = True
    else:
        s = float(scale)
        if s <= 0:
            raise ValueError("set_view_scale: the scale must be > 0 (or 'sheet').")
        v.ScaleDecimal = s
    followers = []
    for w in _sheet_views():
        base = w.GetBaseView()
        if base is not None and C.cast(base, "IView").GetName2() == view_name \
                and w.Type in (_VIEW_SECTION, _VIEW_PROJECTED, _VIEW_AUX):
            _follow_parent_scale(w, v)
            followers.append(w.GetName2())
    md.EditRebuild3()
    md.GraphicsRedraw2()
    ratio = [float(x) for x in (v.ScaleRatio or (1.0, 1.0))]
    if scale != "sheet" and abs(v.ScaleDecimal - float(scale)) > 1e-6:
        raise RuntimeError(f"set_view_scale: '{view_name}' reads {v.ScaleDecimal:g} after "
                           f"asking {scale}.")
    frame, obs = _obstacles(skip=(view_name, *followers))
    fp = [round(c, 1) for c in _footprint(v)]
    probs = _placement_problems(view_name, fp, frame, obs)
    for n in followers:
        probs += _placement_problems(n, _footprint(_find_view(n)),
                                     *_obstacles(skip=(n, view_name, *followers)))
    return {"view": view_name, "scale": f"{ratio[0]:g}:{ratio[1]:g}",
            "followers": followers, "footprint_mm": fp, "problems": probs}


def delete_view(view_name: str) -> list:
    """DELETE a view from the sheet (with its annotations). Returns the names of every view
    that disappeared -- SW may take the views derived from it along."""
    md = _md()
    _find_view(view_name)
    before = {v.GetName2() for v in _sheet_views()}
    ext = C.cast(md.Extension, "IModelDocExtension")
    md.ClearSelection2(True)
    if not ext.SelectByID2(view_name, "DRAWINGVIEW", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError(f"delete_view: could not select view '{view_name}'.")
    ext.DeleteSelection2(0)
    md.ClearSelection2(True)
    gone = sorted(before - {v.GetName2() for v in _sheet_views()})
    if view_name not in gone:
        raise RuntimeError(f"delete_view: '{view_name}' is still on the sheet.")
    return gone


# ── quality: is this drawing USABLE, not just created ─────────────────────────
# ISO 5457 border of SW's ISO sheet formats: 20 mm on the binding (left) edge, 10 mm on
# the others (measured on the a3 - iso.slddrt PDF: frame at x 20..410, y 10..287).
_FRAME_MM = (20.0, 10.0, 10.0, 10.0)      # left, bottom, right, top
_ANN_DIMENSION, _ANN_TABLE = 4, 14         # swAnnotationType_e
# the other annotations whose position must stay on the drawing area
_ANN_PLACED = {2: "datum", 5: "gtol", 6: "note", 7: "surface finish", 8: "weld symbol"}
# title block fields, by the property each ISO sheet-format note links to
_TITLEBLOCK = {"title": "Description", "material": "Material", "weight": "Weight",
               "number": "SW-File Name", "drawn_by": "DrawnBy", "drawn_date": "DrawnDate",
               "revision": "Revision", "company": "COMPANYNAME"}


def _rect_mm(x0, y0, x1, y1):
    return [round(min(x0, x1), 2), round(min(y0, y1), 2), round(max(x0, x1), 2),
            round(max(y0, y1), 2)]


def _rects_overlap(a, b, shrink=0.0):
    return (a[0] + shrink < b[2] - shrink and b[0] + shrink < a[2] - shrink
            and a[1] + shrink < b[3] - shrink and b[1] + shrink < a[3] - shrink)


def _inside(r, box):
    return r[0] >= box[0] - 0.5 and r[1] >= box[1] - 0.5 and r[2] <= box[2] + 0.5 \
        and r[3] <= box[3] + 0.5


def _titleblock(sheet_view):
    """(fields, rect) of the title block: the evaluated text of each $PRP-linked note of
    the sheet format, keyed as in _TITLEBLOCK, and the rectangle that holds them."""
    fields, rect = {}, None
    n = sheet_view.GetFirstNote()
    while n is not None:
        note = C.cast(n, "INote")
        link = note.PropertyLinkedText or ""
        if "$PRP" in link:
            text = (note.GetText() or "").strip()
            for key, prop in _TITLEBLOCK.items():
                if f'"{prop}"' in link and key not in fields:
                    # 'PESO: $PRP:"Weight"...' -> keep only what the property filled in
                    label = link.split("$PRP")[0]
                    fields[key] = text[len(label):].strip() if text.startswith(label) \
                        else text
            ext = note.GetExtent()
            if ext:
                r = _rect_mm(ext[0] * 1000, ext[1] * 1000, ext[3] * 1000, ext[4] * 1000)
                rect = r if rect is None else [min(rect[0], r[0]), min(rect[1], r[1]),
                                               max(rect[2], r[2]), max(rect[3], r[3])]
        n = note.GetNext()
    return fields, rect


def _table_rect(ta):
    """Sheet rectangle of a table annotation (anchor = top-left corner)."""
    ann = C.cast(ta.GetAnnotation(), "IAnnotation")
    x, y = ann.GetPosition()[:2]
    w = sum(ta.GetColumnWidth(c) for c in range(ta.ColumnCount))
    h = sum(ta.GetRowHeight(r) for r in range(ta.RowCount))
    return _rect_mm(x * 1000, y * 1000, (x + w) * 1000, (y - h) * 1000)


def _model_dimensions(model):
    """{short name: value} of the DRIVING dimensions of a part (features and their
    sketches); value in mm, or degrees for an angle."""
    names = {}
    feat = model.FirstFeature()
    while feat is not None:
        f = C.cast(feat, "IFeature")
        stack = [f]
        sub = f.GetFirstSubFeature()
        while sub is not None:
            stack.append(C.cast(sub, "IFeature"))
            sub = C.cast(sub, "IFeature").GetNextSubFeature()
        for g in stack:
            dd = g.GetFirstDisplayDimension()
            while dd is not None:
                dim = C.cast(C.cast(dd, "IDisplayDimension").GetDimension2(0), "IDimension")
                if dim.DrivenState == 2:
                    key = dim.FullName.split("@")[0] + "@" + dim.FullName.split("@")[1]
                    names[key] = round(dim.SystemValue * (57.29577951308232
                                                          if dim.GetType() == 1
                                                          else 1000.0), 3)
                dd = g.GetNextDisplayDimension(dd)
        feat = f.GetNextFeature()
    return names


# ── what each dimension really draws, and what it runs into ──────────────────
# The dimension's GetPosition is one point of its text; the text, the arrows and the
# extension lines are elsewhere. IDisplayDimension.GetDisplayData has them (MEASURED
# 2026-09-28, SW 2026): per text piece the position (left end of the baseline, sheet m),
# the height, the angle and -- through GetTextInBoxWidthAtIndex, even with no box -- the
# width (' 120 ' = 10.34 mm, and the next piece starts exactly there); GetLineAtIndex3 =
# [4 style values, x1, y1, z1, x2, y2, z2] for the dimension and extension lines.
_TEXT_PAD = 0.25     # mm shaved off each text box: its height includes the line leading


def _text_poly(pos, width, height, angle, pad=_TEXT_PAD):
    """Oriented rectangle (4 corners, mm) of one text piece."""
    c, s = math.cos(angle), math.sin(angle)
    x, y = pos[0] * 1000, pos[1] * 1000
    w, h = width * 1000, height * 1000
    if w <= 2 * pad or h <= 2 * pad:
        return None
    ux, uy, vx, vy = c, s, -s, c
    x0, y0 = x + pad * ux + pad * vx, y + pad * uy + pad * vy
    w, h = w - 2 * pad, h - 2 * pad
    return [(x0, y0), (x0 + w * ux, y0 + w * uy),
            (x0 + w * ux + h * vx, y0 + w * uy + h * vy), (x0 + h * vx, y0 + h * vy)]


def _dim_graphics(an):
    """(text polygons, line segments) of a dimension annotation, sheet mm. Blank pieces
    and the padding spaces around a value are left out."""
    data = C.cast(C.cast(an.GetSpecificAnnotation(), "IDisplayDimension").GetDisplayData(),
                  "IDisplayData")
    texts, segs = [], []
    if data is None:
        return texts, segs
    for i in range(data.GetTextCount()):
        text = data.GetTextAtIndex(i) or ""
        if not text.strip():
            continue
        # the padding spaces SW puts around a value (' 40 ') are inside the piece and its
        # width, but draw nothing: in an ANSI-dimensioned drawing (SW 2017 laptop) the
        # trailing one of ' 40 ' reached the next dimension's extension line (false clash). A space is 0.3695 x the
        # text height (measured: 1.293 at 3.5 mm on SW 2026, 1.173 at 3.175 on SW 2017)
        pos, h = list(data.GetTextPositionAtIndex(i)), data.GetTextHeightAtIndex(i)
        ang, space = data.GetTextAngleAtIndex(i), 0.3695 * h
        lead, trail = len(text) - len(text.lstrip(" ")), len(text) - len(text.rstrip(" "))
        pos[0] += lead * space * math.cos(ang)
        pos[1] += lead * space * math.sin(ang)
        p = _text_poly(pos, data.GetTextInBoxWidthAtIndex(i) - (lead + trail) * space, h,
                       ang)
        if p:
            texts.append(p)
    for i in range(data.GetLineCount()):
        ln = data.GetLineAtIndex3(i)
        if ln and len(ln) >= 10:
            seg = (ln[4] * 1000, ln[5] * 1000, ln[7] * 1000, ln[8] * 1000)
            if abs(seg[0] - seg[2]) + abs(seg[1] - seg[3]) > 1e-6:
                segs.append(seg)
    return texts, segs


def _view_edges(v):
    """Every edge the view draws, as sheet segments (mm). GetPolylines5(1) returns, per
    edge, [type, n, n geometry values, 6 style values, point count, points (x, y, z)...]
    in VIEW coordinates: sheet = Position + point * scale (MEASURED 2026-09-28: the front
    view of a 120 mm block runs -0.06..0.06 about its Position)."""
    raw = list(v.GetPolylines5(1) or ())
    px, py = v.Position[0], v.Position[1]
    k = v.ScaleDecimal or 1.0
    segs, i = [], 0
    while i + 2 <= len(raw):
        n_geom = int(raw[i + 1])
        i += 2 + n_geom + 6
        if i >= len(raw):
            break
        n_pts = int(raw[i])
        pts = raw[i + 1:i + 1 + 3 * n_pts]
        i += 1 + 3 * n_pts
        xy = [((px + pts[j] * k) * 1000, (py + pts[j + 1] * k) * 1000)
              for j in range(0, len(pts) - 2, 3)]
        segs += [(a[0], a[1], b[0], b[1]) for a, b in zip(xy, xy[1:])]
    return segs


def _poly_box(p):
    xs, ys = [q[0] for q in p], [q[1] for q in p]
    return [min(xs), min(ys), max(xs), max(ys)]


def _separated(a, b):
    """Separating-axis test for two convex polygons (a segment is a 2-point polygon)."""
    for poly in (a, b):
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            nx, ny = y1 - y2, x2 - x1
            if abs(nx) + abs(ny) < 1e-12:
                continue
            pa = [nx * x + ny * y for x, y in a]
            pb = [nx * x + ny * y for x, y in b]
            if max(pa) <= min(pb) or max(pb) <= min(pa):
                return True
    return False


def _hits(poly, segs):
    """Does the text polygon touch any of the segments?"""
    box = _poly_box(poly)
    for s in segs:
        if max(s[0], s[2]) < box[0] or min(s[0], s[2]) > box[2] \
                or max(s[1], s[3]) < box[1] or min(s[1], s[3]) > box[3]:
            continue
        if not _separated(poly, [(s[0], s[1]), (s[2], s[3])]):
            return True
    return False


def _dimension_clashes(views, frame, tb):
    """(problems, clashes) of the dimensions of these views: a text or line outside the
    frame, on the title block, a text over another dimension's text or lines, a text
    over a balloon/label, a text over the edges of a view."""
    dims, notes, edges = [], [], {}
    for v in views:
        vname = v.GetName2()
        a = v.GetFirstAnnotation2()
        while a is not None:
            an = C.cast(a, "IAnnotation")
            kind = an.GetType()
            if kind == _ANN_DIMENSION:
                dim = C.cast(C.cast(an.GetSpecificAnnotation(), "IDisplayDimension")
                             .GetDimension2(0), "IDimension")
                texts, segs = _dim_graphics(an)
                # 'D1@Sketch1 (Drawing View2)': the model name repeats on every view
                # that shows the dimension, and the file suffix is noise
                name = "@".join(dim.FullName.split("@")[:2]) + f" ({vname})"
                dims.append({"name": name, "view": vname, "texts": texts, "segs": segs})
            elif kind == _ANN_NOTE:
                r = _ann_rect(an)
                if r:
                    text = (C.cast(an.GetSpecificAnnotation(), "INote").GetText() or "")
                    notes.append((f"note '{text.strip()[:20]}' of {vname}", r))
            a = an.GetNext3()
        ol = v.GetOutline()
        edges[vname] = (_rect_mm(ol[0] * 1000, ol[1] * 1000, ol[2] * 1000, ol[3] * 1000),
                        _view_edges(v))
    probs, clashes = [], []

    def add(kind, a, b, text, where):
        clashes.append({"kind": kind, "a": a, "b": b,
                        "at_mm": [round(c, 1) for c in where]})
        probs.append(text)

    for i, d in enumerate(dims):
        label = f"dimension {d['name']}"
        pts = [q for p in d["texts"] for q in p] + \
              [q for s in d["segs"] for q in ((s[0], s[1]), (s[2], s[3]))]
        out = [q for q in pts if not _inside([q[0], q[1], q[0], q[1]], frame)]
        if out:
            add("outside_frame", d["name"], None,
                f"{label} runs outside the frame (at {out[0][0]:.1f}, {out[0][1]:.1f})",
                out[0])
        if tb and any(not _separated(p, [(tb[0], tb[1]), (tb[2], tb[1]), (tb[2], tb[3]),
                                         (tb[0], tb[3])]) for p in d["texts"]):
            add("title_block", d["name"], None, f"{label} is on the title block",
                _poly_box(d["texts"][0])[:2])
        for e in dims[i + 1:]:
            hit = next((p for p in d["texts"] for q in e["texts"]
                        if not _separated(p, q)), None)
            if hit:
                add("text_over_text", d["name"], e["name"],
                    f"{label} and dimension {e['name']} overlap (texts)", hit[0])
                continue
            for x, y in ((d, e), (e, d)):
                hit = next((p for p in x["texts"] if _hits(p, y["segs"])), None)
                if hit:
                    add("text_over_line", x["name"], y["name"],
                        f"the text of dimension {x['name']} crosses the lines of "
                        f"dimension {y['name']}", hit[0])
                    break
        for nlabel, r in notes:
            rp = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
            hit = next((p for p in d["texts"] if not _separated(p, rp)), None)
            if hit:
                add("text_over_note", d["name"], nlabel,
                    f"{label} overlaps the {nlabel}", hit[0])
        for vname, (ol, segs) in edges.items():
            hit = next((p for p in d["texts"]
                        if _rects_overlap(_poly_box(p), ol) and _hits(p, segs)), None)
            if hit:
                add("text_over_view", d["name"], vname,
                    f"the text of {label} crosses the edges of view {vname}", hit[0])
    return probs, clashes


# ── the title block: does every filled-in field fit its cell ─────────────────
def _template_lines(sheet):
    """Line segments (mm) of the sheet format: border and title-block cells, in sheet
    coordinates (MEASURED 2026-09-28: the a3 - iso frame reads 20..410 x 10..287)."""
    sk = sheet.GetTemplateSketch()
    if sk is None:
        return []
    # ISketch.GetLines = 7 values per line [type, x1, y1, z1, x2, y2, z2] in ONE call; the
    # walk over GetSketchSegments + 4 casts per line took 2.3 s for the 61 lines of an
    # A3 format (measured 2026-09-28)
    raw = list(C.cast(sk, "ISketch").GetLines() or ())
    return [(raw[i + 1] * 1000, raw[i + 2] * 1000, raw[i + 4] * 1000, raw[i + 5] * 1000)
            for i in range(0, len(raw) - 6, 7)]


def _cell(lines, x, y):
    """The smallest box of sheet-format lines around (x, y), or None when it is open."""
    left = right = down = up = None
    for x1, y1, x2, y2 in lines:
        if abs(x1 - x2) < 0.01 and min(y1, y2) - 0.01 <= y <= max(y1, y2) + 0.01:
            if x1 <= x and (left is None or x1 > left):
                left = x1
            if x1 >= x and (right is None or x1 < right):
                right = x1
        if abs(y1 - y2) < 0.01 and min(x1, x2) - 0.01 <= x <= max(x1, x2) + 0.01:
            if y1 <= y and (down is None or y1 > down):
                down = y1
            if y1 >= y and (up is None or y1 < up):
                up = y1
    if None in (left, right, down, up):
        return None
    return [left, down, right, up]


def _titleblock_overflow(sheet_view, sheet):
    """[{field, text, rect_mm, cell_mm}] of each property-linked title-block note whose
    text runs out of its cell (a long title over the next cell or the border)."""
    lines = _template_lines(sheet)
    if not lines:
        return []
    out = []
    n = sheet_view.GetFirstNote()
    while n is not None:
        note = C.cast(n, "INote")
        link = note.PropertyLinkedText or ""
        text = (note.GetText() or "").strip()
        ext = note.GetExtent()
        if "$PRP" in link and text and ext:
            r = _rect_mm(ext[0] * 1000, ext[1] * 1000, ext[3] * 1000, ext[4] * 1000)
            tp = note.GetTextPoint2()
            # the text point is on the text's top edge (and on its left edge when it is
            # left-justified): step towards the text's centre so it is inside the cell
            ax = (tp[0] * 1000 + (r[0] + r[2]) / 2) / 2 if tp else (r[0] + r[2]) / 2
            ay = (tp[1] * 1000 + (r[1] + r[3]) / 2) / 2 if tp else (r[1] + r[3]) / 2
            cell = _cell(lines, ax, ay)
            # no slack beyond rounding: 0.2 mm past the line is already text ON the line
            # in the PDF (bearing_support's number and its ISO date, 2026-09-28); the
            # other fields keep >= 1 mm to their cell
            tol = 0.05
            if cell and not (r[0] >= cell[0] - tol and r[1] >= cell[1] - tol
                             and r[2] <= cell[2] + tol and r[3] <= cell[3] + tol):
                field = link.split("$PRP")[1].split('"')[1] if '"' in link else link
                out.append({"field": field, "text": text, "rect_mm": r,
                            "cell_mm": [round(c, 1) for c in cell]})
        n = note.GetNext()
    return out


def quality() -> dict:
    """READ how usable the active drawing is -- the checks a drafter does by eye, as data.
    Every sheet is read; each problem is a string in `problems` (empty = nothing found).
      sheet       -> size_mm, frame_mm (inside the border), titleblock_mm.
      views       -> name, type, outline_mm, dimensions (count on the view).
      titleblock  -> the evaluated fields: title, material, weight, number, drawn_by, ...
                     (a 'DrawN' number means the drawing was never saved).
      tables      -> kind, rect_mm, rows (displayed text), for BOM and bend tables.
      balloons    -> the text of each BOM balloon ('*' = not linked to a BOM row).
      dimensions  -> on_drawing, values (mm / deg, one per dimension), and for a PART
                     view the model's driving dimensions that no view shows
                     (missing_model_dims -- empty when the model has none, which is why
                     `values` is the check for a model made without sketch dimensions)
                     and the SIZES of the model no dimension states
                     (missing_model_values: one Ø4.17 covers a pattern of 300 holes).
                     clashes: [{kind, a, b, at_mm}] read from what each dimension
                     DRAWS (text boxes, dimension and extension lines), kind =
                     outside_frame | title_block | text_over_text | text_over_line (a
                     text crossing another dimension's lines) | text_over_note (balloon,
                     label) | text_over_view (a text crossing the edges of a view).
      titleblock_overflow -> [{field, text, rect_mm, cell_mm}]: a filled-in title-block
                     field whose text runs out of its cell (a long title).
    Problems flagged: views overlapping each other or the title block, views/tables/
    dimensions outside the frame, tables over a view or the title block, every dimension
    clash above, title-block text out of its cell, an empty BOM, balloons without an
    item number, empty title-block fields (title, material for a part, number), views
    without any dimension on a part drawing (iso excepted).
    It does not judge whether the dimensioning is COMPLETE for manufacture -- only what
    can be measured; look at the exported PDF as well."""
    md = _md()
    if md.GetType() != C.DOC_DRAWING:
        raise RuntimeError(f"quality: '{md.GetTitle()}' is not a drawing.")
    dwg = _dwg()
    out = {"sheets": [], "views": [], "tables": [], "balloons": [], "titleblock": {},
           "dimensions": {"on_drawing": 0, "values": [], "missing_model_dims": [],
                          "clashes": []},
           "titleblock_overflow": [], "problems": []}
    probs = out["problems"]
    shown, model_dims, part_views = set(), {}, 0
    for sheet in dwg.GetViews() or []:
        sheet = list(sheet or [])
        if not sheet:
            continue
        sv = C.cast(sheet[0], "IView")
        sh = C.cast(dwg.Sheet(sv.Name), "ISheet")
        props = sh.GetProperties2()
        w, h = props[5] * 1000, props[6] * 1000
        frame = [_FRAME_MM[0], _FRAME_MM[1], w - _FRAME_MM[2], h - _FRAME_MM[3]]
        fields, tb = _titleblock(sv)
        if tb is not None:
            tb = [tb[0] - 2, frame[1], frame[2], tb[3] + 2]    # the block runs to the frame
        out["sheets"].append({"name": sv.Name, "size_mm": [round(w, 1), round(h, 1)],
                              "frame_mm": frame, "titleblock_mm": tb})
        if not out["titleblock"]:
            out["titleblock"] = fields
        rects = []
        for raw in sheet[1:]:
            v = C.cast(raw, "IView")
            name = v.GetName2()
            ol = v.GetOutline()
            r = _rect_mm(ol[0] * 1000, ol[1] * 1000, ol[2] * 1000, ol[3] * 1000)
            ndim, a = 0, v.GetFirstAnnotation2()
            while a is not None:
                an = C.cast(a, "IAnnotation")
                kind = an.GetType()
                if kind == _ANN_DIMENSION:
                    ndim += 1
                    dim = C.cast(C.cast(an.GetSpecificAnnotation(), "IDisplayDimension")
                                 .GetDimension2(0), "IDimension")
                    fn = dim.FullName.split("@")
                    shown.add("@".join(fn[:2]))
                    out["dimensions"]["values"].append(
                        round(dim.SystemValue * (57.29577951308232 if dim.GetType() == 1
                                                 else 1000.0), 3))
                else:
                    if kind == _ANN_NOTE:
                        note = C.cast(an.GetSpecificAnnotation(), "INote")
                        if note.IsBomBalloon():
                            out["balloons"].append(note.GetText())
                    ar = _ann_rect(an) if kind in _ANN_PLACED else None
                    if ar:
                        label = _ANN_PLACED[kind]
                        if not _inside(ar, frame):
                            probs.append(f"{label} of {name} is outside the frame")
                        elif tb and _rects_overlap(ar, tb):
                            probs.append(f"{label} of {name} is on the title block")
                a = an.GetNext3()
            out["dimensions"]["on_drawing"] += ndim
            vtype = C.enum_names("swDrawingViewTypes_e").get(v.Type, str(v.Type))
            orient = v.GetOrientationName() or ""
            out["views"].append({"name": name, "type": vtype, "orientation": orient,
                                 "outline_mm": r, "dimensions": ndim})
            model = v.ReferencedDocument
            if model is not None:
                model = C.cast(model, "IModelDoc2")
                if model.GetType() == C.DOC_PART:
                    part_views += 1
                    model_dims.update(_model_dimensions(model))
                    if ndim == 0 and "Isometric" not in orient and not v.IsFlatPatternView():
                        probs.append(f"view {name} ({orient or vtype}) has no dimension")
            if not _inside(r, frame):
                probs.append(f"view {name} runs outside the frame {r}")
            if tb and _rects_overlap(r, tb, shrink=3):
                probs.append(f"view {name} overlaps the title block")
            for other, orr in rects:
                if _rects_overlap(r, orr, shrink=4):   # the outline carries ~6 mm margin
                    probs.append(f"views {other} and {name} overlap")
            rects.append((name, r))
        # the dimensions as DRAWN (text, arrows, extension lines), not their anchor point
        dprobs, clashes = _dimension_clashes([C.cast(raw, "IView") for raw in sheet[1:]],
                                             frame, tb)
        probs += dprobs
        out["dimensions"]["clashes"] += clashes
        for f in _titleblock_overflow(sv, sh):
            out["titleblock_overflow"].append(f)
            probs.append(f"title block: '{f['text']}' ({f['field']}) runs out of its cell "
                         f"{f['cell_mm']}")
        if sv.Name == C.cast(dwg.GetCurrentSheet(), "ISheet").GetName():
            for ta in _all_tables():
                kind = C.enum_names("swTableAnnotationType_e").get(ta.Type, str(ta.Type))
                kind = kind.replace("swTableAnnotation_", "")
                tr = _table_rect(ta)
                rows = [[ta.DisplayedText(i, j) for j in range(ta.ColumnCount)]
                        for i in range(ta.RowCount)]
                out["tables"].append({"kind": kind, "rect_mm": tr, "rows": rows})
                if not _inside(tr, frame):
                    probs.append(f"{kind} table runs outside the frame {tr}")
                if tb and _rects_overlap(tr, tb):
                    probs.append(f"{kind} table overlaps the title block")
                for vn, vr in rects:
                    if _rects_overlap(tr, vr, shrink=4):
                        probs.append(f"{kind} table overlaps view {vn}")
                if kind == "BillOfMaterials" and len(rows) < 2:
                    probs.append("the BOM has no item rows")
    if part_views:
        missing = sorted(set(model_dims) - shown)
        out["dimensions"]["model_driving"] = len(model_dims)
        out["dimensions"]["missing_model_dims"] = missing
        # the same SIZE stated once covers every feature that repeats it (a hole pattern):
        # the values of the model no dimension on the drawing states
        vals = out["dimensions"]["values"]
        out["dimensions"]["missing_model_values"] = sorted(
            {v for v in model_dims.values() if v > 0
             and not any(abs(v - w) < 0.005 for w in vals)})
    bad = [b for b in out["balloons"] if not b.strip().isdigit()]
    if bad:
        probs.append(f"{len(bad)} balloon(s) without an item number: {bad[:5]}")
    tbk = out["titleblock"]
    for key in ("title", "number") + (("material",) if part_views else ()):
        if not tbk.get(key):
            probs.append(f"title block: '{key}' is empty")
    if "not specified" in tbk.get("material", "").lower():
        probs.append("title block: the part has no material assigned "
                     f"('{tbk['material']}') -- sw_parts.set_material")
    if tbk.get("number", "").startswith("Draw"):
        probs.append(f"title block: the number is '{tbk['number']}' -- save the drawing "
                     "before exporting")
    return out


# ── save / export ─────────────────────────────────────────────────────────────
def save(path: str = "") -> str:
    """Save the drawing (empty path = Save) or Save-as (.slddrw). Returns the path."""
    md = _md()
    if path:
        # SILENT: IModelDoc2's SaveAs can open the save box and stall automation waiting
        # for the user (see sw_com.save_as).
        return C.save_as(md, path)
    # `IModelDoc2.Save3` raises 'Type mismatch' under early binding (byref out-params,
    # the same gotcha as sw_parts.save): save OVER the document's own path silently.
    current = md.GetPathName()
    if not current:
        raise RuntimeError("save(): drawing has no path -- pass the path (Save-as)")
    return C.save_as(md, current)


def export(path: str) -> str:
    """Export the drawing in the format inferred from the extension (.pdf/.dxf/.dwg). E.g.
    export('beam.pdf') -> PDF; export('beam.dxf') -> DXF. Returns the path."""
    md = _md()
    if not md.SaveAs(path):
        raise RuntimeError(f"export (drawing) failed: {path}")
    return path
