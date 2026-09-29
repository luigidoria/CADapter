"""check_compare.py -- the version comparator, judged against the findings it produced.

Deterministic: no SolidWorks, no network. It feeds `compare_version` the signatures the
2026-09-21 battery actually recorded and asks whether the verdict is the right one.

Why it exists: that battery came back with "two real differences between SW 2017 and
SW 2026, and both are SolidWorks behaviour, not ours". One of the two was the
comparator reading a box that covers ONE body of a two-body part -- the solid had not
moved 40 mm, the reading had moved to the other plate. The comparison is the instrument;
when the instrument produces a finding, something has to be able to check the
instrument.

    .venv\\Scripts\\python.exe -u tests\\version\\check_compare.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare_version import compare_signature_part        # noqa: E402

FAILED = 0


def check(what, got, want):
    global FAILED
    ok = got == want
    if not ok:
        FAILED += 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {what}: {got}"
          + ("" if ok else f"\n         esperado {want}"))


# The numbers below are verbatim from step 4 of `gab_solid_combine`, the second
# `part.extrude` (merge=False), in reports sw25_3_0_20260921-092842 and
# sw34_3_2_20260921-093946. Two plates: volume and area cover BOTH (144000 mm3,
# 24000 mm2), the face list covers ONE (6 planar faces).
BASE = {"volume_mm3": 144000.0, "area_mm2": 24000.0, "com_mm": [50.0, 10.0, -30.0],
        "corpos": 2, "faces": {"planar": 6}, "raios_mm": [], "cylinders": [],
        "arestas": {"total": 12, "retas": 12, "curvas": 0}}
SW2017 = dict(BASE, caixa_mm=[0.0, 0.0, -60.0, 60.0, 20.0, 0.0], caixa_parcial=True)
SW2026 = dict(BASE, caixa_mm=[40.0, 0.0, -60.0, 100.0, 20.0, 0.0], caixa_parcial=True)

print("== the phantom difference of 2026-09-21 ==")
check("a multi-body box that picked another body is NOT a difference",
      compare_signature_part(SW2017, SW2026), [])

print("\n== and the comparator did not go blind ==")
# /!\ COUNTER-PROOF. A rule that hides a moved box is only safe while it hides it for
# ONE body count. Without this leg, "suppress the box difference" could have been
# written as "never compare the box" and nothing here would have noticed.
ONE = dict(BASE, corpos=1, caixa_parcial=False)
check("the SAME displacement in a SINGLE-body part is still reported",
      compare_signature_part(
          dict(ONE, caixa_mm=[0.0, 0.0, -60.0, 60.0, 20.0, 0.0]),
          dict(ONE, caixa_mm=[40.0, 0.0, -60.0, 100.0, 20.0, 0.0])),
      ["box of the SAME size [60.0, 20.0, 60.0] in ANOTHER POSITION: "
       "origin [40.0, 0.0, -60.0] (reference [0.0, 0.0, -60.0]), "
       "displaced [40.0, 0.0, 0.0]"])
check("and a multi-body part still reports volume and body count",
      compare_signature_part(SW2017, dict(SW2026, volume_mm3=99999.0, corpos=3)),
      ["volume_mm3: 99999.0 mm3 (reference 144000.0)", "bodies: 3 (reference 2)"])
check("a box of a DIFFERENT SIZE is still reported in a SINGLE-body part",
      compare_signature_part(
          dict(ONE, caixa_mm=[0.0, 0.0, -60.0, 60.0, 20.0, 0.0]),
          dict(ONE, caixa_mm=[0.0, 0.0, -60.0, 999.0, 20.0, 0.0])),
      ["box: [999.0, 20.0, 60.0] (reference [60.0, 20.0, 60.0])"])
# /!\ And NOT in a multi-body one, SIZE included. The first draft of this file expected
# the opposite -- suppress the position, keep the size -- and that was wrong for a
# reason worth writing down: the box is built entirely from the face list, so every
# property of it, size as much as origin, belongs to whichever body got picked. Two
# plates of different sizes would differ in size too, and it would still say nothing
# about SolidWorks. Volume, area and the body count are the whole-part readings, and
# they stay compared.
check("and a DIFFERENT SIZE is not reported when the box is partial either",
      compare_signature_part(
          SW2017, dict(SW2026, caixa_mm=[0.0, 0.0, -60.0, 999.0, 20.0, 0.0])),
      [])

print("\n== the final combine, which was identical all along ==")
DONE = {"volume_mm3": 120000.0, "area_mm2": 18400.0, "com_mm": [50.0, 10.0, -30.0],
        "caixa_mm": [0.0, 0.0, -60.0, 100.0, 20.0, 0.0], "caixa_parcial": False,
        "corpos": 1, "faces": {"planar": 6}, "raios_mm": [], "cylinders": [],
        "arestas": {"total": 12, "retas": 12, "curvas": 0}}
check("`part.combine` itself never differed between the versions",
      compare_signature_part(DONE, dict(DONE)), [])

print("\n== SCOREBOARD ==")
print("  all ok" if not FAILED else f"  {FAILED} FAILED")
sys.exit(1 if FAILED else 0)
