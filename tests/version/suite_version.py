# -*- coding: utf-8 -*-
r"""
suite_version.py -- runs the verbs and records a fingerprint per STEP, to compare the
SAME suite between SolidWorks versions.

Why it exists: the verb suite answers "did the verb execute?".
That is not enough -- the PART suite's `pattern_linear` passed 111/111 having created
the pattern with ZERO instances, because the test only checked `bool(pat)`. What tells
"it executed" from "it came out right" is the GEOMETRY, and what tells "a SW 2026 bug"
from "a bug that always existed" is running the same script on a SW 2017 machine.

This suite does NOT judge geometry on its own (there is no hand-written golden, which
would be wrong). It RECORDS; the oracle is the other version's report, compared by
`compare_version.py`. A step that ERRORS, though, is recorded as an error -- that it does judge.

Usage. Every scenario runs on the CURATED layer (`solidworks.catalog`), in this process
and with no server; `--local` is still accepted and changes nothing (the compiled
catalog it once chose was retired on 2026-09-27):
  $env:PYTHONIOENCODING="utf-8"
  .venv\Scripts\python.exe -u tests\version\suite_version.py
  .venv\Scripts\python.exe -u tests\version\suite_version.py --domain part
  .venv\Scripts\python.exe -u tests\version\suite_version.py --scenario bloco_ordem_a
  .venv\Scripts\python.exe -u tests\version\suite_version.py --scenario a --scenario b --expected-revision 25
  .venv\Scripts\python.exe -u tests\version\suite_version.py --out meu_relatorio.json

The report is written to `tests/version/reports/sw<rev>_<stamp>.json`. A run narrowed by
--domain/--scenario is PARTIAL: its report is named `partial_sw<rev>_<stamp>.json` and
carries `meta.filter`, and it refuses --baseline -- a subset must never pass for the
full reference.

Documents and files. In development every scenario starts with CloseAllDocuments(True)
and writes to tests/version/work/. In isolated mode (the public default, see
solidworks/tests/isolation.py) a scenario closes only documents this battery opened,
and the work directory is new for the run: <CADAPTER_TEST_OUT>/version_work when a
runner set one, else a fresh temporary directory. CADAPTER_VERSION_WORK overrides both.
Reference-grade evidence still wants NO other document open: the battery warns when
there are some, and leaves them alone.

Verdict (tests/release_gate.py): every declared step is PASS, FAIL (it ran and did not do
what was asked), ERROR (it raised), NOT RUN (the scenario stopped before it) or KNOWN (an
ERROR/FAIL at a step the scenario declares in its `known` map, {step index: reason}).
Exit code: 0 no ERROR, FAIL or NOT RUN; 1 any of them; 2 could not run (no scenario
matched, SOLIDWORKS did not answer). The report is written in every case. The same error
on both versions is still an ERROR: agreement with the other report is the comparator's
question, not an acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

# /!\ The cloud harness is NOT imported here. It is not in every checkout (the public
# repository has no server), and a module-level import made the whole suite
# fail to start there -- sheet metal and the local engine included, which need no cloud.
# `_hygiene` imports it only when a scenario really runs on the cloud.

import goldens  # noqa: E402  (mesma pasta)
import surface  # noqa: E402
from solidworks.tests import isolation as T  # noqa: E402  (no COM at import)
from tests import release_gate as G  # noqa: E402  (no COM)

OUT = os.path.join(HERE, "reports")


def _work_dir() -> str:
    """Where the scenarios write. Computed at import (SCENARIOS embed these paths) and
    created only by `main`, so importing this module -- check_scenarios does -- writes
    nothing. The path itself never reaches a report: `normalise` swaps it for <TRAB>."""
    explicit = os.environ.get("CADAPTER_VERSION_WORK", "").strip()
    if explicit:
        return os.path.abspath(explicit)
    if not T.isolated():
        return os.path.join(HERE, "work")
    shared = os.environ.get(T.ENV_OUT, "").strip()
    if shared:
        return os.path.join(os.path.abspath(shared), "version_work")
    # realpath: a temp directory reported in 8.3 form (USERNA~1) would come back from
    # SOLIDWORKS in long form and escape the <TRAB> substitution
    return os.path.join(os.path.realpath(tempfile.gettempdir()),
                        f"cadapter-version-work-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}")


WORK = _work_dir()


# ══════════════════════════════════════════════════════════════════════════════
# The scenarios
# ══════════════════════════════════════════════════════════════════════════════
# One scenario = a sequence of (verb, params). There is deliberately NO expected result
# here: demanding a hand-written number measures my arithmetic, not SolidWorks.
#
# The `_ordem_a` / `_ordem_b` pairs build with the SAME verbs in a DIFFERENT ORDER.
# They are not required to give the same part (fillet-then-shell and shell-then-fillet
# are legitimately different parts) -- the value is in exercising distinct paths and
# comparing each with itself on the other version.
#
# A DELIBERATE PREFERENCE for verbs WITHOUT a handle ('x' instead of @h7, 'top' instead
# of @h3): a handle is numbered by call order and does not survive a comparison between
# machines. Where a handle is unavoidable, use {"$passo": N} (see `_resolve`).

def _file(name):
    return os.path.join(WORK, name)


SCENARIOS = [
    # ── PART: solidos basicos ────────────────────────────────────────────────
    {"name": "bloco_simples", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 80, "height_mm": 60, "depth_mm": 20}),
    ]},
    {"name": "extrude_por_sketch", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.sketch", {"plane": "Front", "shape": "rect",
                         "x1": 0, "y1": 0, "x2": 50, "y2": 30}),
        ("part.extrude", {"depth_mm": 20}),
    ]},
    {"name": "extrude_reverse", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.sketch", {"plane": "Front", "shape": "rect",
                         "x1": 0, "y1": 0, "x2": 50, "y2": 30}),
        ("part.extrude", {"depth_mm": 20, "reverse": True}),
    ]},
    {"name": "cilindro", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.cylinder", {"plane": "Top", "diameter_mm": 30, "height_mm": 40}),
    ]},
    {"name": "revolve", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("sketch.begin", {"plane": "Front"}),
        ("sketch.centerline", {"x1": 0, "y1": 0, "x2": 0, "y2": 50}),
        ("sketch.rect", {"x1": 10, "y1": 0, "x2": 20, "y2": 40}),
        ("sketch.end", {}),
        ("part.revolve", {"angle_deg": 360}),
    ]},

    # ── PART: mesma materia-prima, ORDENS diferentes ─────────────────────────
    {"name": "bloco_ordem_a_filete_casca", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 80, "height_mm": 60, "depth_mm": 20}),
        ("part.fillet", {"radius_mm": 4, "edges": "all"}),
        ("part.shell", {"thickness_mm": 3, "remove_faces": ["top"]}),
    ]},
    {"name": "bloco_ordem_b_casca_filete", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 80, "height_mm": 60, "depth_mm": 20}),
        ("part.shell", {"thickness_mm": 3, "remove_faces": ["top"]}),
        # 1 mm, not the 4 mm of order A. After the shell the open top is a RIM only
        # 3 mm wide, and "all" rounds it: a radius >= 3 cannot fit on it, so 4 mm was
        # refused on both versions (the historical baselines) -- the scenario asked for
        # impossible geometry, not a verb defect. Measured 2026-09-28 on SW 2026: 1.5
        # and 2 mm are also accepted, but with 2r > 3 the fillets on the rim's two
        # edges overlap and the result leans on SOLIDWORKS' overflow handling; 1 mm
        # keeps them apart, so the case measures a plain fillet on both versions.
        ("part.fillet", {"radius_mm": 1, "edges": "all"}),
    ]},
    {"name": "furo_ordem_a_furo_depois_filete", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 80, "height_mm": 60, "depth_mm": 20}),
        ("part.hole", {"face": "top", "diameter_mm": 10, "positions": [[20, 10]]}),
        ("part.fillet", {"radius_mm": 3, "edges": "all"}),
    ]},
    {"name": "furo_ordem_b_filete_depois_furo", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 80, "height_mm": 60, "depth_mm": 20}),
        ("part.fillet", {"radius_mm": 3, "edges": "all"}),
        ("part.hole", {"face": "top", "diameter_mm": 10, "positions": [[20, 10]]}),
    ]},

    # -- PART: patterns -- where the 111/111 bug was hiding --------------------
    # `pattern_linear` creates the feature even when NO instance fits in the
    # material (the server documents that in verbs_part.py). The signature catches it:
    # 3 holes give 3 equal radii in `raios_mm`, 1 hole gives one.
    {"name": "padrao_linear_x", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 100, "height_mm": 60, "depth_mm": 15}),
        ("part.hole", {"face": "top", "diameter_mm": 8, "positions": [[20, 7.5]]}),
        ("part.pattern_linear", {"seed_feature": "Cut-Extrude1", "direction_edge": "x",
                                 "count": 3, "spacing_mm": 20}),
    ]},
    {"name": "padrao_linear_x_reverse", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 100, "height_mm": 60, "depth_mm": 15}),
        ("part.hole", {"face": "top", "diameter_mm": 8, "positions": [[20, 7.5]]}),
        ("part.pattern_linear", {"seed_feature": "Cut-Extrude1", "direction_edge": "x",
                                 "count": 3, "spacing_mm": 20, "reverse": True}),
    ]},
    {"name": "padrao_linear_y", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 100, "height_mm": 60, "depth_mm": 15}),
        # `front` and not `top`, and the face is the WHOLE point of this case. A hole on
        # `top` (+Y) is drilled ALONG Y, so patterning in "y" slides the copies down the
        # hole's own axis, out of the material: the verb builds an empty pattern, with no
        # error, and the case measures nothing. Measured 2026-09-21 on the three
        # directions: from `top` only "x" builds ("z" fails too -- the face is only 15 mm
        # deep and the spacing is 15). On `front` (+Z) the face plane is (x, y), so "y" is
        # a real direction and three holes fit in the 60 mm.
        ("part.hole", {"face": "front", "diameter_mm": 8, "positions": [[20, 7.5]]}),
        ("part.pattern_linear", {"seed_feature": "Cut-Extrude1", "direction_edge": "y",
                                 "count": 3, "spacing_mm": 15}),
    ]},
    {"name": "padrao_circular", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.cylinder", {"plane": "Top", "diameter_mm": 80, "height_mm": 10}),
        # reverse=True: the cylinder grows from the Top plane, and the default cut
        # direction of a sketch on that same plane points AWAY from it -- the hole cut
        # nothing and raised on both versions, so pattern_circular had never run.
        ("part.hole", {"plane": "Top", "diameter_mm": 6, "positions": [[25, 0]],
                       "reverse": True}),
        # pattern_circular takes a NAMED axis (SelectByID2 "AXIS"), not 'x'/'y'/'z' as
        # pattern_linear does: "y" selected nothing. Front x Right is the vertical axis
        # through the origin -- the cylinder's own axis, since it grows from Top at (0, 0).
        ("part.reference_axis", {"plane_a": "Front", "plane_b": "Right"}),
        ("part.pattern_circular", {"seed_feature": "Cut-Extrude1", "axis": "$3",
                                   "count": 6, "total_angle_deg": 360}),
    ]},
    {"name": "padrao_espelho", "domain": "part", "steps": [
        ("part.new_part", {}),
        # `x: -40` is what makes the mirror POSSIBLE. `part.block` draws a
        # CreateCornerRectangle from (x, y), so by default the block spans x in [0, 80]
        # and lies entirely on ONE side of the Right plane (x = 0) -- the copy of a hole
        # at x=20 lands at x=-20, in thin air, and the mirror comes out empty with no
        # error. Offset, the block straddles the plane. Measured 2026-09-21: as it was,
        # the volume did not move; offset, it drops by exactly one hole.
        ("part.block", {"width_mm": 80, "height_mm": 60, "depth_mm": 15, "x": -40}),
        ("part.hole", {"face": "top", "diameter_mm": 8, "positions": [[20, 7.5]]}),
        ("part.pattern_mirror", {"seed_feature": "Cut-Extrude1",
                                 "mirror_plane": "Right"}),
    ]},

    # ── PART: furos e acabamento ─────────────────────────────────────────────
    {"name": "furo_passante_vs_cego", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 60, "height_mm": 60, "depth_mm": 30}),
        ("part.hole", {"face": "top", "diameter_mm": 10, "positions": [[15, 15]]}),
        ("part.hole", {"face": "top", "diameter_mm": 10, "positions": [[45, 15]],
                          "depth_mm": 10}),
    ]},
    {"name": "escareado", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 100, "height_mm": 60, "depth_mm": 10}),
        ("part.countersink", {"face": "front", "positions": [[25, 30]],
                              "d_hole_mm": 8, "d_sink_mm": 16, "angle_deg": 90}),
    ]},
    {"name": "chanfro_total", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 50, "height_mm": 50, "depth_mm": 20}),
        ("part.chamfer", {"dist_mm": 3, "edges": "all"}),
    ]},
    {"name": "ressalto_sobre_face", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 80, "height_mm": 60, "depth_mm": 15}),
        ("part.block", {"face": "top", "width_mm": 30, "height_mm": 20, "depth_mm": 12,
                        "at": [0, 0]}),
    ]},

    # ── PART: corpo e parametrico ────────────────────────────────────────────
    {"name": "escala", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 40, "height_mm": 40, "depth_mm": 40}),
        ("part.scale", {"factor": 2.0}),
    ]},
    {"name": "cotas_parametricas", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 50, "height_mm": 30, "depth_mm": 20}),
        ("inspect.model_summary", {"brief": True}),
        ("part.set_dimension", {"name": "D1@Boss-Extrude1", "value_mm": 35}),
        ("inspect.validate", {"interferences": False}),
    ]},
    {"name": "material_e_propriedade", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 40, "height_mm": 40, "depth_mm": 10}),
        ("part.set_material", {"name": "6061 Alloy"}),
        ("part.get_material", {}),
        ("part.set_property", {"name": "Description", "value": "bateria de versao"}),
        ("part.get_property", {"name": "Description"}),
    ]},
    {"name": "datums", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 60, "height_mm": 40, "depth_mm": 20}),
        ("part.reference_plane", {"from_plane": "Top", "offset_mm": 40, "name": "plano_alto"}),
        ("part.reference_axis", {"plane_a": "Front", "plane_b": "Right", "name": "eixo_v"}),
    ]},
    {"name": "exportar_formatos", "domain": "part", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 40, "height_mm": 30, "depth_mm": 10}),
        ("part.save", {"path": _file("bv_bloco.sldprt")}),
        ("part.export", {"path": _file("bv_bloco.x_t")}),
        ("part.export", {"path": _file("bv_bloco.step")}),
        ("part.export", {"path": _file("bv_bloco.stl")}),
    ]},

    # ── ASSEMBLY ─────────────────────────────────────────────────────────────
    {"name": "asm_pecas_de_apoio", "domain": "asm", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 100, "height_mm": 100, "depth_mm": 20}),
        ("part.save", {"path": _file("bv_base.sldprt")}),
        ("part.new_part", {}),
        ("part.block", {"width_mm": 60, "height_mm": 60, "depth_mm": 15}),
        ("part.save", {"path": _file("bv_tampa.sldprt")}),
    ]},
    {"name": "asm_mate_faces_empilhado", "domain": "asm", "steps": [
        ("asm.new_assembly", {}),
        ("asm.add_component", {"path": _file("bv_base.sldprt"), "xyz": [0, 0, 0],
                               "fixed": True}),
        ("asm.add_component", {"path": _file("bv_tampa.sldprt"), "xyz": [0, 0, 50]}),
        ("asm.mate_faces", {"comp_a": "bv_base-1", "face_a": "top",
                            "comp_b": "bv_tampa-1", "face_b": "bottom",
                            "kind": "coincident"}),
        ("asm.interferences", {}),
        ("asm.mate_errors", {}),
        ("asm.components", {}),
    ]},
    {"name": "asm_mate_ordem_invertida", "domain": "asm", "steps": [
        ("asm.new_assembly", {}),
        ("asm.add_component", {"path": _file("bv_base.sldprt"), "xyz": [0, 0, 0],
                               "fixed": True}),
        ("asm.add_component", {"path": _file("bv_tampa.sldprt"), "xyz": [0, 0, 50]}),
        ("asm.mate_faces", {"comp_a": "bv_tampa-1", "face_a": "bottom",
                            "comp_b": "bv_base-1", "face_b": "top",
                            "kind": "coincident"}),
        ("asm.interferences", {}),
        ("asm.components", {}),
    ]},
    {"name": "asm_remover_e_recontar", "domain": "asm", "steps": [
        ("asm.new_assembly", {}),
        ("asm.add_component", {"path": _file("bv_base.sldprt"), "xyz": [0, 0, 0],
                               "fixed": True}),
        ("asm.add_component", {"path": _file("bv_tampa.sldprt"), "xyz": [0, 0, 50]}),
        ("asm.component_count", {"top_only": True}),
        ("asm.remove_component", {"comp": "bv_tampa-1"}),
        ("asm.component_count", {"top_only": True}),
        ("asm.components", {}),
    ]},

    # -- DRAWING ---------------------------------------------------------------
    {"name": "dwg_folha_e_vistas", "domain": "dwg", "steps": [
        # it creates its own part: running `--domain dwg` on its own must not depend
        # on a file that only exists if the `part` domain ran first
        ("part.new_part", {}),
        ("part.block", {"width_mm": 120, "height_mm": 40, "depth_mm": 25}),
        ("part.save", {"path": _file("bv_viga.sldprt")}),
        ("dwg.new_drawing", {"model_path": _file("bv_viga.sldprt"), "size": "A3"}),
        ("dwg.add_view", {"kind": "front", "at": [100, 200],
                          "model_path": _file("bv_viga.sldprt")}),
        ("dwg.add_view", {"kind": "top", "at": [100, 120],
                          "model_path": _file("bv_viga.sldprt")}),
        ("dwg.add_view", {"kind": "iso", "at": [250, 200],
                          "model_path": _file("bv_viga.sldprt")}),
        ("dwg.set_units", {"units": "mm"}),
        ("dwg.import_model_dims", {"all_views": True}),
        ("dwg.save", {"path": _file("bv_viga.slddrw")}),
        ("dwg.export", {"path": _file("bv_viga.pdf")}),
    ]},
]


# ══════════════════════════════════════════════════════════════════════════════
# SHEET METAL -- the CURATED surface
# ══════════════════════════════════════════════════════════════════════════════
# When these were written, sheet metal did NOT exist in the cloud catalog (118 verbs,
# none of them sheet); it has 44 `sheet.*` verbs since 2026-09-07. The scenarios stay on
# the curated layer (`solidworks.catalog`, 47 verbs), which needs no server to run.
# That is why these scenarios declare `surface: "curated"` -- and why the suite
# had to become bi-surface instead of choosing one.
SCENARIOS += [
    {"name": "chapa_caixa_planifica", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.is_sheet", {}),
         ("sheet.thickness", {}),
         ("sheet.box_flanges", {"length_mm": 20}),
         ("sheet.bend_info", {}),
         # export_flat NEEDS the part saved -- CLAUDE.md, sheet metal section
         ("part.save", {"path": _file("bv_caixa.sldprt")}),
         ("sheet.flatten", {"on": True}),
         ("sheet.is_flat", {}),
         ("sheet.bbox_mm", {}),
         ("sheet.export_flat", {"path": _file("bv_caixa.dxf")}),
         ("sheet.cut_list", {}),
     ]},
    {"name": "chapa_planifica_e_volta", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 80, "h_mm": 50,
                              "thickness_mm": 1.5}),
         ("sheet.box_flanges", {"length_mm": 15}),
         ("sheet.flatten", {"on": True}),
         ("sheet.is_flat", {}),
         # folding back has to RECOVER the earlier box: flattening is a change
         # of STATE, not of geometry, and the signature proves that
         ("sheet.flatten", {"on": False}),
         ("sheet.is_flat", {}),
     ]},
    {"name": "chapa_espessura_parametrica", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 60, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.thickness", {}),
         ("sheet.set_thickness", {"thickness_mm": 3}),
         ("sheet.thickness", {}),
     ]},
    {"name": "chapa_de_solido_comum", "domain": "part", "surface": "curated",
     "steps": [
         # the other path: whoever ALREADY has the part modelled as a solid and only
         # wants the DXF. It has to be an L profile: `convert_to_sheet` finds the bends
         # by the CONCAVE sharp corners, and a straight block has none (a measured refusal).
         # `part.block` does not exist on this surface either -- it is a convenience of
         # NUVEM; a curada tem 37 verbos de part contra 51 da nuvem, e os conjuntos
         # do not coincide. The L comes from the `sketch` domain, which thus also enters
         # bateria.
         ("part.new_part", {}),
         ("sketch.begin", {"plane": "Front"}),
         ("sketch.line", {"x1": 0, "y1": 0, "x2": 60, "y2": 0}),
         ("sketch.line", {"x1": 60, "y1": 0, "x2": 60, "y2": 2}),
         ("sketch.line", {"x1": 60, "y1": 2, "x2": 2, "y2": 2}),
         ("sketch.line", {"x1": 2, "y1": 2, "x2": 2, "y2": 40}),
         ("sketch.line", {"x1": 2, "y1": 40, "x2": 0, "y2": 40}),
         ("sketch.line", {"x1": 0, "y1": 40, "x2": 0, "y2": 0}),
         ("sketch.end", {}),
         ("part.extrude", {"depth_mm": 50}),
         ("sheet.convert_to_sheet", {"thickness_mm": 2.0}),
         ("sheet.is_sheet", {}),
         ("sheet.flatten", {"on": True}),
     ]},
]

# ══════════════════════════════════════════════════════════════════════════════
# SHEET METAL, widened -- the rest of the `sheet.*` family
# ══════════════════════════════════════════════════════════════════════════════
# The four scenarios above exercised 13 of the 47 curated sheet verbs. Every one below
# is a recipe that already passes in `solidworks/tests/smoke/smoke_sheetmetal.py` (the block named in
# each comment), rewritten as flat (verb, params) steps so the SAME build runs on both
# SolidWorks versions. One document per scenario: an error stops only its own scenario.
#
# Left out on purpose:
#   - `set_gauge`, `gauge_radii`, `set_bend_table`: they need a gauge/bend table FILE,
#     and its path is part of the SolidWorks install -- it differs between 2017 and
#     2026, so a hard-coded path would be a version difference in the scenario, not in
#     the verb. `gauge_table()` with no path (the read) is covered.
#   - `rip`: its smoke recipe picks the edge with `inspect.find_edge(1)` on a shelled
#     box, which returns whichever edge comes first -- not stable enough to compare.
SCENARIOS += [
    # smoke block `params`: the edge flange and everything that edits a bend after it
    {"name": "chapa_aba_parametros", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.free_edges", {}),
         ("sheet.edge_flange", {"edge": "$1[0]", "length_mm": 25}),
         ("sheet.bends", {}),
         ("sheet.bend_info", {}),
         ("sheet.last_feature", {}),
         ("sheet.bend_params", {}),
         ("sheet.set_bend_radius", {"radius_mm": 8}),
         ("sheet.set_flange", {"radius_mm": 5}),
         ("sheet.set_k_factor", {"k_factor": 0.4}),
         ("sheet.bend_info", {}),
         ("sheet.flat_options", {"merge": False, "simplify": False}),
     ]},
    # smoke block `hem`
    {"name": "chapa_bainha", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.free_edges", {}),
         ("sheet.hem", {"edge": "$1[0]", "kind": "closed", "length_mm": 5}),
         ("sheet.last_feature", {}),
     ]},
    # smoke block `borda`
    {"name": "chapa_quebra_de_canto", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.base_face", {}),
         ("sheet.break_corner", {"entities": ["$1"], "radius_mm": 5, "kind": "fillet"}),
         ("sheet.cross_break", {}),
     ]},
    # smoke block `corte`: unfold -> cut ACROSS the bend region -> fold back
    {"name": "chapa_desdobra_corta_dobra", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.free_edges", {}),
         ("sheet.edge_flange", {"edge": "$1[0]", "length_mm": 30}),
         ("sheet.unfold", {}),
         ("sheet.bbox_mm", {}),
         ("sheet.fold", {}),
         ("sheet.bbox_mm", {}),
         ("sketch.begin", {"plane": "Front"}),
         ("sketch.circle", {"cx": 50, "cy": 30, "r": 6}),
         ("sketch.end", {}),
         ("sheet.last_sketch", {}),
         ("sheet.cut", {"sketch_name": "$9"}),
     ]},
    # smoke block `dobra_esbocada`
    {"name": "chapa_dobra_esbocada", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.sketched_bend", {"line": [[60, -10, 2], [60, 70, 2]], "angle_deg": 90}),
         ("sheet.bends", {}),
         ("sheet.flatten", {"on": True}),
         ("sheet.is_flat", {}),
         ("sheet.flatten", {"on": False}),
     ]},
    # smoke block `degrau` -- the jog takes its angle in DEGREES (CLAUDE.md gotcha)
    {"name": "chapa_degrau", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.jog", {"line": [[50, -10, 0], [50, 70, 0]], "offset_mm": 10}),
         ("sheet.bends", {}),
         ("sheet.bbox_mm", {}),
     ]},
    # smoke block `mitrada`
    {"name": "chapa_aba_mitrada", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.free_edges", {}),
         ("sheet.miter_flange", {"edge": "$1[0]", "length_mm": 20}),
         ("sheet.bends", {}),
     ]},
    # smoke block `canto_fechado`, the four-flange box
    {"name": "chapa_cantos_fechados", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.box_flanges", {"length_mm": 20}),
         ("sheet.open_corners", {}),
         ("sheet.closed_corner", {}),
         ("sheet.open_corners", {}),
         ("sheet.flatten", {"on": True}),
         ("sheet.is_flat", {}),
     ]},
    # smoke block `apara_canto`: it only exists on the FLATTENED part.
    # /!\ The sheet needs a flange first. Flattening a sheet with NO bend leaves its box
    # unchanged, so the `flatten` oracle reported "did not do" -- measured on the first
    # run of this scenario. The verb was right; the scenario asked a flat sheet to flatten.
    {"name": "chapa_apara_canto", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.free_edges", {}),
         ("sheet.edge_flange", {"edge": "$1[0]", "length_mm": 20}),
         ("sheet.flatten", {"on": True}),
         ("sheet.corner_trim", {}),
         ("sheet.is_flat", {}),
     ]},
    # smoke block `nervura`
    {"name": "chapa_nervura", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sheet.free_edges", {}),
         ("sheet.edge_flange", {"edge": "$1[0]", "length_mm": 25}),
         ("sheet.bend_faces", {}),
         ("sheet.gusset", {}),
     ]},
    # smoke block `lofted`: the transition sheet between two open profiles
    {"name": "chapa_transicao", "domain": "part", "surface": "curated",
     "steps": [
         ("part.new_part", {}),
         ("sketch.begin", {"plane": "Front"}),
         ("sketch.line", {"x1": 0, "y1": 0, "x2": 60, "y2": 0}),
         ("sketch.line", {"x1": 60, "y1": 0, "x2": 60, "y2": 40}),
         ("sketch.line", {"x1": 60, "y1": 40, "x2": 0, "y2": 40}),
         ("sketch.end", {}),
         ("part.reference_plane", {"from_plane": "Front", "offset_mm": 80,
                                   "name": "PlanoTopo"}),
         ("sketch.begin", {"plane": "PlanoTopo"}),
         ("sketch.line", {"x1": 10, "y1": 10, "x2": 50, "y2": 10}),
         ("sketch.line", {"x1": 50, "y1": 10, "x2": 50, "y2": 30}),
         ("sketch.line", {"x1": 50, "y1": 30, "x2": 10, "y2": 30}),
         ("sketch.end", {}),
         ("sheet.lofted_bend", {"sketch_a": "$5", "sketch_b": "$11", "thickness_mm": 2}),
         ("sheet.is_sheet", {}),
         ("sheet.thickness", {}),
     ]},
    # smoke block `multicorpo`: a second base flange makes a second body
    {"name": "chapa_multicorpo", "domain": "part", "surface": "curated",
     "steps": [
         ("sheet.new_sheet", {"plane": "Front", "w_mm": 100, "h_mm": 60,
                              "thickness_mm": 2}),
         ("sketch.begin", {"plane": "Front"}),
         ("sketch.rect", {"x1": 150, "y1": 0, "x2": 230, "y2": 40}),
         ("sketch.end", {}),
         ("sheet.base_flange", {"thickness_mm": 3, "radius_mm": 2}),
         ("sheet.sheet_bodies", {}),
         ("sheet.bbox_mm", {}),
         ("sheet.cut_lists", {}),
         ("sheet.gauge_table", {}),
     ]},
    # smoke block `convert`: the READS the conversion is built on
    {"name": "chapa_de_solido_leituras", "domain": "part", "surface": "curated",
     "steps": [
         ("part.new_part", {}),
         ("sketch.begin", {"plane": "Front"}),
         ("sketch.line", {"x1": 0, "y1": 0, "x2": 60, "y2": 0}),
         ("sketch.line", {"x1": 60, "y1": 0, "x2": 60, "y2": 2}),
         ("sketch.line", {"x1": 60, "y1": 2, "x2": 2, "y2": 2}),
         ("sketch.line", {"x1": 2, "y1": 2, "x2": 2, "y2": 40}),
         ("sketch.line", {"x1": 2, "y1": 40, "x2": 0, "y2": 40}),
         ("sketch.line", {"x1": 0, "y1": 40, "x2": 0, "y2": 0}),
         ("sketch.end", {}),
         ("part.extrude", {"depth_mm": 50}),
         ("sheet.sharp_bend_edges", {}),
         ("sheet.convert_to_sheet", {"thickness_mm": 2.0}),
         ("sheet.bends", {}),
         ("sheet.cut_list", {}),
     ]},
]

# ══════════════════════════════════════════════════════════════════════════════
# ASSEMBLY and DRAWING, widened -- on the CURATED surface
# ══════════════════════════════════════════════════════════════════════════════
# Why these exist: the battery closed 2026-09-21 with "REGRESSION = 0" over 69
# scenarios, and MEASURED (2026-09-22) that coverage was 64 part scenarios against 4
# assembly and 1 drawing -- 8 of 32 assembly verbs and 6 of 22 drawing ones. So the
# zero was a strong statement about PART and a weak one about everything else, and the
# weakness was invisible because a scenario that does not exist cannot fail.
#
# Why the CLOUD surface, the same one the older assembly/drawing scenarios use: the
# curated layer is a DIFFERENT DIALECT, not a second door onto the same verbs. Measured
# while writing these: there `comp` is a component OBJECT and not an instance name
# (`asm.fix("cv_tampa-1")` answers `'str' object has no attribute 'Select4'`),
# `part.hole` takes `plane` instead of `face`, `mirror_component` reverses its two
# arguments -- and `asm.mates`, `dwg.view_names` and `dwg.sheet_info`, which the
# assembly and drawing FINGERPRINTS are built from, do not exist there at all.
#
# It would take an adapter in `surface.Curated` for each of those, and the value would
# be running with no server. That trade is worth revisiting; it is not worth taking on
# the same day the coverage doubles.
#
# Where the cloud harness is not in the checkout (the public repository), these
# scenarios -- like every other cloud one -- run on `surface.Local`: the same compiled
# verbs, executed in-process. Until 2026-09-23 they simply did not run there.
#
# STATIC COORDINATES, and they are legitimate: measured on rev 34.3.2 that a view
# created with `at=(x, y)` reports `view_center == at` exactly (delta 0.0), so a pick
# can be written by hand from the part's known size. The drawing smokes read
# `view_center` and do arithmetic on it, which a flat (verb, params) scenario cannot.
# The parts below are sized so that every pick is a round number:
#
#   cv_furado   sketch Top 120 x 40, extrude 25  ->  front view 120 x 25 (x, y)
#                                                    top view   120 x 40 (x, z)
#               hole d10 through, centred at (60, 20) of the 120 x 40 face
#
# /!\ If a future SolidWorks stops putting the view centre on `at`, these scenarios
# fail on the PICK, not on the verb. That is a real version difference and the report
# says which step it was -- but read this note before blaming the verb.
SCENARIOS += [
    # -- the support parts, built on THIS surface -----------------------------
    # They are rebuilt here instead of reused from `asm_pecas_de_apoio` because that
    # one is a cloud scenario: a `--domain asm` run on a machine with no server has to
    # be able to produce its own inputs. `part.block` does not exist here (it is a
    # cloud convenience), so it is sketch + extrude.
    {"name": "cv_pecas_de_apoio", "domain": "asm", "steps": [
        ("part.new_part", {}),
        ("part.block", {"width_mm": 100, "height_mm": 100, "depth_mm": 20}),
        # a NAMED datum: the address `asm.mate_planes` asks for, and the only kind of
        # reference that survives a rebuild without a coordinate in it
        ("part.reference_plane", {"from_plane": "Top", "offset_mm": 10,
                                  "name": "meio_base"}),
        ("part.save", {"path": _file("cv_base.sldprt")}),

        ("part.new_part", {}),
        ("part.block", {"width_mm": 60, "height_mm": 60, "depth_mm": 15}),
        ("part.reference_plane", {"from_plane": "Top", "offset_mm": 7.5,
                                  "name": "meio_tampa"}),
        ("part.save", {"path": _file("cv_tampa.sldprt")}),

        # the drawing's part: a hole is what `center_marks` needs to find, and what
        # gives `gtol position` something to point at
        ("part.new_part", {}),
        ("part.sketch", {"plane": "Top", "shape": "rect",
                         "x1": 0, "y1": 0, "x2": 120, "y2": 40}),
        ("part.extrude", {"depth_mm": 25, "kind": "boss"}),
        # `hole_on` (by FACE) and not `hole` (by PLANE): measured here, `hole` cuts to
        # the wrong side of the plane whichever way `reverse` is set, and every scenario
        # in this suite that makes a hole uses `hole_on`. The two are not spellings of
        # one verb.
        # /!\ on face `top` a position is the pair (x, z) from the MODEL ORIGIN, and a
        # rect sketched on Top grows into NEGATIVE z -- so the centre of this
        # 120 x 40 face is (60, -20), not (60, 20). The verb says which pair it
        # wants; it cannot say which sign your part used.
        ("part.hole", {"face": "top", "diameter_mm": 10,
                          "positions": [[60, -20]]}),
        ("part.set_property", {"name": "Description", "value": "viga furada"}),
        ("part.save", {"path": _file("cv_furado.sldprt")}),
    ]},

    # -- GROUNDING and SUPPRESSION -------------------------------------------
    # `fix`, `float` and `suppress` move no geometry, so a scenario that only looked at
    # positions would pass doing nothing. What catches them is `asm.components`: the
    # signature reads WHICH part is fixed and which has no box.
    {"name": "cv_asm_grounding", "domain": "asm", "steps": [
        ("asm.new_assembly", {}),
        ("asm.add_component", {"path": _file("cv_base.sldprt"), "xyz": [0, 0, 0],
                               "fixed": True}),
        ("asm.add_component", {"path": _file("cv_tampa.sldprt"), "xyz": [0, 40, 0]}),
        ("asm.components", {}),
        ("asm.fix", {"comp": "cv_tampa-1"}),
        ("asm.components", {}),
        ("asm.float_", {"comp": "cv_tampa-1"}),
        ("asm.components", {}),
        ("asm.suppress", {"comp": "cv_tampa-1", "state": True}),
        ("asm.components", {}),
        ("asm.suppress", {"comp": "cv_tampa-1", "state": False}),
        ("asm.components", {}),
        ("asm.component_count", {"top_only": True}),
    ]},

    # -- MOVING and MEASURING -------------------------------------------------
    # `free_translations` NUDGES the component and puts it back, so it is the one
    # reading here that touches the model. With no mate it must answer all three axes;
    # the next scenario is where it has to answer fewer.
    {"name": "cv_asm_mover_medir", "domain": "asm", "steps": [
        ("asm.new_assembly", {}),
        ("asm.add_component", {"path": _file("cv_base.sldprt"), "xyz": [0, 0, 0],
                               "fixed": True}),
        ("asm.add_component", {"path": _file("cv_tampa.sldprt"), "xyz": [0, 40, 0]}),
        ("asm.move_component", {"comp": "cv_tampa-1", "xyz": [30, 60, 20]}),
        ("asm.components", {"comp": "cv_tampa-1", "info": True}),
        ("asm.free_translations", {"comp": "cv_tampa-1"}),
        ("asm.rebuild", {}),
        ("asm.components", {}),
    ]},

    # -- MATE by face, and UNDOING it ----------------------------------------
    # `find_face` -> `mate` is the pair the curated layer offers for "join these two
    # without a coordinate". `free_translations` before and after the mate is what says
    # the mate DID something: three free axes become fewer.
    {"name": "cv_asm_mate_e_apagar", "domain": "asm", "steps": [
        ("asm.new_assembly", {}),
        ("asm.add_component", {"path": _file("cv_base.sldprt"), "xyz": [0, 0, 0],
                               "fixed": True}),
        ("asm.add_component", {"path": _file("cv_tampa.sldprt"), "xyz": [0, 60, 0]}),
        ("asm.find_face", {"comp": "cv_base-1", "kind": "planar", "axis": 1,
                           "want_max": True}),
        ("asm.find_face", {"comp": "cv_tampa-1", "kind": "planar", "axis": 1,
                           "want_max": False}),
        ("asm.mate", {"ref_a": "$3", "ref_b": "$4", "kind": "coincident"}),
        ("asm.mates", {}),
        ("asm.mate_errors", {}),
        ("asm.free_translations", {"comp": "cv_tampa-1"}),
        ("asm.interferences", {}),
        ("asm.components", {}),
        # by NAME, and the name comes from the step that CREATED it ($5) -- a mate's
        # name is not predictable (`Distance1` becomes `LimitDistance1` when it gains a
        # limit), so writing one by hand would be a scenario that rots
        ("asm.delete_mate", {"mate_name": "$5"}),
        ("asm.mates", {}),
        ("asm.components", {}),
    ]},

    # -- MATE by NAMED PLANE -------------------------------------------------
    # The datum->mate bridge, and the field the 2026-09-21 battery reported a false
    # difference on: `_component_refs` was offering the three default planes on one
    # machine and not the other. A scenario that USES a named plane is what turns that
    # field from a reading into a result.
    {"name": "cv_asm_mate_planes", "domain": "asm", "steps": [
        ("asm.new_assembly", {}),
        ("asm.add_component", {"path": _file("cv_base.sldprt"), "xyz": [0, 0, 0],
                               "fixed": True}),
        ("asm.add_component", {"path": _file("cv_tampa.sldprt"), "xyz": [40, 60, 40]}),
        # SAVED before the mate: the qualified reference is `plane@instance@TITLE`, and
        # the verb builds the title at execution time from the TREE name. On an unsaved
        # assembly that name is volatile, which is one hypothesis for the selection
        # failing here -- pinning it costs one step and removes the variable.
        ("asm.save", {"path": _file("cv_datum.sldasm")}),
        ("asm.mate_planes", {"comp_a": "cv_base-1", "name_a": "meio_base",
                             "comp_b": "cv_tampa-1", "name_b": "meio_tampa",
                             "kind": "coincident"}),
        ("asm.mates", {}),
        ("asm.mate_errors", {}),
        ("asm.components", {}),
    ]},

    # -- MIRROR, EXPLODE and OUTPUT ------------------------------------------
    # `explode`/`collapse` change POSITION and nothing else, which is exactly what the
    # assembly signature measures; `mirror_component` creates a NEW instance, which is
    # what the parts list measures.
    {"name": "cv_asm_espelho_explode", "domain": "asm", "steps": [
        ("asm.new_assembly", {}),
        ("asm.add_component", {"path": _file("cv_base.sldprt"), "xyz": [0, 0, 0],
                               "fixed": True}),
        ("asm.add_component", {"path": _file("cv_tampa.sldprt"), "xyz": [30, 40, 0]}),
        ("asm.mirror_component", {"comps": ["cv_tampa-1"], "mirror_plane": "Right"}),
        ("asm.components", {}),
        ("asm.explode", {}),
        ("asm.components", {}),
        ("asm.collapse", {}),
        ("asm.components", {}),
        ("asm.save", {"path": _file("cv_conjunto.sldasm")}),
        ("asm.export", {"path": _file("cv_conjunto.x_t")}),
    ]},

    # -- DRAWING: views, marks and the sheet ---------------------------------
    {"name": "cv_dwg_vistas_marcas", "domain": "dwg", "steps": [
        ("dwg.new_drawing", {"model_path": _file("cv_furado.sldprt"), "size": "A3"}),
        ("dwg.set_units", {"units": "mm"}),
        ("dwg.add_view", {"kind": "front", "at": [150, 150],
                          "model_path": _file("cv_furado.sldprt")}),
        ("dwg.add_view", {"kind": "top", "at": [150, 80],
                          "model_path": _file("cv_furado.sldprt")}),
        ("dwg.list_views", {}),
        # the view centre IS `at` -- measured, see the note above
        ("dwg.view_center", {"view_name": "$2"}),
        ("dwg.center_marks", {"view_name": "$3"}),
        ("dwg.list_views", {}),
        ("dwg.sheet_scale", {"num": 1, "den": 2}),
        ("dwg.list_views", {}),
    ]},

    # -- DRAWING: dimensions -------------------------------------------------
    # The three `orient` of `dimension`, plus the model's own dimensions and the dedupe
    # that follows them. `import_model_dims` needs all_views -- CLAUDE.md.
    {"name": "cv_dwg_cotas", "domain": "dwg", "steps": [
        ("dwg.new_drawing", {"model_path": _file("cv_furado.sldprt"), "size": "A3"}),
        ("dwg.set_units", {"units": "mm"}),
        ("dwg.add_view", {"kind": "top", "at": [150, 120],
                          "model_path": _file("cv_furado.sldprt")}),
        # top view = 120 (x) x 40 (z), centred on (150, 120)
        ("dwg.dimension", {"view_name": "$2", "picks": [[150, 100]],
                           "place_at": [150, 88], "orient": "horizontal"}),
        ("dwg.dimension", {"view_name": "$2", "picks": [[90, 120]],
                           "place_at": [76, 120], "orient": "vertical"}),
        ("dwg.dimension", {"view_name": "$2", "picks": [[150, 100]],
                           "place_at": [150, 76], "orient": "aligned"}),
        ("dwg.import_model_dims", {"all_views": True}),
        ("dwg.dedupe_dimensions", {}),
        ("dwg.list_views", {}),
    ]},

    # -- DRAWING: GD&T and finish --------------------------------------------
    {"name": "cv_dwg_gdt", "domain": "dwg", "steps": [
        ("dwg.new_drawing", {"model_path": _file("cv_furado.sldprt"), "size": "A3"}),
        ("dwg.set_units", {"units": "mm"}),
        ("dwg.add_view", {"kind": "front", "at": [150, 150],
                          "model_path": _file("cv_furado.sldprt")}),
        ("dwg.add_view", {"kind": "top", "at": [150, 80],
                          "model_path": _file("cv_furado.sldprt")}),
        # front view = 120 (x) x 25 (y): bottom edge at y-12.5, left edge at x-60
        ("dwg.datum", {"view_name": "$2", "edge_at": [150, 137.5], "label": "A"}),
        ("dwg.gtol", {"view_name": "$2", "edge_at": [90, 150],
                      "symbol": "perpendicularity", "tolerance": "0.05",
                      "datums": ["A"]}),
        ("dwg.surface_finish", {"view_name": "$2", "edge_at": [150, 162.5],
                                "ra": 3.2, "kind": "machined"}),
        # the hole, on the top view: its edge is 5 mm from the view centre (d10)
        ("dwg.gtol", {"view_name": "$3", "edge_at": [145, 80], "symbol": "position",
                      "tolerance": "0.1", "datums": ["A"], "diameter": True,
                      "mc": "mmc"}),
        ("dwg.annotate", {"text": "TOLERANCIA GERAL ISO 2768-m", "at": [40, 30]}),
        ("dwg.list_views", {}),
    ]},

    # -- DRAWING: title block, section and output ----------------------------
    # The title block reads the MODEL's custom properties through `$PRPSHEET`, which is
    # why `cv_furado` sets a Description when it is built.
    {"name": "cv_dwg_carimbo_corte", "domain": "dwg", "steps": [
        ("dwg.new_drawing", {"model_path": _file("cv_furado.sldprt"), "size": "A3"}),
        ("dwg.set_units", {"units": "mm"}),
        ("dwg.add_view", {"kind": "front", "at": [150, 180],
                          "model_path": _file("cv_furado.sldprt")}),
        # a cut line crossing the whole 120 mm width, at the height of the view
        ("dwg.section_view", {"parent_view": "$2", "p1": [80, 180], "p2": [220, 180],
                              "at": [150, 90], "label": "A"}),
        ("dwg.list_views", {}),
        ("dwg.fill_titleblock", {"drawn_by": "bateria", "checked_by": "versao",
                                 "revision": "A", "company": "MCP-SolidWorks"}),
        ("dwg.list_views", {}),
        ("dwg.save", {"path": _file("cv_furado.slddrw")}),
        ("dwg.export", {"path": _file("cv_furado.pdf")}),
    ]},

    # -- DRAWING of an ASSEMBLY: balloons and BOM ----------------------------
    # /!\ BALLOON BEFORE THE BOM -- CLAUDE.md records this as a gotcha, and a scenario
    # that did it the other way round would be measuring our mistake instead of the
    # SolidWorks version.
    {"name": "cv_dwg_conjunto", "domain": "dwg", "steps": [
        ("dwg.new_drawing", {"model_path": _file("cv_conjunto.sldasm"), "size": "A3"}),
        ("dwg.set_units", {"units": "mm"}),
        ("dwg.add_view", {"kind": "iso", "at": [160, 170], "scale": 0.5,
                          "model_path": _file("cv_conjunto.sldasm")}),
        ("dwg.set_exploded", {"view_name": "$2", "exploded": True}),
        ("dwg.balloons", {"view_name": "$2", "layout": "circle"}),
        ("dwg.table", {"view_name": "$2", "bom_type": "top", "at": [230, 250]}),
        ("dwg.list_views", {}),
        ("dwg.list_views", {}),
    ]},
]

# Os 37 goldens da `suite_verbs` entram como cenarios tambem -- e de la que vem
# a cobertura de esboco (arco, poligono, slot, spline) e dos solidos avancados
# (sweep, loft, combine, draft), which have no hand-written scenario here.
SCENARIOS += goldens.load()

DOMAINS = ["part", "asm", "dwg"]


# ══════════════════════════════════════════════════════════════════════════════
# Normalisation -- what must NOT go into the comparison
# ══════════════════════════════════════════════════════════════════════════════
# A document's title (Part2 on one machine, Part10 on the other) and a handle number
# depend on how much has already run in the session, not on the SolidWorks version.
# Compara-los produziria diferenca em TODO passo e esconderia a diferenca real.
_RE_DOC = re.compile(r"\bPart(\d+)\b|\bAssem(\d+)\b|\bDraw(\d+)\b")
_RE_HANDLE = re.compile(r"@h\d+")
_PRIVATE_PATHS = [(p, m) for p, m in ((ROOT, "<REPO>"), (os.path.expanduser("~"), "<HOME>"))
                  if len(p) > 3]


def normalise(v):
    """Troca o que varia por sessao por um marcador estavel."""
    if isinstance(v, str):
        s = _RE_HANDLE.sub("@h<N>", v)
        s = _RE_DOC.sub(lambda m: re.sub(r"\d+", "<N>", m.group(0)), s)
        s = s.replace(WORK, "<TRAB>").replace(WORK.replace("\\", "/"), "<TRAB>")
        # no other absolute path of this machine may reach a report either (a verb error
        # quoting a file). The repository before the home folder: it may live inside it.
        for path, mark in _PRIVATE_PATHS:
            s = s.replace(path, mark).replace(path.replace("\\", "/"), mark)
        return s
    if isinstance(v, list):
        return [normalise(x) for x in v]
    if isinstance(v, tuple):
        return [normalise(x) for x in v]
    if isinstance(v, dict):
        return {k: normalise(x) for k, x in v.items()}
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    # The CURATED surface returns the raw COM object (the cloud already swaps it for "@hN").
    # Keeping the INTERFACE NAME is the most that can be compared between machines --
    # the object itself does not serialise, and its address means nothing.
    return f"<{type(v).__name__}>"


# ══════════════════════════════════════════════════════════════════════════════
# Assinatura -- a impressao digital comparavel
# ══════════════════════════════════════════════════════════════════════════════
def signature_part(v) -> dict:
    """The PART's fingerprint, invariant to the construction path.

    No feature name and no feature order go in -- what goes in is what the geometry
    says. It is what would have caught
    the PART suite empty pattern: 3 holes leave THREE equal radii, one hole leaves one.
    """
    mass = v("inspect.mass") or {}
    faces = v("inspect.faces", {"info": True}) or []
    edges = v("inspect.edges", {"info": True}) or []
    bodies = v("inspect.bodies") or []

    cx = [f["box_mm"] for f in faces if f.get("box_mm")]
    box = ([round(min(c[i] for c in cx), 2) for i in range(3)]
             + [round(max(c[i + 3] for c in cx), 2) for i in range(3)]) if cx else None
    kinds: dict = {}
    for f in faces:
        t = str(f.get("kind"))
        kinds[t] = kinds.get(t, 0) + 1
    n_bodies = len(bodies) if isinstance(bodies, list) else bodies

    return {
        "volume_mm3": round((mass.get("volume_m3") or 0.0) * 1e9, 2),
        "area_mm2": round((mass.get("area_m2") or 0.0) * 1e6, 2),
        "com_mm": [round(x, 2) for x in (mass.get("com_mm") or [])],
        "caixa_mm": box,
        # /!\ THE BOX DOES NOT COVER A MULTI-BODY PART, and saying so is the whole point
        # of this flag. `inspect.mass` measures every body -- at `gab_solid_combine`
        # step 4 it reported 144000 mm3, the two plates -- while `inspect.faces_info`
        # returned SIX planar faces, one plate's worth. The box is built from those
        # faces, so in a multi-body part it is ONE body's box, and WHICH body is the
        # documented multi-body gotcha (`bodies()[0]` is not the first body created).
        #
        # On 2026-09-21 the two machines picked different plates and the comparator
        # filed "the solid sits 40 mm away in X" as the second real difference between
        # SolidWorks 2017 and 2026. The combine itself was byte-identical on both. A
        # reading that silently covers part of the part is worse than one that fails,
        # because the comparison accepts it -- the same trap as the drawing's
        # `inspect.mass` answering volume 0.0.
        "caixa_parcial": bool(box) and isinstance(n_bodies, int) and n_bodies > 1,
        "corpos": n_bodies,
        "faces": kinds,
        "raios_mm": sorted(round(float(f["radius_mm"]), 2) for f in faces
                           if f.get("kind") == "cylindrical" and f.get("radius_mm")),
        "cylinders": _cylinders(faces),
        "arestas": {"total": len(edges),
                    "retas": sum(1 for e in edges if e.get("straight")),
                    "curvas": sum(1 for e in edges if not e.get("straight"))},
    }


def _cylinders(faces) -> list:
    """Each cylindrical face with what tells a HOLE from a CHANNEL.

    It exists because of a case measured on the SW 2026: `hole_on(face='top',
    positions=[[15,15]])` on a 100x60x15 block returned `Cut-Extrude1` with no error at
    all, and what came out was a semicircular CHANNEL along the 60 mm -- the position
    landed on the face's border and half the circle fell outside the material. Counting
    radii does not catch it: the radius is 4 in both cases. What catches it is the GAP
    PERPENDICULAR to the axis: a real hole opens the whole diameter in BOTH perpendicular
    directions; the channel opened 8 mm in one and 4 mm in the other.
    """
    out = []
    for f in faces:
        if f.get("kind") != "cylindrical" or not f.get("box_mm"):
            continue
        cx = f["box_mm"]
        gaps = [round(cx[i + 3] - cx[i], 2) for i in range(3)]
        axis = [round(v, 3) for v in (f.get("cyl_axis") or [0, 0, 0])]
        # the axis is (nearly) parallel to one of the global axes in the vast majority
        # of cases; when it is not, `i_axis` stays None and only the length loses meaning.
        i_axis = next((i for i, v in enumerate(axis) if abs(abs(v) - 1.0) < 1e-6), None)
        out.append({
            "raio_mm": round(float(f.get("radius_mm") or 0.0), 2),
            "eixo": axis,
            "comprimento_mm": gaps[i_axis] if i_axis is not None else None,
            "vao_perp_mm": sorted(v for i, v in enumerate(gaps) if i != i_axis),
            "area_mm2": round(float(f.get("area_mm2") or 0.0), 2),
        })
    out.sort(key=lambda c: (c["raio_mm"], c["eixo"], c["comprimento_mm"] or 0))
    return out


def signature_asm(v) -> dict:
    """The ASSEMBLY's fingerprint: WHERE each component is, and the mates' health.

    The position goes in because it is exactly what denounced the lid parked 70 mm from
    the base -- `interferences` alone passes when the parts do not even touch.
    """
    info = v("asm.components", {"info": True}) or {}
    comps = info.get("info") if isinstance(info, dict) else info
    out = []
    for c in (comps or []):
        out.append({
            # LOWERCASE, and it is not cosmetics: SW 2017 answers `bv_base.sldprt` and
            # SW 2026 answers `bv_base.SLDPRT` for the same file. Windows does not
            # distinguish them, and the fingerprint must not either -- on 2026-09-21 that
            # single letter produced 28 ASSEMBLY findings, every one of them saying the
            # component "order changed" between two identical assemblies. Noise on this
            # scale does not just annoy: it is where a real difference hides.
            "file": os.path.basename(str(c.get("file") or "")).lower(),
            "fixo": bool(c.get("fixed")),
            "centro_mm": [round(x, 2) for x in (c.get("center_mm") or [])],
            "tamanho_mm": [round(x, 2) for x in (c.get("size_mm") or [])],
            # `str(r)` and not `r`: on SW 2017 a reference comes back as a dict, and
            # sorting dicts raises -- which killed the WHOLE fingerprint of the step,
            # not just this field (measured 2026-09-21). The shape of a ref is not
            # something this suite gets to assume; comparing it as text is enough, and
            # a difference of shape between versions then shows up as a difference
            # instead of as a crash.
            "refs": sorted(str(r) for r in (c.get("refs") or [])),
        })
    out.sort(key=lambda c: (c["file"], c["centro_mm"]))
    return {
        "components": out,
        "n_componentes": len(out),
        "interferencias": len(v("asm.interferences") or []),
        "erros_de_mate": len(v("asm.mate_errors") or []),
        "mates": sorted(str(m.get("type") if isinstance(m, dict) else m)
                        for m in (v("asm.mates") or [])),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Execution
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# Expectation: "the verb executed" != "the verb did what was asked"
# ══════════════════════════════════════════════════════════════════════════════
# The diff between versions is BLIND to a bug present in BOTH -- the two reports match
# and the comparator says "all equal", which is the wrong conclusion. That is why the
# suite needs an oracle of its own.
#
# THE GOLDEN RULE: the expectation comes from the CALL's PARAMETERS and from the DELTA
# between the before and after signatures -- never from a hand-written number. A number
# written by hand is wrong, and wrong in its own favour: that is how `suite_verbs`' `slot`
# case spent years with a rectangle's volume instead of a slot's.
#
# Each function takes (params, before, after) and returns the LIST of failures (empty = ok).
# `before` is None on the first step with geometry.

TOL = 1e-4


def _near(a, b, tol=TOL) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= max(tol * max(abs(a), abs(b)), 0.01)


def _vol(sig):
    return (sig or {}).get("volume_mm3")


def _dims(sig):
    c = (sig or {}).get("caixa_mm")
    return [round(c[i + 3] - c[i], 2) for i in range(3)] if c else None


def _new_cylinders(before, after):
    """The cylinders this step created (the multiset after minus before)."""
    leftover = list((before or {}).get("cylinders") or [])
    new_ones = []
    for c in (after or {}).get("cylinders") or []:
        if c in leftover:
            leftover.remove(c)
        else:
            new_ones.append(c)
    return new_ones


def _exp_block(p, before, after, res=None):
    f = []
    w, h, d = p.get("width_mm"), p.get("height_mm"), p.get("depth_mm")
    target = w * h * d
    if not _near(_vol(after), target):
        f.append(f"volume {_vol(after)} mm3, pedido {w}x{h}x{d} = {target}")
    if _dims(after) and sorted(_dims(after)) != sorted([round(float(x), 2) for x in (w, h, d)]):
        f.append(f"caixa {_dims(after)}, pedido {sorted([w, h, d])}")
    return f


def _exp_primitive(p, before, after, res=None):
    """`part.block`/`part.cylinder` WITHOUT `face` build the solid; WITH `face` they
    are a boss on an existing part, and all that holds then is that material came."""
    if p.get("face"):
        return _exp_add_material(p, before, after, res)
    return (_exp_block if "width_mm" in p else _exp_cylinder)(p, before, after, res)


def _exp_cylinder(p, before, after, res=None):
    import math
    r = (p.get("diameter_mm") or 0) / 2.0
    h = p.get("height_mm") or p.get("depth_mm") or 0
    target = math.pi * r * r * h
    f = []
    if not _near(_vol(after), target, 1e-3):
        f.append(f"volume {_vol(after)} mm3, pedido D{p.get('diameter_mm')} x "
                 f"{h} = {round(target, 2)}")
    return f


def _exp_hole(p, before, after, res=None):
    """Furo: uma face cilindrica NOVA por posicao, no raio pedido, e FURO INTEIRO.

    The `vao_perp` is what separates a hole from a channel -- see `_cylinders`. A hole
    opens the whole diameter in both directions perpendicular to the axis; what opens
    less escaped through the face's border and became a slot, even with the verb saying OK.
    """
    f = []
    n = len(p.get("positions") or [[0, 0]])
    r = (p.get("diameter_mm") or 0) / 2.0
    new_ones = _new_cylinders(before, after)
    if len(new_ones) != n:
        f.append(f"{len(new_ones)} face(s) cilindrica(s) nova(s), pedido {n} furo(s)")
    if before and _vol(after) is not None and _vol(after) >= _vol(before):
        f.append(f"volume nao caiu: {_vol(before)} -> {_vol(after)}")
    for c in new_ones:
        if not _near(c["raio_mm"], r, 1e-2):
            f.append(f"raio {c['raio_mm']} mm, pedido {r}")
        gaps = c.get("vao_perp_mm") or []
        if any(not _near(v, 2 * r, 1e-2) for v in gaps):
            f.append(f"NAO e furo inteiro: vao perpendicular {gaps} mm, "
                     f"um furo D{2 * r} abre {2 * r} nas duas direcoes "
                     f"(virou calha/rasgo na borda da face)")
    return f


def _exp_pattern(p, before, after, res=None):
    """A pattern with count>1 HAS to change the part.

    This is the case that passed 111/111: `FeatureLinearPattern5` creates the feature
    even when no instance fits in the material, and the test only checked `bool(pat)`.
    A volume identical to before = an empty pattern.
    """
    f = []
    n = int(p.get("count") or 0)
    if n > 1 and before and _near(_vol(after), _vol(before), 1e-9):
        f.append(f"a pattern of {n} instances did NOT change the part "
                 f"(volume {_vol(after)} igual ao de antes) -- nenhuma instancia "
                 f"entrou no material; ver `reverse`")
    return f


def _exp_mirror(p, before, after, res=None):
    f = []
    if before and _near(_vol(after), _vol(before), 1e-9):
        f.append(f"the mirror did NOT change the part (volume {_vol(after)} same as before)")
    return f


def _exp_remove_material(p, before, after, res=None):
    f = []
    if before and _vol(after) is not None and _vol(after) >= _vol(before):
        f.append(f"deveria TIRAR material e o volume nao caiu: "
                 f"{_vol(before)} -> {_vol(after)}")
    return f


def _exp_add_material(p, before, after, res=None):
    f = []
    if before and _vol(after) is not None and _vol(after) <= _vol(before):
        f.append(f"deveria POR material e o volume nao subiu: "
                 f"{_vol(before)} -> {_vol(after)}")
    return f


def _exp_fillet(p, before, after, res=None):
    f = _exp_remove_material(p, before, after)
    ca = ((before or {}).get("arestas") or {}).get("curvas", 0)
    cb = ((after or {}).get("arestas") or {}).get("curvas", 0)
    if before and cb <= ca:
        f.append(f"filete nao criou aresta curva: {ca} -> {cb}")
    return f


def _exp_scale(p, before, after, res=None):
    factor = float(p.get("factor") or 1.0)
    f = []
    if before and not _near(_vol(after), _vol(before) * factor ** 3, 1e-3):
        f.append(f"volume {_vol(after)}, esperado {_vol(before)} x {factor}^3 = "
                 f"{round(_vol(before) * factor ** 3, 2)}")
    return f


def _exp_add_comp(p, before, after, res=None):
    f = []
    na = (before or {}).get("n_componentes", 0)
    nb = (after or {}).get("n_componentes", 0)
    if nb != na + 1:
        f.append(f"componentes {na} -> {nb}, esperado +1")
    return f


def _exp_remove_comp(p, before, after, res=None):
    f = []
    na = (before or {}).get("n_componentes", 0)
    nb = (after or {}).get("n_componentes", 0)
    if nb != na - 1:
        f.append(f"componentes {na} -> {nb}, esperado -1")
    return f


def _exp_mate(p, before, after, res=None):
    """A mate has to CREATE a mate and must not leave the assembly ill."""
    f = []
    na = len((before or {}).get("mates") or [])
    nb = len((after or {}).get("mates") or [])
    if nb <= na:
        f.append(f"nenhum mate novo: {na} -> {nb}")
    if (after or {}).get("erros_de_mate"):
        f.append(f"{after['erros_de_mate']} mate(s) with error/over-defined")
    if (after or {}).get("interferencias"):
        f.append(f"{after['interferencias']} material interference(s)")
    return f


def _exp_file(p, before, after, res=None):
    """A verb that WRITES a file: it has to exist and to have content.

    A 0-byte PDF is the classic "executed and did nothing" -- the verb returns the path,
    the path exists, and the file is empty.
    """
    path = p.get("path")
    if not path:
        return []
    if not os.path.exists(path):
        return [f"the verb returned success and the file does not exist: {path}"]
    if os.path.getsize(path) == 0:
        return [f"file written with 0 bytes: {path}"]
    return []


def _exp_extrude(p, before, after, res=None):
    """The depth asked for has to appear in the box -- as a MEASUREMENT when the
    extrusion is the first solid, and as the GROWTH when it is stacked on one.

    There is no way to know WHICH axis without reimplementing the server's plane
    convention -- and reimplementing the convention to check the convention proves
    nothing. Which of the three it is does not matter; that the number is there does.

    The distinction between measurement and growth is not a detail: a boss on an
    existing body makes the box the SUM. The stepped shaft of `gab_compound_eixo_
    escalonado` (cylinder h=20, +30, +25) gives boxes of 50 and then 75 -- both correct,
    both reported as a defect by the old rule, which looked for a literal 30 among
    [40, 40, 50]. Two of the six `BUG_ANTIGO` of 2026-09-21 were this check, on the two
    versions at once, which is exactly how a wrong oracle disguises itself as a finding.
    """
    d = p.get("depth_mm")
    dims_after = _dims(after)
    if not d or not dims_after or p.get("through_all"):
        return []
    d = float(d)
    dims_before = _dims(before)
    if not dims_before:
        # no solid before: the box IS the extrusion
        if not any(_near(x, d, 1e-3) for x in dims_after):
            return [f"profundidade {d} mm nao aparece na caixa {dims_after}"]
        return []
    if p.get("kind") == "cut":
        return []          # a cut does not have to change the box at all
    growth = [b - a for a, b in zip(dims_before, dims_after)]
    if (not any(_near(x, d, 1e-3) for x in dims_after)
            and not any(_near(g, d, 1e-3) for g in growth)):
        return [f"profundidade {d} mm nao aparece na caixa {dims_after} nem no "
                f"crescimento {[round(g, 2) for g in growth]} (antes {dims_before})"]
    return []


def _exp_datum(p, before, after, res=None):
    """A NAMED datum: the name asked for has to be the name that came back."""
    name = p.get("name")
    if name and res is not None and str(res) != str(name):
        return [f"pediu o datum '{name}' e voltou '{res}'"]
    return []


def _exp_material(p, before, after, res=None):
    """The SW IGNORES an unknown material name -- the verb itself warns that an empty
    'applied' means the name does not exist in the database. Without this check, a wrong
    material passes as a success.

    It used to read `res["aplicado"]`, and the verb returns `applied`: the translation
    renamed the key on one side only, so the read was ALWAYS None and the check ALWAYS
    fired. It accused `set_material` on both SolidWorks versions at once -- and a probe
    of 10 combinations of database x name found the material applied every single time.
    Two of the six `BUG_ANTIGO` of 2026-09-21 were this line.

    `applied` comes as (name, database) and not as a bare string, because
    GetMaterialPropertyName2 puts the database in a byref out-param.
    """
    if not isinstance(res, dict):
        return []
    applied = res.get("applied")
    name = applied[0] if isinstance(applied, (list, tuple)) and applied else applied
    if not name:
        return [f"material '{p.get('name')}' was NOT applied "
                f"(the name does not exist in the SolidWorks database)"]
    # stronger than the old check: SW applying ANOTHER material is also a defect
    sent = res.get("enviado") or p.get("name")
    if sent and str(name).strip() != str(sent).strip():
        return [f"pediu '{sent}' e o SolidWorks aplicou '{name}'"]
    return []


def _exp_base_flange(p, before, after, res=None):
    """Base flange: the volume is w x h x thickness, straight from the parameters."""
    target = (p.get("w_mm") or 0) * (p.get("h_mm") or 0) * (p.get("thickness_mm") or 2.0)
    if not _near(_vol(after), target, 1e-3):
        return [f"volume {_vol(after)} mm3, pedido {p.get('w_mm')}x{p.get('h_mm')}x"
                f"{p.get('thickness_mm')} = {round(target, 2)}"]
    return []


def _exp_flatten(p, before, after, res=None):
    """Planificar muda a CAIXA e preserva (aproximadamente) o VOLUME.

    Flattening is a change of STATE, not of material: the unfolded sheet occupies
    another bounding box but is still the same amount of steel. An equal box = it did
    not flatten, and the verb returned success all the same.
    """
    if before is None:
        return []
    f = []
    ca, cb = _dims(before), _dims(after)
    on = p.get("on", True)
    # the box changes on BOTH sides: flattening opens the sheet, folding closes it
    # again. (The first version of this only demanded the change when on=True and, for
    # on=False, accused precisely when the box DID change -- an inverted condition,
    # caught on the first sheet metal run. A wrong oracle is worse than none.)
    if ca and cb and ca == cb:
        f.append(f"flatten(on={on}) nao mudou a caixa: {cb}")
    if not _near(_vol(after), _vol(before), 5e-2):
        f.append(f"planificar mudou o VOLUME: {_vol(before)} -> {_vol(after)} "
                 "(devia so mudar de estado)")
    return f


EXPECT = {
    "sheet.new_sheet": _exp_base_flange,
    "sheet.box_flanges": _exp_add_material,
    "sheet.edge_flange": _exp_add_material,
    "sheet.flatten": _exp_flatten,
    "sheet.export_flat": _exp_file,
    "sheet.cut": _exp_remove_material,
    # the four below are the direction `smoke_sheetmetal.py` MEASURED, not a guess
    "sheet.hem": _exp_add_material,
    "sheet.miter_flange": _exp_add_material,
    "sheet.gusset": _exp_add_material,
    "sheet.closed_corner": _exp_add_material,
    "sheet.corner_trim": _exp_remove_material,
    "part.save": _exp_file,
    "part.export": _exp_file,
    "dwg.save": _exp_file,
    "dwg.export": _exp_file,
    "asm.save": _exp_file,
    "asm.export": _exp_file,
    "part.extrude": _exp_extrude,
    "part.reference_plane": _exp_datum,
    "part.reference_axis": _exp_datum,
    "part.reference_point": _exp_datum,
    "part.axis_from_face": _exp_datum,
    "part.set_material": _exp_material,
    "part.block": _exp_primitive,
    "part.cylinder": _exp_primitive,
    "part.hole": _exp_hole,
    "part.pattern_linear": _exp_pattern,
    "part.pattern_circular": _exp_pattern,
    "part.pattern_mirror": _exp_mirror,
    "part.fillet": _exp_fillet,
    "part.chamfer": _exp_remove_material,
    "part.shell": _exp_remove_material,
    "part.countersink": _exp_remove_material,
    "part.counterbore": _exp_remove_material,
    "part.scale": _exp_scale,
    "asm.add_component": _exp_add_comp,
    "asm.remove_component": _exp_remove_comp,
    "asm.mate": _exp_mate,
    "asm.mate_faces": _exp_mate,
    "asm.mate_planes": _exp_mate,
}


def check_expect(verb, params, before, after, res=None) -> list:
    fn = EXPECT.get(verb)
    if not fn:
        return []
    try:
        return fn(params, before, after, res)
    except Exception as exc:  # noqa: BLE001
        # a check that breaks becomes a FINDING, never silence: a dead check would go
        # green and that is exactly the failure this suite exists not to repeat
        return [f"a propria checagem quebrou: {type(exc).__name__}: {exc}"]


_RE_REF = re.compile(r"^\$(\d+)(?:\[(\d+)\])?$")


def _resolve(params, results):
    """Replaces a reference to an earlier step with what that step returned.

    Two spellings, because both already exist: `{"$passo": N}` (from here) and `"$N"`
    (the convention of `suite_verbs`' goldens, harvested by `goldens.py`).

    `"$N[j]"` is part of that second spelling and is not optional: a verb may return
    SEVERAL objects (a `rect` returns its 4 segments) and the next step wants one of
    them. Without the index the string travelled whole into the bundle and the bridge
    answered "unknown reference in argument: $2[0]" -- which cost the two `sketch_dimension`
    goldens on 2026-09-06.
    """
    if isinstance(params, str):
        m = _RE_REF.match(params)
        if not m:
            return params
        value = results[int(m.group(1))]
        return value[int(m.group(2))] if m.group(2) is not None else value
    if isinstance(params, dict):
        if "$passo" in params and len(params) == 1:
            return results[int(params["$passo"])]
        return {k: _resolve(x, results) for k, x in params.items()}
    if isinstance(params, list):
        return [_resolve(x, results) for x in params]
    return params


def _files_produced(steps):
    """The size of each file the scenario wrote -- .x_t/.step/.pdf are output too, and
    a zero here is a defect even if the verb said OK."""
    out = {}
    for _, params in steps:
        path = params.get("path")
        if isinstance(path, str) and os.path.isabs(path):
            name = os.path.basename(path)
            out[name] = os.path.getsize(path) if os.path.exists(path) else None
    return out


def run_scenario(cen: dict, v) -> dict:
    print(f"\n--- {cen['name']} ({cen['domain']}) ---")
    steps_out, results = [], []
    stopped_at = None
    # two separate timelines: a part and an assembly never share a signature, and the
    # delta only makes sense against the previous step of the SAME kind
    sig_part = sig_asm = None
    for i, (verb, params) in enumerate(cen["steps"]):
        reg = {"i": i, "verb": verb}
        try:
            res = v(verb, _resolve(params, results))
            results.append(res)
            reg["ok"] = True
            reg["resultado"] = normalise(res)
        except Exception as exc:  # noqa: BLE001
            results.append(None)
            reg["ok"] = False
            reg["error"] = normalise(str(exc))[:400]
            print(f"  [ERRO] {i:>2} {verb}  -- {str(exc)[:90]}")
            steps_out.append(reg)
            # A step that errors invalidates the following steps' state: carrying on
            # would produce cascading errors the other version would have no way to match.
            stopped_at = i
            break

        # the fingerprint AFTER the step -- it is what locates WHERE it diverged
        before = None
        try:
            if verb.startswith("asm."):
                before, sig_asm = sig_asm, normalise(signature_asm(v))
                reg["signature"] = sig_asm
            # `sheet.` touches the PART like any part verb -- without this line the 47
            # sheet metal verbs passed with no signature at all, that is, with no
            # verification, which is the trap this suite exists to close
            elif verb.startswith(("part.", "sheet.")):
                before, sig_part = sig_part, normalise(signature_part(v))
                reg["signature"] = sig_part
        except Exception as exc:  # noqa: BLE001
            reg["assinatura_falhou"] = normalise(str(exc))[:200]

        # ... and the question "no error" does not answer: did it DO what was asked?
        #
        # With no fingerprint there is nothing to check against, and the checks read
        # `(after or {}).get(..., 0)` -- which does not abstain, it INVENTS: on the SW
        # 2017 run a dead fingerprint became "componentes 0 -> 0, esperado +1" on nine
        # assembly steps that had worked, and the comparator promoted all nine to FIX.
        # A check that cannot run has to say so; a fabricated verdict is worse than none.
        if reg.get("assinatura_falhou"):
            reg["nao_verificado"] = "a assinatura do passo falhou -- checagem nao rodou"
            failures = []
        else:
            failures = check_expect(verb, params, before, reg.get("signature"), res)
        if failures:
            reg["expect_failed"] = normalise(failures)
            print(f"  [NAO FEZ] {i:>2} {verb}")
            for msg in failures:
                print(f"           {msg}")
        else:
            mark = "OK " if verb not in EXPECT else "FEZ"
            print(f"  [{mark}] {i:>2} {verb}")
        steps_out.append(reg)

    return {"name": cen["name"], "domain": cen["domain"],
            "steps": steps_out, "parou_em": stopped_at,
            "arquivos": _files_produced(cen["steps"])}


def _hygiene(needs) -> str:
    """Closes everything before each scenario. Returns the SolidWorks revision.

    Through the RIGHT surface: the cloud harness' `clean_sw` runs on a COM thread of
    its own (the bridge's). On a curated-only run that would open a second apartment
    for nothing -- and, worse, would require the cloud stack in a suite that exists
    precisely so as not to need it.
    """
    from solidworks import sw_com as C
    rev = C.connect().RevisionNumber()
    T.begin()        # the first call records the user's documents; later ones do nothing
    T.close_all()    # development: CloseAllDocuments(True); isolated: only ours
    return str(rev)


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", action="append", choices=DOMAINS,
                    help="runs only this domain (repeatable)")
    ap.add_argument("--scenario", action="append", help="runs only this scenario (repeatable)")
    ap.add_argument("--out", default="", help="report path (default: automatic)")
    ap.add_argument("--surface", choices=("curated",), default="curated",
                    help="kept for old command lines: the curated layer is the only one")
    ap.add_argument("--local", action="store_true",
                    help="kept for old command lines; changes nothing (every scenario "
                         "runs on the curated layer, in this process)")
    ap.add_argument("--baseline", action="store_true",
                    help="write the report as `reports/BASELINE_sw<rev>.json`, which is "
                         "the ONE report `.gitignore` commits -- and WITHOUT the machine "
                         "name, because a versioned reference must not carry a hostname")
    ap.add_argument("--expected-revision", default="",
                    help="refuse to run (exit 2) unless the SOLIDWORKS answering over COM "
                         "has this RevisionNumber prefix: '25' = SW 2017, '34' = SW 2026")
    a = ap.parse_args(argv)

    # A subset of the scenarios is validation of a change, never the reference: refuse
    # before touching SOLIDWORKS, so a partial run cannot overwrite a BASELINE file.
    filtered = bool(a.domain or a.scenario)
    if a.baseline and filtered:
        print("ERROR: --baseline needs the FULL suite; --domain/--scenario make a partial "
              "run, which must never be written as a baseline.")
        return 2
    unknown = sorted(set(a.scenario or ()) - {c["name"] for c in SCENARIOS})
    if unknown:
        # a typo would otherwise drop that scenario silently while the others PASS
        print(f"ERROR: unknown scenario(s): {', '.join(unknown)}")
        return 2

    os.makedirs(OUT, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)

    chosen = [c for c in SCENARIOS
                  if (not a.domain or c["domain"] in a.domain)
                  and (not a.scenario or c["name"] in a.scenario)]
    if not chosen:
        print("nenhum cenario casou com o filtro.")
        return 2

    def surface_of(cen):
        return "curated"

    needs = {surface_of(c) for c in chosen}

    try:
        rev = _hygiene(needs)
    except Exception as exc:  # noqa: BLE001 -- could not run is exit 2, not a traceback
        print(f"SOLIDWORKS did not answer over COM: {type(exc).__name__}: {exc}")
        return 2
    if a.expected_revision:
        print(f"Expected SolidWorks revision: {a.expected_revision}")
        print(f"Observed SolidWorks revision: {rev}")
        if not G.revision_matches(a.expected_revision, rev):
            print("ERROR: wrong SolidWorks version is active.")
            return 2
    print(f"SolidWorks rev {rev} -- {len(chosen)} cenarios "
          f"-- surface(s): {', '.join(sorted(needs))}")
    if filtered:
        print(f"FILTERED RUN: {len(chosen)} of {len(SCENARIOS)} scenarios "
              f"({', '.join(c['name'] for c in chosen)}) -- a PARTIAL report, not a "
              f"compatibility baseline")

    # opens only the surfaces the chosen scenarios really ask for: running sheet metal
    # alone must not require the cloud server to be up
    #
    # When BOTH are open, the curated one has to borrow the bridge's COM thread, or it
    # loses every scenario to "marshalled for a different thread" (see `surface.Curated`).
    # Measured on 2026-09-06: the two single-surface runs came out clean and both mixed
    # runs lost all four sheet-metal scenarios to exactly this.
    surfaces = {n: surface.open_(n) for n in needs}
    health = {}

    t0 = time.time()
    report = {
        "meta": {
            "sw_rev": rev,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # /!\ THE HOSTNAME, and `.gitignore` VERSIONS `BASELINE_*.json` on
            # purpose -- so promoting a report to a baseline carries this machine
            # name into a public repository. `--baseline` writes the file without
            # it: which machine ran is a fact for the session, never for the
            # reference, and the SolidWorks revision already says the role.
            **({} if a.baseline else {"machine": platform.node()}),
            "git_commit": _git_commit(),
            "server_verbs": health.get("verbs"),
            "surfaces": sorted(needs),
            "python": platform.python_version(),
            **({"filter": {"domain": a.domain or [], "scenario": a.scenario or [],
                           "scenarios_run": len(chosen), "scenarios_total": len(SCENARIOS)}}
               if filtered else {}),
        },
        "scenarios": [],
    }
    for cen in chosen:
        # each scenario starts from scratch: a document left open by an earlier
        # scenario jams the next one's SaveAs (measured 2026-08-19)
        _hygiene(needs)
        sup_name = surface_of(cen)
        reg = run_scenario(cen, surfaces[sup_name])
        reg["surface"] = sup_name
        report["scenarios"].append(reg)
    report["meta"]["seconds"] = round(time.time() - t0, 1)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # a BASELINE has no timestamp in its name: it is the reference, and a reference
    # that changes name every run is one nobody can cite.
    destino = a.out or os.path.join(
        OUT, f"BASELINE_sw{rev.replace('.', '_')}.json" if a.baseline
        else f"{'partial_' if filtered else ''}sw{rev.replace('.', '_')}_{stamp}.json")
    with open(destino, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, sort_keys=True)

    all_ = [p for c in report["scenarios"] for p in c["steps"]]
    errors = [p for p in all_ if not p.get("ok")]
    did_not = [(c["name"], p) for c in report["scenarios"] for p in c["steps"]
               if p.get("expect_failed")]
    print(f"\n{'=' * 66}")
    print(f"{len(all_)} steps, {len(errors)} with an ERROR, {len(did_not)} that "
          f"executed but did NOT DO what was asked  ({report['meta']['seconds']}s)")
    if did_not:
        # This block is what the 111/111 scoreboard was missing: a verb that returns
        # success and delivers something else. It does NOT depend on the other version
        # to accuse -- it is the suite's own oracle, and the only one that sees a bug
        # present in BOTH versions.
        for name, p in did_not:
            print(f"  {name}  passo {p['i']} ({p['verb']})")
            for msg in p["expect_failed"]:
                print(f"      {msg}")
    print(f"\nrelatorio: {destino}")
    print("\nCompare com a outra versao:")
    print(f"  .venv\\Scripts\\python.exe tests\\version\\compare_version.py "
          f"<relatorio_sw2017.json> {os.path.basename(destino)}")
    return _verdict(report, chosen)


def _verdict(report, chosen) -> int:
    """The exit code of a completed run: every step each chosen scenario DECLARES is
    judged, so a scenario that stopped early leaves its remaining steps NOT RUN."""
    v = G.suite_verdict(report, {c["name"]: (len(c["steps"]), c.get("known", {}))
                                 for c in chosen})
    print(f"\nVERDICT: {G.format_counts(v['counts'])}")
    for name, i, verb, res, detail in v["findings"]:
        print(f"  {res:<7} {name} step {i} ({verb or '-'})  {str(detail)[:120]}")
    print("VERDICT: " + ("PASS" if v["exit_code"] == 0 else "FAIL"))
    return v["exit_code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
