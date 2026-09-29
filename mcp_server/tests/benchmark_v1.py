# -*- coding: utf-8 -*-
r"""
BENCHMARK V1 -- end-to-end workflows through the SAME entry point the MCP tool
`sw_verb` uses (`mcp_server.mcp_solidworks._sw_verb_impl`: JSON in, JSON out, COM
objects as "@handle_N"). Each benchmark is the verb sequence an agent issues for one
engineering request, and it is judged the way this project judges everything: by the
geometry it produced (inspect.measure / inspect.validate / mass / bounding box), never by
the return values.

  A  part            request -> part -> inspect -> measure -> validate -> STEP
  B  sheet metal     sheet part -> inspect bends -> flatten -> DXF -> drawing -> bend table
  C  assembly        parts -> assembly -> mates -> intended DOF -> mate + interference gates
  D  drawing         the C assembly -> views -> dimension -> datum + GD&T -> balloons ->
                     BOM -> PDF -> read it back

A step that can go two ways (e.g. which side of a plane a cut goes) is tried, MEASURED,
and retried the other way when the measurement says it is wrong -- the loop an agent runs.
The retries are logged as `attempts`.

Every case builds its own input, so none depends on a model shipped with SOLIDWORKS;
every case is required, and the exit code is 0 only when all of them PASS.

Output (not versioned): <test output>/benchmark_v1/<X>/ with log.json, the model files,
a screenshot, and summary.json with the scoreboard. The test output is
tests/validation_assembly/ in development and a new directory per run in isolated mode
(solidworks/tests/isolation.py), where only documents opened by the run are closed.

Usage (from the repo root, SOLIDWORKS open):
  .venv\Scripts\python.exe -u mcp_server\tests\benchmark_v1.py [A B C D]   (default: all)
"""
import json
import os
import shutil
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import pythoncom  # noqa: E402

pythoncom.CoInitialize()
import mcp_server.mcp_solidworks as M  # noqa: E402
from solidworks import sw_com as C  # noqa: E402
from solidworks.tests import isolation as T  # noqa: E402

OUT = os.path.join(T.output_root(), "benchmark_v1")


