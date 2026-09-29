# -*- coding: utf-8 -*-
r"""
DRAWING benchmark -- the full technical drawing of three real models, judged by whether
the drawing is USABLE (dwg.quality + the PDF), not by whether each call returned.

  part       bearing_support (benchmark A): views, section through the bore, model
             dimensions, centre marks and centerlines, datum + GD&T, surface finish,
             title block
  assembly   clevis (smoke_clevis): views, EXPLODED iso with no overlap, balloons
             numbered from the BOM, a BOM with one row per part
  sheet      box (benchmark B): flat pattern with bend notes and bend table, inside the
             sheet

The inputs are made by their own scripts when missing (benchmark A/B, smoke_clevis), in
the same test output directory. A fourth case, `external` (a vendor sample part, ~300
holes), was removed on 2026-09-28 and no case needs a SOLIDWORKS-installed model: SW 2017 could not carry it (>15 min, two crashes) and
on SW 2026 it stopped at 49% of the sizes stated, pending a hole table.
Output (not versioned): <test output>/drawings/<case>.SLDDRW/.pdf and summary.json --
tests/validation_assembly/ in development, a new directory per run in isolated mode
(solidworks/tests/isolation.py). Look at the PDFs -- `quality` measures what it can.

Usage (from the repo root, SOLIDWORKS open):
  .venv\Scripts\python.exe -u solidworks\tests\benchmark\benchmark_drawings.py [part assembly sheet]
"""
import json
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
import pythoncom  # noqa: E402

pythoncom.CoInitialize()
from solidworks import sw_assembly as A  # noqa: E402
from solidworks import sw_com as C  # noqa: E402
from solidworks import sw_drawing as D  # noqa: E402
from solidworks import sw_parts as P  # noqa: E402
from solidworks.tests import isolation as T  # noqa: E402

# the input builders below run as subprocesses and inherit this root (isolation.ENV_OUT)
VA = T.output_root()
OUT = os.path.join(VA, "drawings")
BENCH = os.path.join(VA, "benchmark_v1")


class Case:
    def __init__(self, key):
        self.key, self.checks = key, []
        self.skipped = None     # the reason, when a prerequisite outside CADapter is absent

    def check(self, name, cond, detail=""):
        self.checks.append({"check": name, "ok": bool(cond), "detail": str(detail)[:400]})
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""),
              flush=True)
        return bool(cond)

    def usable(self, q, *, dims=True, bom=False):
        """The checks every drawing must pass, read from dwg.quality()."""
        self.check("nothing outside the frame, nothing on the title block, no overlap",
                   not [p for p in q["problems"] if "frame" in p or "overlap" in p
                        or "on the title block" in p],
                   [p for p in q["problems"] if "frame" in p or "overlap" in p])
        # read from what each dimension DRAWS (2026-09-28): a text over another
        # dimension's lines or over the edges of a view
        self.check("no dimension text crosses another dimension or a view's edges",
                   not q["dimensions"].get("clashes"),
                   [c for c in q["dimensions"].get("clashes", [])][:5])
        tb = q["titleblock"]
        self.check("title block: title and number filled",
                   tb.get("title") and tb.get("number") and not tb["number"].startswith("Draw"),
                   {k: tb.get(k) for k in ("title", "number", "material", "weight")})
        if dims:
            d = q["dimensions"]
            self.check("every model driving dimension is on the drawing",
                       not d["missing_model_dims"],
                       f"{d['on_drawing']} on the drawing, missing {d['missing_model_dims'][:12]}")
            self.check("every orthographic view carries dimensions",
                       not [p for p in q["problems"] if "has no dimension" in p],
                       [p for p in q["problems"] if "has no dimension" in p])
        if bom:
            boms = [t for t in q["tables"] if t["kind"] == "BillOfMaterials"]
            rows = boms[0]["rows"][1:] if boms else []
            self.check("BOM: one row per part", boms and len(rows) >= 2, rows)
            self.check("balloons carry the BOM item numbers",
                       q["balloons"] and all(b.strip().isdigit() for b in q["balloons"]),
                       q["balloons"])


def _finish(case, name):
    """Save (the number field is the file name), read the quality, export the PDF."""
    dwg_path = os.path.join(OUT, f"{name}.SLDDRW")
    D.save(dwg_path)
    q = D.quality()
    D.export(os.path.join(OUT, f"{name}.pdf"))
    with open(os.path.join(OUT, f"{name}_quality.json"), "w", encoding="utf-8") as f:
        json.dump(q, f, indent=1, default=str)
    for p in q["problems"]:
        print(f"     problem: {p}")
    return q


