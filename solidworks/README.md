# The curated layer

`solidworks/` is an importable Python package: **181 semantic verbs** over SOLIDWORKS COM,
with the gotchas resolved inside. Scripts and tests import it directly. The MCP server
exposes the same verbs by name.

| Module | Role |
|---|---|
| `sw_com` | layer 0: connect/launch, early binding (`cast`), `NULL`, `mm`/`deg`, `active`, `resolve_plane` / `select_plane`, `close_untitled`, `restart` |
| `sw_parts` | PART verbs |
| `sw_sketch` | low-level sketching: shapes, relations, parametric dimensions |
| `sw_inspect` | inspection and validation helpers; some helpers affect selection or view state |
| `sw_assembly` | ASSEMBLY verbs |
| `sw_drawing` | TECHNICAL DRAWING verbs, to standard |
| `sw_sheetmetal` | SHEET METAL verbs: base sheet, flanges, flattening, cutting DXF |
| `catalog` | the index that exposes every public function of the modules above as a verb by name |
| `standalone_core` | a separate parametric core that imports nothing from the MCP or the modules above (global variables, equations, rebuild, STEP export), for driving a finished model from an optimization loop |

```python
from solidworks import sw_com as C, sw_parts as P, sw_inspect as I

C.connect()          # attach to a running SolidWorks; launch one if there is none
C.close_untitled()   # hygiene: start clean (see gotchas)
P.new_part()
P.sketch("Front", "rect", x1=0, y1=0, x2=50, y2=30)
P.extrude(20, "boss")
print(I.mass())
P.save(r"C:\...\block.sldprt")
```

Plane names: `"Front" / "Top" / "Right"` are resolved to the **real** name of the plane
in the feature tree, so they work on templates in any language (`"Plano frontal"` on a
Portuguese one).

## Part (`sw_parts`, `sw_sketch`, `sw_inspect`)

- **Document:** `new_part`, `open_part`, `save`, `export` (format by extension: `.step`,
  `.x_t`, `.stl`, `.iges`).
- **Sketch:** `sketch(plane, "rect"|"circle", ...)` is the one-call shortcut. For
  everything else, `sw_sketch`: `begin(plane)` / `begin_on_face(face)` → `line`,
  `centerline`, `circle`, `arc_tangent`, `point`, `polygon`, `slot`, `spline`, `rect` →
  `end()`. `relation(entities, kind)` (tangent, parallel, concentric, perpendicular,
  coincident, ...) and `dimension(entities, at, orient=, name=)`: a **parametric
  dimension with a stable name**, which an optimization loop can then drive with
  `set_dimension`.
- **Solid:** `extrude(depth, "boss"|"cut", through_all=, merge=, thin_mm=)`, `revolve`,
  `sweep(profile, path)`, `loft(profiles)`, `fillet`, `chamfer`.
- **Holes (geometric):** `hole`, `counterbore`, `countersink`.
- **Patterns and datums:** `pattern_linear`, `pattern_circular`, `pattern_mirror`;
  `reference_plane`, `reference_axis`, `reference_point`, `reference_csys` and
  `axis_from_face(face, name)`, a **named** axis from a cylindrical face. Named datums
  are what make assembly mates robust.
- **Body and wall:** `shell`, `scale`, `combine("add"|"subtract"|"common")`,
  `delete_body`, `draft`.
- **Parametric:** `get_dimension` / `set_dimension` / `set_dimensions` (batch),
  `add_equation` / `equations`, `set_tolerance(dim, "fit"|"bilateral"|"symmetric"|
  "basic")`, `set_property` / `get_property` (custom properties, which feed the title
  block), `set_material` / `get_material`.
- **Configurations** (part or assembly): `configurations()`, `add_configuration(name,
  activate=)`, `activate_configuration`, `rename_configuration`, `delete_configuration`,
  and `set_dimension(name, mm, config=)` / `get_dimension(name, config=)` for a value in
  ONE configuration (`config="all"` for every one). Measured: the configuration names go
  to SolidWorks as an explicit string array (a plain list is silently ignored), and
  "all configurations" in one call zeroed the inactive ones, so the verb sets them one
  by one.