class Bench:
    def __init__(self, key, title, prompt):
        self.key, self.title, self.prompt = key, title, prompt
        self.dir = os.path.join(OUT, key)
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.calls, self.checks, self.artifacts, self.attempts = [], [], [], 0

    def path(self, name):
        return os.path.join(self.dir, name)

    def V(self, verb, **params):
        """One verb call through the MCP entry point; logged."""
        t0 = time.time()
        entry = {"verb": verb, "params": params}
        try:
            out = M._sw_verb_impl(verb, json.dumps(params))["result"]
            entry.update(ok=True, result=out)
            return out
        except Exception as exc:
            entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            entry["seconds"] = round(time.time() - t0, 2)
            self.calls.append(entry)

    def check(self, name, cond, detail=""):
        self.checks.append({"check": name, "ok": bool(cond), "detail": str(detail)[:400]})
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""),
              flush=True)
        return bool(cond)

    def artifact(self, path):
        if os.path.exists(path):
            self.artifacts.append(os.path.relpath(path, self.dir))
        return path

    def save(self, error=None):
        ok = error is None and self.checks and all(c["ok"] for c in self.checks)
        log = {"benchmark": self.key, "title": self.title, "prompt": self.prompt,
               "result": "PASS" if ok else "FAIL", "error": error,
               "verb_calls": len(self.calls), "failed_calls": sum(not c["ok"] for c in self.calls),
               "attempts": self.attempts, "verbs_used": sorted({c["verb"] for c in self.calls}),
               "checks": self.checks, "artifacts": self.artifacts, "calls": self.calls}
        with open(self.path("log.json"), "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1, default=str)
        return log


def H(result):
    """A verb result that is a COM object -> the '@handle_N' the next call takes."""
    return "@" + result["$handle"]


def near(a, b, tol=0.05):
    return a is not None and abs(a - b) <= tol


def box(b):
    return b.V("inspect.model_summary")["bounding_box"]


# ── A: a part from a request ──────────────────────────────────────────────────
def bench_a():
    b = Bench("A", "Part from a request",
              "Create a bearing support: a 120 x 80 x 15 mm base plate with four 10 mm "
              "mounting holes 90 x 50 mm apart, and a 60 x 60 x 20 mm upright on it with a "
              "30 mm bore whose axis is 50 mm above the bottom of the plate.")
    try:
        b.V("part.new_part")
        b.V("part.sketch", plane="Top", shape="rect", x1=-60, y1=-40, x2=60, y2=40)
        b.V("part.extrude", depth_mm=15, kind="boss")
        bb = box(b)
        b.check("base plate 120 x 15 x 80", bb["size_mm"] == [120.0, 15.0, 80.0], bb["size_mm"])
        holes = [[-45, -25], [45, -25], [-45, 25], [45, 25]]
        # which side the cut goes depends on the plane normal: try, MEASURE, retry
        for rev in (False, True):
            try:
                b.V("part.hole", plane="Top", diameter_mm=10, positions=holes, reverse=rev)
            except Exception:  # noqa: BLE001 -- a refused cut is a measured outcome too
                b.attempts += 1
                continue
            if b.V("inspect.find_face", kind="cylinder", radius_mm=5) is not None:
                break
            b.attempts += 1
        hole_face = b.V("inspect.find_face", kind="cylinder", radius_mm=5)
        b.check("mounting hole is 10 mm", hole_face is not None and near(
            b.V("inspect.measure", entities=[H(hole_face)], kind="diameter")["value"], 10),
            "diameter measured")
        # the upright: a 60 x 60 profile on the Front plane, from the top of the plate
        b.V("part.sketch", plane="Front", shape="rect", x1=-30, y1=15, x2=30, y2=75)
        b.V("part.extrude", depth_mm=20, kind="boss")
        bb = box(b)
        b.check("upright raises the part to 75 mm", near(bb["size_mm"][1], 75), bb["size_mm"])
        for rev in (True, False):
            try:
                b.V("part.hole", plane="Front", diameter_mm=30, positions=[[0, 50]], reverse=rev)
            except Exception:  # noqa: BLE001
                b.attempts += 1
                continue
            if b.V("inspect.find_face", kind="cylinder", radius_mm=15) is not None:
                break
            b.attempts += 1
        bore = b.V("inspect.find_face", kind="cylinder", radius_mm=15)
        bottom = b.V("inspect.find_face", kind="plane", axis=1, want_max=False)
        d = b.V("inspect.measure", entities=[H(bore)], kind="diameter")["value"]
        h = b.V("inspect.measure", entities=[H(bore), H(bottom)], kind="distance")["value"]
        b.check("bore is 30 mm", near(d, 30), f"{d} mm")
        b.check("bore axis 50 mm above the bottom", near(h, 50), f"{h} mm")
        v = b.V("inspect.validate")
        b.check("validate: valid, one body", v["valid"] and v["body_count"] == 1,
                f"errors={v['errors']} warnings={len(v['warnings'])}")
        m = b.V("inspect.mass")
        vol = m["volume_m3"] * 1e9
        expect = 120 * 80 * 15 - 4 * 3.14159265 * 25 * 15 + 60 * 60 * 20 - 3.14159265 * 225 * 20
        b.check("volume matches the intent", near(vol, expect, 1.0), f"{vol:.1f} vs {expect:.1f} mm3")
        b.V("part.save", path=b.artifact(b.path("bearing_support.SLDPRT")))
        b.artifact(b.V("part.export", path=b.path("bearing_support.step")))
        b.artifact(b.V("inspect.screenshot", path=b.path("bearing_support.bmp")))
        return b.save()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return b.save(error=f"{type(exc).__name__}: {exc}")


# ── B: sheet metal to the cutter ──────────────────────────────────────────────
def bench_b():
    b = Bench("B", "Sheet metal",
              "A 100 x 60 mm sheet, 2 mm thick, folded into a box with 20 mm flanges; give "
              "me the flat pattern as a DXF for the laser cutter and a drawing with the bend "
              "table.")
    try:
        b.V("sheet.new_sheet", plane="Front", w_mm=100, h_mm=60, thickness_mm=2)
        b.V("sheet.box_flanges", length_mm=20)
        bends = b.V("sheet.bend_info")
        b.check("four bends", len(bends) == 4, [(x.get("name"), x.get("angle_deg"))
                                                 for x in bends])
        v = b.V("inspect.validate")
        b.check("validate: valid", v["valid"], v["errors"])
        part = b.artifact(b.V("part.save", path=b.path("box.SLDPRT")))
        b.V("sheet.flatten", on=True)
        size = b.V("sheet.bbox_mm")
        b.check("flat pattern 136.6 x 96.6 x 2.0 mm",
                sorted(round(x, 1) for x in size) == [2.0, 96.6, 136.6], size)
        b.artifact(b.V("sheet.export_flat", path=b.path("box.dxf")))
        b.check("DXF written", os.path.getsize(b.path("box.dxf")) > 1000)
        cl = b.V("sheet.cut_list")
        b.check("cut list resolved", bool(cl), str(cl)[:200])
        b.V("sheet.flatten", on=False)
        b.V("part.save", path=part)
        b.V("dwg.new_drawing", model_path=part, language="en")
        fv = b.V("dwg.flat_pattern_view", at=[180, 150], model_path=part)
        b.V("dwg.bend_table", view_name=fv, at=[330, 250])
        s = b.V("inspect.model_summary")
        b.check("drawing: the flat view and a bend table",
                s["view_count"] == 1 and s["annotations"].get("tables", 0) >= 1,
                s["annotations"])
        b.artifact(b.V("dwg.save", path=b.path("box.SLDDRW")))
        b.artifact(b.V("dwg.export", path=b.path("box.pdf")))
        return b.save()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return b.save(error=f"{type(exc).__name__}: {exc}")


# ── C: assembly with intended freedom ─────────────────────────────────────────
def _make_part(b, name, build):
    b.V("part.new_part")
    build()
    return b.artifact(b.V("part.save", path=b.path(name)))


def bench_c():
    b = Bench("C", "Assembly",
              "Put a 10 mm pin, 60 mm long, through the 10.2 mm hole of a 100 x 100 x 20 "
              "plate, flush with the bottom of the plate. The pin must be able to turn and "
              "nothing else; no interference.")
    try:
        def plate():
            b.V("part.sketch", plane="Top", shape="rect", x1=-50, y1=-50, x2=50, y2=50)
            b.V("part.extrude", depth_mm=20, kind="boss")
            for rev in (False, True):
                try:
                    b.V("part.hole", plane="Top", diameter_mm=10.2, positions=[[0, 0]],
                        reverse=rev)
                except Exception:  # noqa: BLE001
                    b.attempts += 1
                    continue
                if b.V("inspect.find_face", kind="cylinder", radius_mm=5.1) is not None:
                    return
                b.attempts += 1

        def pin():
            b.V("part.sketch", plane="Top", shape="circle", cx=0, cy=0, r=5)
            b.V("part.extrude", depth_mm=60, kind="boss")

        p_plate = _make_part(b, "plate.SLDPRT", plate)
        p_pin = _make_part(b, "pin.SLDPRT", pin)
        b.V("asm.new_assembly")
        cp = b.V("asm.add_component", path=p_plate, xyz=[0, 0, 0], fixed=True)
        cn = b.V("asm.add_component", path=p_pin, xyz=[150, 40, 0])
        hole = b.V("asm.find_face", comp=H(cp), kind="cylinder", radius_mm=5.1)
        pin_cyl = b.V("asm.find_face", comp=H(cn), kind="cylinder", radius_mm=5)
        b.V("asm.mate", ref_a=H(hole), ref_b=H(pin_cyl), kind="concentric")
        pb = b.V("asm.find_face", comp=H(cp), kind="plane", axis=1, want_max=False)
        nb = b.V("asm.find_face", comp=H(cn), kind="plane", axis=1, want_max=False)
        b.V("asm.mate", ref_a=H(pb), ref_b=H(nb), kind="coincident", align="aligned")
        # flush = coplanar: the offset along the normal (Y) is 0. NOT `distance`: between
        # coplanar faces side by side that is their in-plane gap (5.1 from the pin's centre)
        m = b.V("inspect.measure", entities=[H(b.V("asm.find_face", comp=H(cp), kind="plane",
                                                      axis=1, want_max=False)),
                                             H(b.V("asm.find_face", comp=H(cn), kind="plane",
                                                      axis=1, want_max=False))])
        b.check("pin flush with the bottom of the plate",
                m.get("parallel") and near(m["delta_mm"][1], 0, 1e-3), m)
        ms = b.V("asm.mates")
        b.check("two mates, both solved",
                [m["type"] for m in ms] == ["concentric", "coincident"]
                and all(m["status"] == "ok" for m in ms), [(m["name"], m["status"]) for m in ms])
        dof = b.V("asm.free_dof", comp=H(cn))
        b.check("intended DOF: the pin only turns about its axis",
                dof["free_translations"] == [] and dof["free_rotations"] == ["Y"], dof)
        v = b.V("inspect.validate")
        b.check("no mate errors, no interference", v["valid"] and v["interferences"] == [],
                f"errors={v['errors']}")
        b.artifact(b.V("asm.save", path=b.path("pin_plate.SLDASM")))
        b.artifact(b.V("inspect.screenshot", path=b.path("pin_plate.bmp")))
        return b.save()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return b.save(error=f"{type(exc).__name__}: {exc}")


# ── D: the drawing of the C assembly ──────────────────────────────────────────
def bench_d():
    b = Bench("D", "Drawing",
              "Make the drawing of the pin-and-plate assembly: front, top and iso views, the "
              "plate width dimensioned, datum A on the bottom of the plate, perpendicularity "
              "of its side to A, balloons, a BOM, and a PDF.")
    asm = os.path.join(OUT, "C", "pin_plate.SLDASM")
    if not os.path.exists(asm):
        b.check("benchmark C assembly available", False, asm)
        return b.save(error="run C first")
    try:
        b.V("dwg.new_drawing", model_path=asm, language="en")
        vf = b.V("dwg.add_view", kind="front", at=[110, 170], model_path=asm)
        b.V("dwg.add_view", kind="top", at=[110, 70], model_path=asm)
        vi = b.V("dwg.add_view", kind="iso", at=[290, 170], model_path=asm, scale=0.8)
        views = b.V("dwg.list_views")
        b.check("three views read back", [v["orientation"] for v in views]
                == ["*Front", "*Top", "*Isometric"], [(v["name"], v["scale"]) for v in views])
        # the front view at 1:1: the plate is 100 x 20 and the pin (60 high) stands on its
        # axis, so the geometry is 100 x 60 around position_mm (the outline has a margin)
        cx, cy = views[0]["position_mm"]
        xmin, xmax, ymin = cx - 50, cx + 50, cy - 30
        w = b.V("dwg.dimension", view_name=vf, picks=[[xmin, ymin + 10], [xmax, ymin + 10]],
                place_at=[cx, ymin - 15], orient="horizontal")
        b.check("plate width dimension reads 100", near(w, 100, 0.01), w)
        lbl = b.V("dwg.datum", view_name=vf, edge_at=[cx - 30, ymin], label="A")
        tol = b.V("dwg.gtol", view_name=vf, edge_at=[xmin, ymin + 10],
                  symbol="perpendicularity", tolerance=0.05, datums=["A"])
        b.check("datum A + perpendicularity 0.05 to A", lbl == "A" and tol == "0.05",
                f"{lbl} / {tol}")
        nb = b.V("dwg.balloons", view_name=vi)
        b.check("balloons on both items", nb >= 2, nb)
        b.V("dwg.table", view_name=vi, kind="bom", at=[300, 280])
        s = b.V("inspect.model_summary")
        ann = s["annotations"]
        b.check("drawing reads back: dims, datum, gtol, BOM",
                ann.get("dimensions", 0) >= 1 and ann.get("datums", 0) >= 1
                and ann.get("gtols", 0) >= 1 and ann.get("tables", 0) >= 1, ann)
        b.check("drawing health", s["health"]["valid"], s["health"]["errors"])
        b.artifact(b.V("dwg.save", path=b.path("pin_plate.SLDDRW")))
        b.artifact(b.V("dwg.export", path=b.path("pin_plate.pdf")))
        return b.save()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return b.save(error=f"{type(exc).__name__}: {exc}")


BENCHES = {"A": bench_a, "B": bench_b, "C": bench_c, "D": bench_d}


def main():
    which = [a.upper() for a in sys.argv[1:]] or list(BENCHES)
    os.makedirs(OUT, exist_ok=True)
    C.connect()
    T.begin()
    T.close_all()
    rev = C.app().RevisionNumber()
    print(f"SW rev {rev} | benchmarks: {which}")
    board = {}
    for key in which:
        print(f"\n== {key} ==", flush=True)
        t0 = time.time()
        log = BENCHES[key]()
        T.close_all()
        board[key] = {"title": log["title"], "result": log["result"],
                      "checks": f"{sum(c['ok'] for c in log['checks'])}/{len(log['checks'])}",
                      "verb_calls": log["verb_calls"], "failed_calls": log["failed_calls"],
                      "attempts": log["attempts"], "seconds": round(time.time() - t0, 1),
                      "error": log["error"]}
        print(f"  -> {log['result']} {board[key]['checks']}", flush=True)
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"solidworks_revision": rev, "benchmarks": board}, f, indent=1)
    print("\n==== SCOREBOARD ====")
    for key, r in board.items():
        print(f"{key} {r['title']:<22} {r['result']}  checks {r['checks']}  calls "
              f"{r['verb_calls']} (failed {r['failed_calls']})  retries {r['attempts']}  "
              f"{r['seconds']} s" + (f"  ERROR {r['error']}" if r["error"] else ""))
    n_ok = sum(r["result"] == "PASS" for r in board.values())
    # every requested case is required: the denominator is every case, never what ran
    print(f"{n_ok}/{len(board)} PASS")
    T.finish()
    return 0 if board and n_ok == len(board) else 1


if __name__ == "__main__":
    sys.exit(main())
