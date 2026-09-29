# Sheet metal

`solidworks/sw_sheetmetal.py`: 47 verbs that take a flat sheet all the way to a cutting
DXF. Sheet metal started out on the "long tail" of the plan, as ~20 methods assumed to be
escape hatch. Mapping it live showed that half of it worked. Five more rounds of
measurement brought **every modelling feature** into the verbs. The rounds are why this
domain has its own page: it is where the API most often **reports success without
building anything**, and where a negative result was most often the wrong path rather
than a real limit.

## End to end

```python
from solidworks import sw_com as C, sw_parts as P, sw_sheetmetal as SM

C.connect(); C.close_untitled()
SM.new_sheet("Front", 100, 60, thickness_mm=2)   # base sheet
SM.box_flanges(20)                               # 4 flanges -> a box, 100 x 60 x 22
P.save(r"C:\...\box.sldprt")                    # export_flat requires a SAVED part
SM.flatten(True)                                 # 136.6 x 96.6 x 2.0 mm, volume preserved
SM.export_flat(r"C:\...\box.dxf")               # the DXF for the laser / punch
print(SM.cut_list())                             # resolved fabrication properties
```

And the path for someone who already has the part modelled as an ordinary solid and only
wants the DXF:

```python
SM.convert_to_sheet(2.0)   # an L/U-profile solid becomes a sheet-metal part, geometry kept
SM.flatten(True)           # L of 60 x 40 x 50 -> 96.3 x 2.0 x 50.0 mm
```

## The verbs

- **Construction:** `new_sheet`, `base_flange`, `edge_flange` (90°, 45°, partial with
  `margin_mm`), `box_flanges`, `hem` (5 kinds), `break_corner`, `cross_break`, `rip`,
  `sketched_bend` (a bend along a line, with no edge needed), `miter_flange` (on
  axis-parallel **and oblique** edges), `jog` (two bends in one step), `cut`,
  `lofted_bend` (a transition sheet between two open profiles: a funnel or an adapter),
  `convert_to_sheet`, `closed_corner`, `corner_trim`, `gusset` (a stiffening rib across
  a bend).
- **Selection (the eyes):** `base_face`, `free_edges`, `bends`, `bend_info` (angle,
  radius, direction, order and K-factor per bend), `bend_faces`, `sharp_bend_edges`,
  `open_corners`, `bbox_mm` (all bodies), `sheet_bodies`, `is_sheet`, `is_flat`,
  `last_sketch`, `last_feature`.
- **Flattening:** `flatten(True|False)`, `unfold(bends, fixed_face)` / `fold(...)`,
  `flat_options(fixed_face=, merge=, simplify=)` (what ends up in the DXF).
- **Fabrication:** `export_flat` (DXF/DWG), `cut_list` (one body), `cut_lists` (one per
  body), `gauge_table`, `gauge_radii`.
- **Parametric:** `thickness`, `set_thickness`, `bend_params`, `set_bend_radius`,
  `set_flange(angle_deg=, radius_mm=, k_factor=)`, `set_k_factor`, `set_gauge`.
- **Drawing** (in `sw_drawing`): `flat_pattern_view`, `bend_table`, `bend_lines`.
- **Assembly:** no new verb. A sheet-metal part goes through `sw_assembly` like any
  other part, and the smoke test mates one, checks interference against its flange, and
  flattens it inside the assembly.

## Reference numbers

Measured live, so a regression can be checked without opening SolidWorks:

| Case | Result |
|---|---|
| box 100 × 60 × 2 + 4 flanges of 20 | folded 100 × 60 × 22; flat 136.6 × 96.6 × 2.0; volume 2.2970e-05 m³ preserved |
| sheet 100 × 60 × 2 + one 25 mm flange | flat 100 × **83.28** mm at radius 3 → **81.14** mm at radius 8 |
| same, K-factor | K = 0.1 → **82.0265**; K = 0.5 → **83.2832**; K = 0.9 → **84.5398** mm |
| sheet + `sketched_bend` 90° at x = 60 | box 65 × 60 × 38.7; volume 1.2000e-05 preserved |
| sheet + `miter_flange(20)` | box 100 × 60 × **20.0** (the flange is as long as its profile) |
| `lofted_bend`, two profiles, 2 mm | volume **2.1602e-05** m³; at 3 mm **3.2883e-05** |
| sample gauge table | Gauge 3 → **6.0731** mm; Gauge 5 → **5.3137** mm |

## What the API does, measured

Every claim below was judged by **geometry** (volume, face count, bounding box), never
by a return value.

**The newest version is often the one that does not work.** Elsewhere the rule is "use
the latest version the licence accepts". In sheet metal it inverts:

- `InsertSheetMetalBaseFlange2` (19 args) is a silent no-op. The v1 (16 args) builds.
- `CustomPropertyManager.Get5` returns an empty resolved value. `Get2` resolves it.
- `SetBendState(Flattened)` does not flatten and returns "no error". Flattening means
  unsuppressing the `Flat-Pattern` feature.