- **Inspection:** `list_features`, `bodies`, `faces`, `edges`,
  `find_face(kind, axis, want_max, radius_mm)`, `find_edge`, `select`, `mass`,
  `screenshot`.
- **Closing the loop** (any document type, parts, assemblies and drawings):
  - `measure(entities, kind)` measures 1 or 2 faces / edges / vertices / components:
    `distance`, `minimum_distance`, `maximum_distance`, `angle`, `radius`, `diameter`,
    `length`, `area`, or `all`. It answers in mm and degrees, and raises when the
    quantity does not apply to what was picked. To a hole, `distance` is from its
    centre (hole R5 at 25 mm from a wall: 25 / 20 / 30 for centre / min / max).
  - `validate(force, interferences)` rebuilds, then reports SolidWorks' "What's Wrong"
    list with the enum names (`swFeatureErrorCutNotIntersectModel`), plus per type:
    sketch states and body count (part); mate errors, interferences, component states
    and missing files (assembly); each view's model (drawing). `valid` is false on any
    error; warnings (an over-defined sketch) do not flip it.
  - Why both: the rebuild's return is not evidence. An over-defined sketch rebuilds
    "fine", and a fillet or chamfer too big for a part made thinner is silently
    adapted (a 10 mm chamfer on a 4 mm plate rebuilt with no error and cut the volume
    to a third). Validate the document, then measure the geometry.
- **Reading a model you did not build:**
  - `model_summary()`: one call to understand the active document. Part: configurations,
    material, features, dimensions (by the name `set_dimension` takes), sketch states,
    mass, bounding box. Assembly: component counts (total, top level, unique files,
    subassemblies, fixed), mates by type and their errors. Drawing: sheets, views,
    annotation counts (dimensions, GD&T, datums, notes, tables). Plus `health` from
    `validate`.
  - `sketch(name, include)`: a sketch's entities (Line1, Arc1, ... with their points
    in mm), its dimensions and relations by entity name (`horizontal(Line1)`,
    `coincident(Line1.end, Line2.start)`), and whether it is fully, under or over
    defined. Coordinates are in the sketch's own 2D space.

## Assembly (`sw_assembly`)

- **Structure:** `new_assembly`, `add_component(path, xyz, fixed=)`, `components`,
  `component_count`, `fix` / `float_`, `suppress`, `remove_component`,
  `replace_component`, `mirror_component`, `rebuild`, `save`, `export`.
- **Mates:** `mate(ref_a, ref_b, kind, align, distance_mm, angle_deg)`, one verb for the
  seven kinds; `mate_planes(comp_a, name_a, comp_b, name_b, kind)`, mating by **named**
  planes or axes; `limit_mate(...)`, which limits travel to a range instead of locking
  it; `delete_mate`.
- **Selection:** `find_face(comp, kind, axis, want_max, radius_mm)`, `faces_perp`,
  `comp_faces`, `cylinder_axis_world(comp, r)`.
- **Mechanical quality gates:** `interferences()` (zero material overlap),
  `mate_errors()` (zero over-defined or failing mates), `free_translations(comp)`
  (degrees of freedom measured empirically by nudging), `sweep_collision_angle(...)`
  (derives a rotation limit by turning the part until it collides, without creating
  mates).