def _ensure(path, make):
    if not os.path.exists(path):
        print(f"  (building the input: {os.path.basename(path)})", flush=True)
        make()
        C.connect()
        T.close_all()
    if not os.path.exists(path):
        raise RuntimeError(f"input not available: {path}")
    return path


def _bench(key):
    def run():
        subprocess.run([sys.executable, "-u", os.path.join(ROOT, "mcp_server", "tests",
                                                           "benchmark_v1.py"), key], cwd=ROOT)
    return run


def _clevis():
    subprocess.run([sys.executable, "-u", os.path.join(ROOT, "solidworks", "tests", "smoke",
                                                       "smoke_clevis.py")], cwd=ROOT)


# ── the cases ─────────────────────────────────────────────────────────────────
def _scale(view):
    v = next(x for x in D.list_views() if x["name"] == view)
    return v["scale_ratio"][0] / v["scale_ratio"][1]


def _values_present(case, q, expected):
    got = q["dimensions"]["values"]
    missing = [e for e in expected if not any(abs(g - e) < 0.01 for g in got)]
    case.check(f"the design sizes are dimensioned {sorted(expected)}", not missing,
               f"missing {missing}; on the drawing {sorted(set(got))}")


def case_part():
    c = Case("part")
    part = _ensure(os.path.join(BENCH, "A", "bearing_support.SLDPRT"), _bench("A"))
    C.app().OpenDoc6(part, C.DOC_PART, 1, "", pythoncom.Missing, pythoncom.Missing)
    P.set_material("AISI 1020")
    P.save(part)
    D.new_drawing(part, size="A3")
    vf = D.add_view("front", at=(100, 200))
    vt = D.add_view("top", at=(100, 100))
    D.add_view("iso", at=(330, 200), scale=0.5)
    D.fill_titleblock(title="Bearing support", finish="Ra 3.2", drawn_by="SolidMCP",
                      drawn_date=time.strftime("%Y-%m-%d"), revision="A")
    D.arrange(reserve_mm=35)
    # section A-A: a vertical cut through the bore axis (x = 0, 50 above the bottom;
    # the view centre is the centre of the 120 x 75 front silhouette)
    cx, cy = D.view_center(vf)
    s = _scale(vf)
    vs = D.section_view(vf, (cx, cy + 45 * s), (cx, cy - 45 * s), (cx + 200, cy), label="A")
    D.arrange(reserve_mm=35)
    for v in (vf, vt, vs):
        D.auto_dimension(v)
    D.center_marks(vf)
    D.center_marks(vt)
    D.centerlines(vs)
    D.arrange()
    D.dedupe_dimensions()
    D.tidy_dimensions()
    cx, cy = D.view_center(vf)
    s = _scale(vf)
    D.datum(vf, (cx - 45 * s, cy - 37.5 * s), "A")
    D.gtol(vf, (cx + 15 * s, cy + 12.5 * s), "position", 0.05, datums=["A"], diameter=True)
    D.surface_finish(vf, (cx, cy + 27.5 * s), ra=1.6)
    # 'auto' on an annotated sheet only SHRINKS (never enlarges). It was scale='keep',
    # which failed on SW 2017 (2026-09-27, probe_sw17_v1.py): there the previous arrange
    # fitted 1:1 by a hair (SW 2026 had already gone to 1:2), and the datum, gtol and
    # finish symbol pushed the front view to 135 mm tall -- 291 mm of views for 277 mm
    # of frame, with no room to shrink.
    D.arrange()
    q = _finish(c, "bearing_support")
    c.usable(q, dims=False)
    c.check("every orthographic view and the section carry dimensions",
            not [p for p in q["problems"] if "has no dimension" in p],
            [p for p in q["problems"] if "has no dimension" in p])
    _values_present(c, q, [120, 80, 75, 15, 10, 30, 50, 60, 20])
    c.check("title block: material and weight come from the model",
            q["titleblock"].get("material") and q["titleblock"].get("weight")
            and not [p for p in q["problems"] if "no material" in p], q["titleblock"])
    return c


