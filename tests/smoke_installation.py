# -*- coding: utf-8 -*-
r"""
CADapter INSTALLATION SMOKE: one disposable part, and nothing else touched.

Usage (from the repository root, with SOLIDWORKS already open):
  .venv\Scripts\python.exe -u tests\smoke_installation.py
  .venv\Scripts\python.exe -u tests\smoke_installation.py --launch   # start SW if needed

What it does, with the same curated calls the part tests use:
  attach to SOLIDWORKS -> read its revision -> NEW part -> 40 x 30 mm rectangle on the
  Top plane -> extrude 10 mm -> force a rebuild and validate -> measure it (one body,
  volume 12 000 mm^3, 10 mm between the top and bottom faces) -> close exactly that
  part WITHOUT saving.

What it never does: save or write any file, close or edit a document it did not create,
call CloseAllDocuments, restart or kill SOLIDWORKS. If another document becomes active
while it runs, it stops instead of modelling into that document, and still closes only
its own part (by title). The one persistent side effect is the one every CADapter
connection has: three SOLIDWORKS prompts that stall automation are switched off in the
user's options (see `sw_com.connect`).

Exit code: 0 passed; 1 a check or the cleanup failed; 2 it could not run (SOLIDWORKS
not reachable, or the Python environment is incomplete).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WIDTH, HEIGHT, DEPTH = 40.0, 30.0, 10.0          # mm
VOLUME_M3 = WIDTH * HEIGHT * DEPTH * 1e-9
TOL_MM = 1e-6


class Report:
    def __init__(self):
        self.failed = 0

    def __call__(self, step, ok, detail=""):
        self.failed += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {step}" + (f" -- {detail}" if detail else ""),
              flush=True)
        return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--launch", action="store_true",
                    help="start SOLIDWORKS when it is not running (default: attach only)")
    a = ap.parse_args(argv)
    chk = Report()
    try:
        from solidworks import sw_com as C, sw_inspect as I, sw_parts as P
        from solidworks.tests import isolation as T
    except ImportError as exc:
        print(f"  [FAIL] import -- {exc} (install requirements.txt in a Windows venv)")
        return 2
    try:
        sw = C.connect(launch=a.launch)
        rev = sw.RevisionNumber()
    except Exception as exc:  # noqa: BLE001 -- nothing to clean up yet
        print(f"  [FAIL] connect -- {type(exc).__name__}: {exc}")
        print("SMOKE COULD NOT RUN")
        return 2
    chk("connect", True, f"SOLIDWORKS revision {rev}")

    before = T.open_document_keys()
    title = None
    try:
        title = P.new_part()
        mine = "untitled:" + title
        if not chk("create a new disposable part", mine not in before, title):
            title = None                     # not ours: never close it
            raise RuntimeError("the new part's title belongs to a document that was open")

        def guard(step):
            active = C.active("IModelDoc2").GetTitle()
            if active != title:
                raise RuntimeError(f"before '{step}' the active document became '{active}', "
                                   f"not this test's '{title}' -- stopping without touching it")

        guard("sketch")
        P.sketch("Top", "rect", x1=0, y1=0, x2=WIDTH, y2=HEIGHT)
        guard("extrude")
        P.extrude(DEPTH, "boss")
        guard("validate")
        v = I.validate(force=True)
        chk("rebuild + validate", v["valid"] and v["rebuild_ok"] and v.get("body_count") == 1
            and not v["errors"], f"valid={v['valid']} bodies={v.get('body_count')} "
            f"errors={v['errors']} warnings={v['warnings']}")
        guard("measure")
        vol = I.mass()["volume_m3"]
        chk("measure volume", abs(vol - VOLUME_M3) < 1e-12,
            f"{vol * 1e9:.3f} mm^3 (expected {VOLUME_M3 * 1e9:.0f})")
        # sketched on Top, the extrusion runs along Y: top and bottom are planes normal to Y
        top, bottom = I.find_face("plane", 1, True), I.find_face("plane", 1, False)
        d = I.measure([top, bottom], "distance")["value"]
        chk("measure top-bottom distance", abs(d - DEPTH) < TOL_MM, f"{d} mm")
    except Exception as exc:  # noqa: BLE001 -- reported as a failure, cleanup still runs
        chk("smoke steps", False, f"{type(exc).__name__}: {exc}")
    finally:
        if title is not None:
            try:
                if title == C.active("IModelDoc2").GetTitle():
                    C.close_active_sketch()
                sw.CloseDoc(title)            # by title: only this test's part
                after = T.open_document_keys()
                chk("cleanup: the test part is closed, unsaved", mine not in after, title)
                kept = before <= after
                chk("cleanup: every document open before is still open", kept,
                    f"{len(before)} before" if kept else f"missing: {sorted(before - after)}")
            except Exception as exc:  # noqa: BLE001
                chk("cleanup", False, f"{type(exc).__name__}: {exc} -- close '{title}' "
                                      f"by hand without saving")
    print("SMOKE PASSED" if not chk.failed else f"SMOKE FAILED ({chk.failed} check(s))")
    return 0 if not chk.failed else 1


if __name__ == "__main__":
    sys.exit(main())