- **Reading an existing assembly:** `mates(comp=)` lists every mate (also ones made
  outside this MCP): type, alignment, the two components, each reference's geometry in
  assembly coordinates (a concentric mate carries each cylinder's axis and radius), the
  value of a distance/angle mate, and its status (the SolidWorks error name).
  `free_dof(comp)` measures what those mates leave free: translations AND rotations
  about the assembly axes, by nudging the part and letting the solver answer. Together
  they answer "why can this part still turn": a pin with a concentric mate and a face
  mate comes back with `free_rotations: ["Y"]`. (`GetRemainingDOFs` does not marshal
  through `pywin32`, measured on 2017 and 2026.)
- **Exploded view:** `explode()` plans a CONTROLLED explosion (`CreateExplodedView` +
  one `AddExplodeStep` per part): fixed parts stay, each other part leaves along the axis
  direction whose path is clear of the others, until it is clear of everything it was
  nested in. It returns the steps and the `overlaps` left (empty = clean);
  `exploded_overlaps()` re-reads them. `explode(steps=[...])` takes explicit steps,
  `method="auto"` keeps SW's AutoExplode (which leaves nested parts inside each other).
  `collapse()` folds it back.

## Drawing (`sw_drawing`)

- **Sheet:** `new_drawing(model_path, size, projection, units, standard, language,
  scale)` builds the sheet **to standard**: title block, border and units. Default: A3,
  ISO, mm, first-angle projection. `sheet_scale`, `set_units`.
- **Views:** `add_view(kind, at, scale, display=)` (orthographic part views show hidden
  edges dashed), `section_view(...)`, `view_center(view)`, `set_exploded(view, exploded)`.
  `new_drawing` hides origins, planes and sketches (`hide_reference_geometry`).
- **Layout:** `arrange(gap_mm=, scale="auto"|"keep", reserve_mm=)` picks the largest ISO
  5455 scale at which everything fits and places the views like a drafter: the front view
  and the views projected from it aligned in 1st/3rd angle order, sections in its row or
  column, pictorial and flat-pattern views in the free space, tables in the frame's
  top-right corner, nothing on the title block. It measures each view WITH its
  annotations, so run it again after dimensioning. Choose the scale before dimensioning
  (`reserve_mm`): it never enlarges an annotated sheet. `list_views()` reads a drawing back (also one made
  elsewhere): per view its sheet, type (named, projected, section...), orientation,
  parent view, model, configuration, position, outline and scale.
- **Dimensions:** `import_model_dims(types, all_views)` brings in the **model's**
  dimensions (a part made by these verbs has almost none -- use `auto_dimension(view)`,
  SW's Auto Dimension in the baseline scheme); `tidy_dimensions(view)` re-spaces them;
  `dedupe_dimensions()` states each size once (per model dimension, per hole size, and
  across aligned views); `dimension(view, picks, place_at,
  orient)` adds a reference dimension on the sheet; `center_marks(view)` (holes seen
  end-on); `centerlines(view)` (the axis of every VISIBLE cylinder seen from the side; a
  hidden hole is not exposed by the view, so cut a section first).
- **GD&T and finish:** `datum(view, edge_at, label)`, `gtol(view, edge_at, symbol,
  tolerance, datums=, diameter=, mc=)`, `surface_finish(view, edge_at, ra, kind)`. The
  frame and the symbol are placed OUTSIDE the view with a leader unless `place_at` says
  otherwise.
- **Assembly drawings:** `balloons(view, layout)` (balloon **before** the BOM; the
  balloons are pulled in next to the view), `table(view, kind="bom")` (one row per part,
  in the frame's top-right corner by default).
- **Title block and output:** `fill_titleblock(title=, material="auto", weight="auto",
  finish=, drawn_by=, ...)` writes title, material and weight into the MODEL (linked to
  the assigned material and the mass, with its unit) and saves it; the drawing number is
  the drawing's file name, so save before exporting. `annotate`, `save`, `export` (PDF).
- **Quality gate:** `quality()` reads whether the drawing is usable: views, tables and
  annotations outside the frame or on the title block, overlapping views, an empty BOM,
  balloons without a number, empty title-block fields, views without dimensions, and the
  model's sizes that no dimension states. `problems == []` plus a look at the PDF.
- **Sheet metal:** `flat_pattern_view(at=, bend_notes=True)`, `bend_table(view, at=)`,
  `bend_lines(view)`.

## Sheet metal (`sw_sheetmetal`)

Construction, flattening, fabrication output and parametric editing, 47 verbs in all. It
is the domain where the API resisted most, so it has its own guide:
[SHEET_METAL.md](docs/SHEET_METAL.md).

```python
from solidworks import sw_com as C, sw_parts as P, sw_sheetmetal as SM

C.connect(); C.close_untitled()
SM.new_sheet("Front", 100, 60, thickness_mm=2)   # base sheet
SM.box_flanges(20)                               # 4 flanges -> a box
P.save(r"C:\...\box.sldprt")                    # export_flat REQUIRES a saved part
SM.flatten(True)                                 # 136.6 x 96.6 x 2.0 mm
SM.export_flat(r"C:\...\box.dxf")               # the DXF for the cutter
print(SM.cut_list())                             # resolved fabrication data
```

## Gotchas the layer handles for you

Each of these cost real time once. They are why the verbs exist, and each has a recipe
(`sw_search_api(..., partition="recipe")`).

- **Early binding is mandatory** for assemblies and for anything returning objects:
  `C.cast(obj, "IInterface")`. `win32com.client.CastTo` does **not** work on these
  objects. Returned objects come back dynamic, so **re-cast** them.
- **Null dispatch:** the early-bound path wants a plain `None`.
  `VARIANT(VT_DISPATCH, None)` belongs to the dynamic path (`sw_call`) only.
- **`swAddMateError_NoError == 1`**, not 0. An ANGLE mate returns 5 (over-defined) even
  when it creates the mate.
- **Several methods return `None` or a tuple even when they built something**
  (`InsertRefPlane`, `InsertMirror`, `InsertScale`, ...), so the verb reads the new
  feature back from the tree.
- **Selection marks for patterns:** linear direction = 1, seed = 4; circular axis = 1,
  seed = 4; mirror plane = 2, feature = 1.
- **Hygiene:** ~20+ open documents make SolidWorks crawl. SaveAs fails if the file is
  open, or referenced by an open assembly.
- **Grounding an assembly:** mating to the three assembly planes over-defines it.
  Position the part, then `fix` it.
- **`AddComponent5` only inserts a document that is already loaded.** Straight from disk
  it returns `None` silently (measured 0/5 without preloading, 5/5 with).
  `add_component` opens the file first. `OpenDoc6` activates what it opens, and only
  `ActivateDoc` (v1) hands focus back.
- **In a coincident mate, `align` decides whether the assembly is physically possible.**
  With the wrong side, a flange ends up inside the neighbouring part.
- **`IComponent2.GetBox` measures position, not shape.** It is aligned to the assembly
  axes, so the free rotation a mate leaves makes the same 100 × 60 × 32 part measure
  115 × 94 × 32. Shape criteria come from the body (`IBody2.GetBodyBox`).
- **`IModelDoc2.Save3` raises `Type mismatch`** (by-ref out-params), so saving goes
  through `C.save_as(md, path)`, even onto the document's own file.
- **Drawings:** the default template is ANSI / inches. `new_drawing` builds an ISO sheet
  from the standard's `.slddrt` and deletes the ANSI one. Dimensions need
  `set_units('mm')`. `import_model_dims` needs `AllViews=True`.
- **Dimensions and tolerances:** creating a dimension would open the modal *Modify* box,
  so `C.connect()` turns that preference off. A sketch segment casts to
  `ISketchSegment`, not `IEntity`. Tolerances go through
  `IDimensionTolerance.SetValues` (`SetValues2` returns False).
- **Dialogs that are not ours:** SolidWorks has a **save reminder** that pops up every
  20 minutes and waits for an answer, which stalls a long unattended run.
  `C.connect()` turns it off, together with the close-sketch prompt.
- **Enums:** take them from the makepy-generated constants module, not from the help.

## Limits

These stay on the escape hatch because they were measured not to work through
`pywin32`: Hole Wizard, `rib` (a silent no-op), move/copy body (`InsertMoveCopyBody2`
returns Nothing), split, fill/curve patterns, component patterns, detail view (the view
is created but empty), hole/weld/revision tables, note patterns. Use `sw_call` with a
recipe, or the SolidWorks UI.