def case_assembly():
    c = Case("assembly")
    asm = _ensure(os.path.join(VA, "clevis.SLDASM"), _clevis)
    C.app().OpenDoc6(asm, C.DOC_ASSEMBLY, 1, "", pythoncom.Missing, pythoncom.Missing)
    r = A.explode()
    c.check("the exploded state leaves no overlap", r["overlaps"] == [], r)
    A.collapse()
    D.new_drawing(asm, size="A3")
    D.add_view("front", at=(80, 210))
    D.add_view("top", at=(80, 120))
    vi = D.add_view("iso", at=(240, 170), scale=0.8)
    D.set_exploded(vi, True)
    D.fill_titleblock(title="Clevis joint", drawn_by="SolidMCP",
                      drawn_date=time.strftime("%Y-%m-%d"), revision="A")
    D.arrange(reserve_mm=20)     # the scale first, with room for the balloons
    D.balloons(vi, layout="circle")   # BEFORE the BOM: on SW 2017 a BOM breaks AutoBalloon
    D.table(vi, kind="bom")
    D.arrange()
    q = _finish(c, "clevis")
    c.usable(q, dims=False, bom=True)
    return c


def case_sheet():
    c = Case("sheet")
    part = _ensure(os.path.join(BENCH, "B", "box.SLDPRT"), _bench("B"))
    C.app().OpenDoc6(part, C.DOC_PART, 1, "", pythoncom.Missing, pythoncom.Missing)
    P.set_material("AISI 304")
    P.save(part)
    D.new_drawing(part, size="A3")
    fv = D.flat_pattern_view(at=(150, 170), model_path=part)
    D.bend_table(fv)
    D.fill_titleblock(title="Box -- flat pattern", drawn_by="SolidMCP",
                      drawn_date=time.strftime("%Y-%m-%d"), revision="A")
    D.arrange(reserve_mm=35)
    D.auto_dimension(fv)
    D.dedupe_dimensions()
    D.arrange()
    q = _finish(c, "box_flat")
    c.usable(q, dims=False)
    _values_present(c, q, [136.566, 96.566])
    bends = [t for t in q["tables"] if t["kind"] == "BendTable"]
    c.check("bend table with one row per bend", bends and len(bends[0]["rows"]) == 5,
            bends[0]["rows"] if bends else q["tables"])
    return c


CASES = {"part": case_part, "assembly": case_assembly, "sheet": case_sheet}


def main():
    which = sys.argv[1:] or list(CASES)
    # CADAPTER_DRAWINGS_SKIP=part,... drops cases the MACHINE cannot carry, and says so
    # (a SKIP is not a PASS).
    skip = [k for k in os.environ.get("CADAPTER_DRAWINGS_SKIP", "").split(",") if k]
    for key in skip:
        if key in which:
            which.remove(key)
            print(f"  [SKIP] {key} -- CADAPTER_DRAWINGS_SKIP (not validated on this machine)")
    os.makedirs(OUT, exist_ok=True)
    C.connect()
    T.begin()
    T.close_all()
    rev = C.app().RevisionNumber()
    print(f"SW rev {rev} | drawings: {which}")
    board = {}
    for key in which:
        print(f"\n== {key} ==", flush=True)
        t0 = time.time()
        try:
            case = CASES[key]()
            error = None
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            case, error = Case(key), f"{type(exc).__name__}: {exc}"
            case.check(f"{key} (exception)", False, error)
        T.close_all()
        ok = sum(ch["ok"] for ch in case.checks)
        result = ("SKIP" if case.skipped and not case.checks
                  else "PASS" if ok == len(case.checks) and not error else "FAIL")
        board[key] = {"result": result, "skipped": case.skipped,
                      "checks": f"{ok}/{len(case.checks)}", "error": error,
                      "seconds": round(time.time() - t0, 1), "detail": case.checks}
        print(f"  -> {board[key]['result']} {board[key]['checks']}", flush=True)
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"solidworks_revision": rev, "cases": board}, f, indent=1)
    n_ok = sum(int(b["checks"].split("/")[0]) for b in board.values())
    n = sum(int(b["checks"].split("/")[1]) for b in board.values())
    print("\n==== DRAWINGS ====")
    for key, r in board.items():
        print(f"{key:<10} {r['result']}  {r['checks']}  {r['seconds']} s"
              + (f"  ERROR {r['error']}" if r["error"] else ""))
    skipped = sum(b["result"] == "SKIP" for b in board.values())
    print(f"SUMMARY: {n_ok}/{n} PASS" + (f"  ({skipped} case(s) SKIP)" if skipped else ""))
    T.finish()


if __name__ == "__main__":
    main()
