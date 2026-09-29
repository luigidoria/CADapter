# -*- coding: utf-8 -*-
r"""
SMOKE of the ASSEMBLY verbs (solidworks/sw_assembly.py). Self-contained: it builds the 3
parts, assembles them, validates the 7 mate kinds + fix/float + rebuild + save + a Parasolid
export.

Usage (from the MCP-SolidWorks root):
  .venv\Scripts\python.exe -u solidworks\tests\smoke\smoke_assembly.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from solidworks import sw_com as C, sw_parts as P, sw_sketch as S, sw_assembly as A  # noqa: E402
from solidworks import sw_inspect as I  # noqa: E402
from solidworks.tests import isolation as T  # noqa: E402

# shared tests/validation_assembly/ in development, a new directory per run in public
OUT = T.output_root()
P_BASE = os.path.join(OUT, "base.sldprt")
P_PLATE = os.path.join(OUT, "chapa.sldprt")
# do NOT use "pino.sldprt": it COLLIDES with the clevis' REAL pin (smoke_clevis.py) and
# overwrites it with a plain cylinder -> the clevis assembly then references the wrong pin.
P_PIN = os.path.join(OUT, "pino_smoke.sldprt")


class Chk:
    def __init__(self): self.ok = self.n = 0
    def __call__(self, name, cond, detail=""):
        self.n += 1; self.ok += bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL '}] {name}" + (f" -- {detail}" if detail else ""))


def build_parts():
    P.new_part(); P.sketch("Top", "rect", x1=0, y1=0, x2=100, y2=100); P.extrude(20, "boss")
    P.save(P_BASE)
    P.new_part(); P.sketch("Top", "rect", x1=0, y1=0, x2=80, y2=80); P.extrude(15, "boss")
    P.save(P_PLATE)
    P.new_part(); S.begin("Top"); S.circle(0, 0, 10); S.end(); P.extrude(60, "boss")
    P.save(P_PIN)


def main():
    C.connect(); T.begin(); T.close_scratch()
    # closes instances saved by earlier runs (the assembly first -> it releases the parts)
    T.close_titles(("montagem_smoke.sldasm", "base.sldprt", "chapa.sldprt", "pino_smoke.sldprt"))
    print(f"SW rev {C.app().RevisionNumber()}")
    build_parts()
    chk = Chk()

    A.new_assembly()
    base = A.add_component(P_BASE, (0, 0, 0))       # the 1st = auto-fixed
    sheet = A.add_component(P_PLATE, (200, 0, 0))
    pin1 = A.add_component(P_PIN, (0, 120, 0))
    pin2 = A.add_component(P_PIN, (50, 120, 0))
    chk("add_component (4)", A.component_count() == 4, f"n={A.component_count()}")

    chk("fix", A.fix(sheet))
    chk("float_", A.float_(sheet))

    # references by geometry
    base_top = A.find_face(base, "plane", 1, True)
    base_xp = A.find_face(base, "plane", 0, True)
    plate_bot = A.find_face(sheet, "plane", 1, False)
    plate_xp = A.find_face(sheet, "plane", 0, True)
    pin1_cyl = A.find_face(pin1, "cylinder")
    pin2_cyl = A.find_face(pin2, "cylinder")
    chk("find_face (6 refs)", all(x is not None for x in
        (base_top, base_xp, plate_bot, plate_xp, pin1_cyl, pin2_cyl)))

    # the 7 mate kinds -- create, check, delete (isolation)
    tests = [
        ("parallel", base_xp, plate_xp, dict(align="aligned")),
        ("distance", base_xp, plate_xp, dict(align="aligned", distance_mm=30)),
        ("angle", base_top, plate_xp, dict(align="closest", angle_deg=30)),
        ("perpendicular", base_top, plate_xp, dict(align="closest")),
        ("tangent", pin1_cyl, base_top, dict(align="closest")),
    ]
    for kind, a, b, kw in tests:
        try:
            name = A.mate(a, b, kind, **kw)
            A.delete_mate(name)
            chk(f"mate {kind}", True, name)
        except Exception as e:
            chk(f"mate {kind}", False, str(e)[:50])

    # keepers -> a coherent final assembly
    try:
        A.mate(base_top, plate_bot, "coincident", align="anti")
        A.mate(pin1_cyl, pin2_cyl, "concentric")
        chk("mates keeper (coincident+concentric)", True)
    except Exception as e:
        chk("mates keeper", False, str(e)[:50])

    chk("rebuild", A.rebuild())
    p = A.save(os.path.join(OUT, "montagem_smoke.sldasm"))
    chk("save .sldasm", os.path.exists(p))
    e = A.export(os.path.join(OUT, "montagem_smoke.x_t"))
    chk("export Parasolid (for Mechanical)", os.path.exists(e))

    # ── exploded view (essential for an assembly drawing) ───────────────────────
    try:
        r = A.explode()
        chk("explode (planned): an explode view and no overlap left",
            bool(r["view"]) and r["overlaps"] == [], str(r))
        chk("collapse (folds back)", A.collapse())
    except Exception as ex:
        chk("explode/collapse", False, str(ex)[:60])

    # ── component operations (mirror / replace / suppress / remove) ─────────────
    # a NEW and isolated assembly (it does not touch the previous one)
    A.new_assembly()
    m1 = A.add_component(P_BASE, (30, 0, 30), fixed=True)
    try:
        n = A.mirror_component("Right", [m1])
        chk("mirror_component (+1 inst)", n == 2, f"{n} comp")
    except Exception as ex:
        chk("mirror_component", False, str(ex)[:50])

    A.new_assembly()
    rc = A.add_component(P_BASE, (0, 0, 0), fixed=True)  # base 100mm
    dx0 = round((C.cast(rc.GetBody(), "IBody2").GetBodyBox()[3]) * 1000)
    try:
        A.replace_component(rc, P_PLATE)                 # -> the 80mm plate
        dx1 = round((C.cast(A.components()[0].GetBody(), "IBody2").GetBodyBox()[3]) * 1000)
        chk("replace_component (100->80mm)", dx0 == 100 and dx1 == 80, f"{dx0}->{dx1}")
    except Exception as ex:
        chk("replace_component", False, str(ex)[:50])

    A.new_assembly()
    s1 = A.add_component(P_BASE, (0, 0, 0), fixed=True)
    s2 = A.add_component(P_PIN, (150, 0, 0))
    try:
        A.suppress(s2, True); sup = C.cast(s2, "IComponent2").IsSuppressed()
        A.suppress(s2, False); unsup = not C.cast(s2, "IComponent2").IsSuppressed()
        chk("suppress/unsuppress", sup and unsup, f"sup={sup} unsup={unsup}")
        n0 = A.component_count(top_only=True)
        A.remove_component(s2)
        chk("remove_component", A.component_count(top_only=True) == n0 - 1,
            f"{n0}->{A.component_count(top_only=True)}")
    except Exception as ex:
        chk("suppress/remove", False, str(ex)[:50])

    # ── closing the loop: inspect.measure / inspect.validate on an assembly ─────
    A.new_assembly()
    vb = A.add_component(P_BASE, (0, 0, 0), fixed=True)
    vp = A.add_component(P_PLATE, (0, 60, 0))
    top, bot = A.find_face(vb, "plane", 1, True), A.find_face(vp, "plane", 1, False)
    gap0 = I.measure([top, bot], "distance")["value"]
    A.mate(top, bot, "coincident", align="anti")
    gap1 = I.measure([A.find_face(vb, "plane", 1, True), A.find_face(vp, "plane", 1, False)],
                     "distance")["value"]
    chk("measure: the coincident mate closes the gap", gap0 > 1 and abs(gap1) < 1e-6,
        f"{gap0:.1f} -> {gap1:.3f} mm")
    v = I.validate()
    chk("validate: healthy assembly", v["valid"] and v["component_count"] == 2,
        f"errors={v['errors']}")
    ms = A.mates(vp)
    chk("mates: read back (type, components, status)",
        len(ms) == 1 and ms[0]["type"] == "coincident" and ms[0]["align"] == "anti"
        and sorted(ms[0]["components"]) == sorted([vb.Name2, vp.Name2])
        and ms[0]["status"] == "ok", str(ms))
    d = A.free_dof(vp)
    chk("free_dof: plate on a face slides in X/Z and spins about Y",
        d["free_translations"] == ["X", "Z"] and d["free_rotations"] == ["Y"], str(d))
    try:
        A.mate(A.find_face(vb, "plane", 1, True), A.find_face(vp, "plane", 1, False),
               "distance", align="anti", distance_mm=30)
    except Exception:  # noqa: BLE001 -- an over-defining mate may be refused: fine either way
        pass
    v = I.validate(interferences=False)
    chk("validate: a conflicting mate makes it invalid",
        not v["valid"] and any(e["type"] in ("mate", "feature_rebuild") for e in v["errors"]),
        f"{[e['message'] for e in v['errors']]}")
    A.new_assembly()
    A.add_component(P_BASE, (0, 0, 0), fixed=True)
    A.add_component(P_PIN, (50, 0, 50))
    v = I.validate()
    chk("validate: interference makes it invalid",
        not v["valid"] and any(e["type"] == "interference" for e in v["errors"]),
        f"{v.get('interferences')}")

    T.close_scratch()
    T.finish()
    print(f"\nSUMMARY: {chk.ok}/{chk.n} PASS")


if __name__ == "__main__":
    main()