The inversion is not a law, though. For the gusset and the lofted bend, the old versions
failed exactly like the new ones.

**`ModifyDefinition` lies, by interface.** It returns `True` and changes nothing on
`ISheetMetalFeatureData` and `IOneBendFeatureData`. The global bend radius only takes
effect after `SetOverrideDefaultParameter(1)`. The K-factor does not move globally in any
of 8 combinations; it changes **per flange**. Always check the geometry afterwards.

**On a sheet-metal part, the last feature in the tree is always `Flat-Pattern1`.** The
usual way to find "the feature I just created", `FeatureByPositionReverse(0)`, therefore
returns the wrong thing. This broke `sw_sketch.end()`, `sw_parts.reference_plane` and 11
other places in the part verbs, which now use a helper that skips it.

**What decides is the path and the selection, not the argument.** Items were marked
impossible and then fell:

| Feature | What was wrong | Variants tried before |
|---|---|---|
| sketched bend | `CreateDefinition` returns `None`; the direct `InsertSheetMetal3dBend` builds | — |
| miter flange | the profile has to **touch the end** of the edge; 2 mm away is a silent refusal | — |
| lofted bend | profiles must be selected as **sketch segments with mark 1**; as a "SKETCH" it returns `None` | — |
| gauge table | the selector is `ThicknessTableName`, a **name** such as `"Gauge 3"`, not a thickness | — |
| convert to sheet metal | the **v2** method × selecting the bend **edge** had never been tried together | 15 |
| closed corner | the UI selects **one side face** of a flange, not the two large walls | 11 |
| jog | the angle goes in **degrees**, unlike the whole rest of the API | 18 |
| corner trim | `InternalCornerFlag=0`, **several** edges at once, and only on the flattened part | 12 |
| perpendicular plane | a **different mark per reference** (edge 0, vertex 1). This unlocked miter flanges on oblique edges | 11 |
| gusset | the right **faces** (the two that form the bend's corner) and `BDraft=False`; the v1 was always enough | 19 |

Two lessons from that table:

1. **A sweep can be complete on every axis and still useless if the axes are never
   crossed.** The convert-to-sheet sweep varied the method version and the edge
   selection, but each in different variants. The combination that works was never
   tried until a recording showed it.
2. **When the sweep is exhausted, record the operation in the UI.** SolidWorks' macro
   recorder shows which method the UI actually calls and what it selects.
   The recorder is **blind to the gusset**: it
   records the selections and no call. In that case, read the feature the UI created,
   and read it through the **right** interface. Through the wrong one
   (`IGussetFeatureData` is the weldment gusset; sheet metal's is
   `ISMGussetFeatureData`), the values come back scrambled without raising.

**Other measured facts worth knowing:**

- The edge-flange profile must lie on the side **opposite** the material.
- The base face is the one with the largest **area**, not the highest Z. On an L-shaped
  solid that is the **outer** face, which `convert_to_sheet` refuses.
- Bends are **sub-features**: `EdgeBend1` lives inside `Edge-Flange1`.
- `ExportFlatPatternView` on an unsaved part fails **inconsistently**, so `export_flat`
  refuses to run on one.
- Sheet thickness belongs to the **document**, not the body: a second base flange at
  3 mm thickens the first one, which was at 2 mm. On multi-body parts `cut_list()`
  refuses to answer, and `cut_lists()` gives one list per body.
- `SM.thickness()` already returns millimetres. Multiplying by 1000 sends a 2-metre sheet
  to COM.

## SOLIDWORKS 2017 vs. 2026

The sheet-metal verbs were mapped on SOLIDWORKS 2017. The first run of the smoke test on
SOLIDWORKS 2026 (rev 34.3.2) found three differences, all in the **fabrication side**:

| What | 2017 | 2026 | How the code handles it |
|---|---|---|---|
| `IView.InsertBendTable` with an empty template | uses the default | raises "The server threw an exception" | `dwg.bend_table` passes the installed `bendtable-standard.sldbndtbt` explicitly |
| sample gauge tables | `.xls` | `.xlsx` | `gauge_table(path)` takes either |
| material in the cut list | `Material` | `MATERIAL` | `cut_list()` passes SolidWorks' names through: read both |

**One limit, on both versions:** `bend_faces()` (and so `gusset()`) only accepts a
**perpendicular** corner, because a gusset rests on the two faces of a 90-degree bend.
On a 45, 60 or 120-degree flange it refuses with a clear error instead of handing the
gusset the wrong faces.

## What is left

Two items, both on the **drawing** side. Neither stops anyone from modelling or
fabricating:

- **bend table** (`InsertBendTableOpen`) opens a modal dialog and never returns. The
  gauge table covers the use case.
- **cut-list property note** (`IView.InsertCutListPropertyNote`) raises
  "The server threw an exception".
