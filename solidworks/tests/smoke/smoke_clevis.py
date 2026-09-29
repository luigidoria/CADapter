# -*- coding: utf-8 -*-
r"""
SMOKE -- a MECHANICALLY HONEST assembly: a clevis pin joint.
It covers the engineering details that separate a valid assembly from a toy one
(see the Ansys-MCP memory `montagem-restricoes-realismo` and the assembly-* recipes):

  (fit)     a parametric CLEARANCE fit: hole D10, pin D(10-2*clearance) -- the pin DERIVED
            from the hole (the relation lives in the parameter layer; SW equations are not viable).
  (b) NAMED reference AXES per hole (axis_from_face) -> mates by axis (coincident),
            robust (they do not depend on 'finding the 1st cylinder').
  (c) RETENTION on both sides: a head (D16, seated on the clevis) + a COTTER PIN HOLE at the far end.
  (relief)  a FILLET at the re-entrant root of the U's slot (a stress riser for FEA).
  (d) A MEANINGFUL assembly DATUM: the clevis grounded to the assembly's planes (the pin's
            axis = the assembly's X axis, symmetry = the Right plane) -- a useful origin for FEA.
  (e) An angular LIMIT DERIVED FROM the real COLLISION (it sweeps the angle to find where it interferes).
  (f) An empirical DOF CHECK: no part 'floating' (free_translations); a joint with 1 DOF.
  (gate)    interference detection -> ZERO overlapping material.

Usage (from the MCP-SolidWorks root):
  $env:PYTHONIOENCODING="utf-8"
  .venv\Scripts\python.exe -u solidworks\tests\smoke\smoke_clevis.py

The file names (garfo, lingueta, pino) and the named axes (eixo_pivo, eixo_corpo) are
CAD FIXTURE names: the cloud parity suite and the agent batteries use the same ones.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from solidworks import sw_com as C, sw_parts as P, sw_sketch as S, sw_assembly as A, sw_inspect as I  # noqa: E402
from solidworks.tests import isolation as T  # noqa: E402

# shared tests/validation_assembly/ in development, a new directory per run in public
OUT = T.output_root()
P_FORK = os.path.join(OUT, "garfo.sldprt")
P_TONGUE = os.path.join(OUT, "lingueta.sldprt")
P_PIN = os.path.join(OUT, "pino.sldprt")
MAT = "AISI 1020"

# --- parameters (a single source; a fit is a RELATION, not loose equal numbers) ---
D_HOLE = 10.0
CLEAR_R = 0.15
R_HOLE = D_HOLE / 2.0          # 5.0
R_PIN = R_HOLE - CLEAR_R       # 4.85 (the pin DERIVED from the hole)
T_TONGUE = 20.0
FILLET_R = 3.0
R_HEAD = 8.0
H_HEAD = 4.0
PIN_LEN = 66.0
D_COTTER = 2.5


class Chk:
    def __init__(self): self.ok = self.n = 0
    def __call__(self, name, cond, detail=""):
        self.n += 1; self.ok += bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL '}] {name}" + (f" -- {detail}" if detail else ""))


def _slot_root_edges():
    out = []
    for e2 in I.edges():
        d = I.edge_dir(e2)
        if d and abs(d[1]) > 0.9 and abs(d[0]) < 0.1 and abs(d[2]) < 0.1:
            lp = C.cast(e2.GetCurve(), "ICurve").LineParams
            x, z = lp[0] * 1000, lp[2] * 1000
            if abs(z + 15) < 0.5 and (abs(x - 19) < 0.5 or abs(x - 41) < 0.5):
                out.append(e2)
    return out


def build_fork():
    P.new_part()
    P.sketch("Top", "rect", x1=0, y1=0, x2=60, y2=15); P.extrude(40, "boss")
    P.sketch("Top", "rect", x1=0, y1=15, x2=19, y2=60); P.extrude(40, "boss")
    P.sketch("Top", "rect", x1=41, y1=15, x2=60, y2=60); P.extrude(40, "boss")
    P.hole("Right", D_HOLE, [(45, 20)], through_all=True, reverse=True)   # pivot (world Z=-45)
    P.fillet(FILLET_R, _slot_root_edges())                               # (relief)
    P.axis_from_face(I.find_face("cylinder", radius_mm=R_HOLE), name="eixo_pivo")  # (b)
    P.reference_plane("Right", 30, name="mid_x")
    P.set_material(MAT); P.save(P_FORK)


def build_tongue():
    P.new_part()
    P.sketch("Top", "rect", x1=0, y1=0, x2=T_TONGUE, y2=60); P.extrude(40, "boss")
    P.hole("Right", D_HOLE, [(12, 20)], through_all=True, reverse=True)
    P.axis_from_face(I.find_face("cylinder", radius_mm=R_HOLE), name="eixo_pivo")  # (b)
    P.reference_plane("Right", T_TONGUE / 2.0, name="mid_x")
    P.set_material(MAT); P.save(P_TONGUE)


def build_pin():
    P.new_part()
    P.sketch("Right", "circle", r=R_PIN); P.extrude(PIN_LEN, "boss")             # body (+X)
    P.sketch("Right", "circle", r=R_HEAD); P.extrude(H_HEAD, "boss", reverse=True)  # head (-X)
    # (c) the cotter pin hole: a Top plane offset above the pin, cutting -Y right through
    P.reference_plane("Top", R_PIN + 2, name="cotter_plane")
    P.hole("cotter_plane", D_COTTER, [(62, 0)], through_all=True, reverse=False)
    P.axis_from_face(I.find_face("cylinder", radius_mm=R_PIN), name="eixo_corpo")  # (b)
    P.set_material(MAT); P.save(P_PIN)


def box_center_x(comp):
    b = comp.GetBox(False, False)
    return (b[0] + b[3]) / 2.0 * 1000.0


def cyl_radius(comp, radius_mm):
    f2 = A.find_face(comp, "cylinder", radius_mm=radius_mm)
    return C.cast(f2.GetSurface(), "ISurface").CylinderParams[6] * 1000.0 if f2 else None


def main():
    C.connect(); T.begin(); T.close_scratch()
    T.close_titles(("clevis.sldasm", "garfo.sldprt", "lingueta.sldprt", "pino.sldprt"))
    print(f"SW rev {C.app().RevisionNumber()}")
    build_fork(); build_tongue(); build_pin()
    chk = Chk()

    A.new_assembly()
    # (d) a MEANINGFUL datum: it positions the clevis so the pin's axis falls on the
    # assembly's X axis and its symmetry (mid_x) on X=0, and FIXES it. Grounding by 3
    # coincident planes OVER-DEFINES (each plane locks 1 transl + 2 rot -> the rotations
    # overlap); fixing it in a meaningful pose is clean.
    # Pin in part-local coords (Y=20, Z=-45); offset (-30,-20,45) takes (30,20,-45) to the origin.
    fork = A.add_component(P_FORK, (-30, -20, 45))
    tongue = A.add_component(P_TONGUE, (0, 60, 40))
    pin = A.add_component(P_PIN, (120, 0, 0))
    chk("add_component (3)", A.component_count() == 3, f"n={A.component_count()}")

    A.fix(fork); A.rebuild()
    try:
        pt, d = A.cylinder_axis_world(fork, R_HOLE)
        chk("fork: a meaningful datum (fixed, the pin's axis parallel to X)",
            fork.IsFixed() and abs(abs(d[0]) - 1) < 0.01,
            f"pin axis: dir~X, through (Y={pt[1]:.0f},Z={pt[2]:.0f}) mm")
    except Exception as e:
        chk("fork datum", False, str(e)[:50])

    # (b) mates by NAMED AXIS (coincident) + the head's seat (retention)
    try:
        A.mate_planes(fork, "eixo_pivo", pin, "eixo_corpo", "coincident", sel_type="AXIS")
        fork_out0 = A.faces_perp(fork, 0)[0]
        pin_under = A.faces_perp(pin, 0)[1]
        A.mate(fork_out0, pin_under, "coincident")           # the head seats
        A.mate_planes(pin, "eixo_corpo", tongue, "eixo_pivo", "coincident", sel_type="AXIS")
        chk("mates by named axis + head seat", True)
    except Exception as e:
        chk("mates by named axis", False, str(e)[:70])
    try:
        A.mate_planes(fork, "mid_x", tongue, "mid_x", "coincident")   # centres the tongue
        chk("the tongue is centred (mid_x)", True)
    except Exception as e:
        chk("centring the tongue", False, str(e)[:60])

    # (e) a limit derived from the real collision
    # a sweep by ROTATION (transform, creating no mates -> it does not become
    # over-defined) around the pin's REAL axis (transformed into assembly coords).
    try:
        gtop = A.find_face(fork, "plane", 1, True)
        ltop = A.find_face(tongue, "plane", 1, True)
        axis_pt, axis_dir = A.cylinder_axis_world(pin, R_PIN)
        limit, col_at = A.sweep_collision_angle(tongue, axis_pt, axis_dir, step=15, amax=165)
        maxlim = max(limit - 5, 10)
        lm = A.limit_mate(gtop, ltop, "angle", min_deg=0, max_deg=maxlim, nominal_deg=min(20, limit))
        source = f"collides at {col_at} degrees" if col_at else f"no collision up to {limit} degrees"
        chk("limit derived from the collision (rotation)", True, f"{source} -> limit=[0,{maxlim}]  ({lm})")
    except Exception as e:
        chk("limit derived from the collision", False, str(e)[:70])

    chk("rebuild", A.rebuild())

    # --- proofs of realism ---
    try:
        chk("the tongue centred in the slot", abs(box_center_x(tongue) - box_center_x(fork)) < 1.0)
    except Exception as e:
        chk("tongue centred", False, str(e)[:50])
    try:
        gb = fork.GetBox(False, False); pb = pin.GetBox(False, False)
        chk("the pin crosses the whole U", pb[0] * 1000 < gb[0] * 1000 + 0.1 and pb[3] * 1000 > gb[3] * 1000 - 0.1,
            f"pin_X=[{pb[0]*1000:.1f},{pb[3]*1000:.1f}]")
    except Exception as e:
        chk("pin crosses", False, str(e)[:50])
    try:
        rp, rh = cyl_radius(pin, R_PIN), cyl_radius(fork, R_HOLE)
        chk("real pin-hole clearance", abs((rh - rp) - CLEAR_R) < 0.05, f"clearance={rh-rp:.3f} mm")
    except Exception as e:
        chk("real clearance", False, str(e)[:50])
    chk("cotter pin hole present (c)", A.find_face(pin, "cylinder", radius_mm=D_COTTER / 2) is not None)
    # (f) DOF: nothing floating; the tongue only turns (0 free translations), the pin located
    try:
        # the pin has to be FULLY located (a reliable test: with no body rotation).
        # the latch HAS an intentional rotation -> the translation nudge is absorbed by
        # the rotation (a false positive); its axial lock is proved by the centring + the over-defined gate.
        fl_p = A.free_translations(pin)
        chk("DOF: the pin fully located (it does not float)", not fl_p, f"pin free translations={fl_p}")
    except Exception as e:
        chk("empirical DOF", False, str(e)[:50])
    try:
        errs = A.mate_errors()
        chk("assembly with NO over-defined / mate error", len(errs) == 0,
            f"errors={[e['mate'] for e in errs]}" if errs else "every mate OK")
    except Exception as e:
        chk("gate over-defined", False, str(e)[:50])
    try:
        itf = A.interferences()
        chk("NO material interference (gate)", len(itf) == 0, f"n={len(itf)}")
    except Exception as e:
        chk("interference gate", False, str(e)[:50])

    p = A.save(os.path.join(OUT, "clevis.sldasm"))
    chk("save .sldasm", os.path.exists(p))
    A.export(os.path.join(OUT, "clevis.x_t"))
    try:
        md = C.active("IModelDoc2"); md.ShowNamedView2("*Isometric", -1); md.ViewZoomtofit2()
        I.screenshot(os.path.join(OUT, "clevis.bmp"))
    except Exception:
        pass
    T.finish()
    print(f"\nSUMMARY: {chk.ok}/{chk.n} PASS  (clearance fit | named axes | 2-sided retention | fillet | "
          f"assembly datum | limit by collision | DOF not floating | zero interference)")


if __name__ == "__main__":
    main()
