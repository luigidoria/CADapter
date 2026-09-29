You are going to build a complete mechanical showcase project in SOLIDWORKS using CADapter.

This is a recorded CADapter demonstration. The SOLIDWORKS window is being recorded continuously, so the quality and visibility of what happens on screen are important.

Your job is to execute the project directly in SOLIDWORKS.

Do not narrate your reasoning.
Do not explain what you are about to do.
Do not provide progress updates.
Do not describe tool calls.
Do not summarize intermediate results.

During a block, work silently.

The ONLY normal text response allowed is the exact completion marker defined at the end of each block.

---

# PROJECT

Create a complete open-frame two-cylinder inline reciprocating engine demonstrator.

This is a mechanical CAD showcase, not a production engine.

The system must visually and mechanically demonstrate:

- native parametric part creation;
- multiple non-trivial SOLIDWORKS parts;
- reusable components;
- a multi-component mechanical assembly;
- mates and constrained motion;
- a rotating crankshaft;
- two connecting rods;
- two reciprocating pistons;
- 180-degree crank phasing between the two cylinders;
- a flywheel;
- interference and mate validation;
- technical drawings of the manufactured parts;
- a complete assembly drawing;
- exploded assembly documentation;
- BOM and balloons;
- PDF drawing export.

The final result should look like a coherent mechanical engineering project, not a collection of primitive demonstration shapes.

Prefer visually convincing geometry while keeping the design robust enough to build reliably with the available CADapter operations.

---

# CRITICAL RECORDING / CAMERA RULES

The SOLIDWORKS window is being recorded.

At ALL TIMES, the geometry currently being created or edited must remain clearly visible on screen.

This requirement is mandatory.

## General camera behavior

Whenever you:

- open or create a document;
- create a major feature;
- substantially change the size of a part;
- insert components into an assembly;
- move components;
- create or modify a drawing;
- switch between parts, assemblies or drawings;

actively adjust the SOLIDWORKS view.

Use Zoom to Fit whenever the current object no longer fits comfortably in the viewport.

Never continue modeling geometry that is outside the visible viewport.

Never continue working while the important part of the model is clipped by the edge of the screen.

If a feature is small, zoom in enough that the feature being created is visible.

After completing a local detail, zoom back out so the overall part is visible again.

For general modeling and assembly work, prefer an isometric view when practical.

For sketches, use a view normal to the sketch plane when useful.

After completing a significant feature, return to a useful 3D view and fit the model again.

## Assembly recording

During assembly:

- keep the whole mechanism visible whenever practical;
- after inserting approximately 2-3 components, fit the assembly again;
- after large component movements, fit the assembly again;
- when working on a mate between small features, zoom in temporarily;
- after the mate is complete, return to an overall assembly view.

The viewer should always be able to understand what is changing.

## Drawing recording

When working on drawings:

- fit the sheet after creating the drawing;
- fit again after adding or rearranging views;
- zoom appropriately when dimensions or annotations are being edited;
- return to a full-sheet view after major changes;
- leave each completed drawing clearly visible before moving to the next one.

---

# CADAPTER USAGE

Prefer CADapter curated verbs.

Use raw COM calls only when the curated layer does not provide the required operation.

When raw COM is necessary:

1. inspect the signature first;
2. use the correct SOLIDWORKS interface;
3. validate the result afterward.

Do not modify CADapter source code, tests, configuration, baselines or documentation during this project.

This task is exclusively about creating CAD project files.

If a requested decorative feature proves unreliable after reasonable attempts, simplify that local feature while preserving the mechanical function and visual character of the design.

Do not stop the whole project because of a non-essential decorative detail.

Do not ask me questions during execution.

Make reasonable engineering decisions yourself when small unspecified details are required.

---

# PERSISTENT PROJECT STATE

Conversation context will be compacted between blocks.

Therefore, before beginning CAD work, create a project directory OUTSIDE the CADapter source repository.

Use a directory such as:

CADapter_Showcase/Inline2_Engine/

Inside it create:

Parts/
Assembly/
Drawings/
PDF/
ProjectState/

Create:

ProjectState/SHOWCASE_SPEC.md
ProjectState/SHOWCASE_STATE.md

`SHOWCASE_SPEC.md` must contain a concise but complete copy of:

- project architecture;
- important dimensions;
- file names;
- component quantities;
- assembly layout;
- block sequence;
- remaining work.

`SHOWCASE_STATE.md` must be updated at the end of every block with:

- completed files;
- important implementation decisions;
- deviations from the requested geometry;
- verified dimensions;
- unresolved non-critical issues;
- the next block to execute.

These state files exist only to survive conversation compaction.

When I later say:

Continue.

silently read both state files first and resume from the next incomplete block.

Do not explain that you read them.

---

# GLOBAL DESIGN COORDINATE SYSTEM

Use this design convention throughout the project:

- X = crankshaft axis and engine length;
- Y = lateral direction;
- Z = vertical direction;
- piston motion = Z axis;
- cylinder centers = X positions -60 mm and +60 mm;
- crankshaft nominal axis height = 60 mm above the top surface of the base plate;
- cylinder centerlines lie at Y = 0.

The engine should remain visually symmetrical around Y = 0.

The two crank throws must be 180 degrees out of phase.

---

# TARGET ASSEMBLY

The completed assembly should contain approximately 14-18 component instances.

Use repeated instances of reusable manufactured parts instead of creating unique duplicate files.

Target structure:

1x base_plate
3x bearing_support
2x cylinder_guide
1x crankshaft
1x flywheel
2x connecting_rod
2x piston
2x piston_pin
1x hand_crank_arm if useful
1x crank_handle if useful

Optional spacers or simple retainers may be added if they improve the assembly mechanically or visually.

Do not add dozens of cosmetic fasteners just to inflate the component count.

---

# MATERIAL INTENT

Where the installed SOLIDWORKS material database makes assignment reliable:

Base / structural parts:
- Aluminum 6061 or a close standard aluminum material.

Pistons:
- Aluminum.

Crankshaft:
- medium-carbon steel.

Connecting rods:
- steel.

Pins:
- steel.

Flywheel:
- steel or aluminum.

Material assignment must not block the project if a particular database name is unavailable.

Geometry and assembly are more important than exact material database naming.

---

# PART SPECIFICATIONS

The dimensions below define the intended design.

Small adjustments are allowed where required for clearances, feature robustness or assembly feasibility.

Preserve the overall architecture and proportions.

---

## PART A — BASE PLATE

File:

Parts/base_plate.SLDPRT

Nominal size:

300 mm X
180 mm Y
16 mm Z thickness

Features:

- rectangular base plate;
- rounded external corners, approximately R8;
- four mounting holes near the corners, approximately Ø9 mm;
- symmetric layout;
- additional mounting holes for the three crankshaft bearing supports;
- mounting holes or pads for the two cylinder-guide structures;
- clean parametric feature tree.

The base should visually anchor the complete mechanism.

Avoid making it just a completely featureless rectangular slab.

Add reasonable mounting patterns while keeping the geometry clean.

---

## PART B — BEARING SUPPORT

File:

Parts/bearing_support.SLDPRT

Quantity in assembly:

3

Purpose:

Support the crankshaft at approximately:

X = -110 mm
X = 0 mm
X = +110 mm

Target geometry:

- mounting foot approximately 50 x 70 mm;
- support thickness along X approximately 18 mm;
- total height approximately 70 mm;
- crankshaft bore centered 60 mm above the base mounting surface;
- bore approximately Ø20.6 mm;
- two or four mounting holes;
- rounded upper profile around the bearing region;
- fillets approximately R4-R6 where appropriate.

Create useful reference geometry or named axes if it improves assembly mating.

The part should visually resemble a proper bearing pedestal rather than a simple block with a hole.

---

## PART C — CYLINDER GUIDE / OPEN SUPPORT

File:

Parts/cylinder_guide.SLDPRT

Quantity:

2

Purpose:

Guide each piston vertically while leaving the connecting rod and crank mechanism visually exposed.

The design should look like an open-frame engine demonstrator rather than a closed engine block.

Target architecture:

- vertical cylindrical piston guide or sleeve;
- piston axis along Z;
- sleeve inner diameter approximately 47 mm;
- sleeve outer diameter approximately 58-62 mm;
- useful guide length approximately 80-90 mm;
- enough radial clearance for a Ø46 mm piston;
- structural mounting flange, feet, side legs or support geometry that allows the guide to mount rigidly to the base;
- lower region should remain sufficiently open for the connecting rod to move toward the crankshaft;
- mounting holes;
- fillets where useful.

The final guide should visually frame the piston while allowing the reciprocating motion to remain visible.

Position the two instances on the engine at:

X = -60 mm
X = +60 mm

Y = 0

---

## PART D — CRANKSHAFT

File:

Parts/crankshaft.SLDPRT

This is one of the HERO parts of the showcase.

Make it visually substantial.

Overall length:

approximately 260 mm along X.

Main shaft:

approximately Ø20 mm.

Two crank throws:

centered near:

X = -60 mm
X = +60 mm

Crank radius:

28 mm.

The two throws must be 180 degrees apart.

Suggested architecture:

- continuous main shaft or connected main journals;
- four crank webs;
- two eccentric crank pins;
- webs approximately 60-66 mm outside diameter or equivalent shaped profiles;
- web thickness around 8-10 mm;
- connecting-rod clearance approximately 14-16 mm between paired webs;
- crank pin diameter approximately Ø16 mm;
- crank pins aligned with X;
- first crank pin offset +28 mm from the crankshaft axis;
- second crank pin offset -28 mm from the crankshaft axis;
- fillets between major transitions where robust;
- suitable right-side region for mounting the flywheel.

Use a modeling strategy that produces one coherent solid body.

Validate that all bosses/webs/pins are merged correctly.

The crankshaft must visually communicate that it is a real crankshaft, not simply a straight shaft with two cylinders attached.

---

## PART E — CONNECTING ROD

File:

Parts/connecting_rod.SLDPRT

Quantity:

2 identical instances.

Center-to-center length:

approximately 105 mm.

Big end:

- outside diameter approximately 38 mm;
- bore approximately Ø16.4 mm.

Small end:

- outside diameter approximately 28 mm;
- bore approximately Ø10.4 mm.

Thickness:

approximately 12 mm.

Body:

- tapered shank between the two ends;
- visually lighter central web;
- optional shallow central relief/pocket if robust;
- smooth transitions;
- fillets approximately R2-R4.

The connecting rod should be visually recognizable immediately.

This is another good candidate for recorded part-creation footage.

---

## PART F — PISTON

File:

Parts/piston.SLDPRT

Quantity:

2 identical instances.

Target:

- diameter approximately Ø46 mm;
- height approximately 28-32 mm;
- wrist-pin bore approximately Ø10.4 mm;
- wrist-pin bore axis along X after assembly;
- small edge chamfers or fillets;
- optional shallow ring grooves if reliable.

The piston must slide freely inside the cylinder guide.

Maintain sufficient clearance.

---

## PART G — PISTON PIN

File:

Parts/piston_pin.SLDPRT

Quantity:

2.

Target:

- diameter approximately Ø10 mm;
- length sufficient to pass through the piston and connecting-rod small end;
- approximately 52-58 mm depending on the final piston geometry;
- small chamfers on the ends.

Simple geometry is acceptable.

---

## PART H — FLYWHEEL

File:

Parts/flywheel.SLDPRT

This should be visually interesting.

Target:

- outside diameter approximately Ø120 mm;
- thickness approximately 14 mm;
- central bore approximately Ø20.4 mm;
- hub diameter approximately Ø34-40 mm;
- substantial outer rim;
- approximately 6 spokes or 6 repeated radial windows;
- use a circular pattern where practical;
- edge fillets or chamfers.

The flywheel should clearly show CADapter handling repeated geometry.

Do not make it a plain solid disk unless a more complex version proves genuinely unreliable.

---

## OPTIONAL PART I — HAND CRANK ARM

File:

Parts/hand_crank_arm.SLDPRT

Add this only if it improves the completed showcase.

It may connect to the flywheel or crankshaft end.

Target:

- simple but polished arm;
- two bores;
- tapered or rounded profile.

---

## OPTIONAL PART J — CRANK HANDLE

File:

Parts/crank_handle.SLDPRT

Simple cylindrical handle/pin mounted to the crank arm.

Again, only add if it improves the final mechanism without destabilizing the project.

---

# PARAMETRIC QUALITY

For every major manufactured part:

- use native SOLIDWORKS features;
- keep the feature tree clean;
- use sketches and proper features rather than imported mesh geometry;
- use patterns where they naturally apply;
- rebuild after meaningful feature groups;
- validate the model;
- inspect the final geometry;
- save the file.

Use named dimensions or meaningful reference geometry where practical, but do not spend excessive time naming every trivial item.

---

# BLOCK 1 — STRUCTURAL PARTS

Create and save:

- base_plate;
- bearing_support;
- cylinder_guide.

For each part:

1. create the geometry;
2. keep the geometry visible during modeling;
3. rebuild;
4. validate;
5. inspect important dimensions;
6. save.

After all three parts are complete:

- update SHOWCASE_STATE.md;
- ensure all files exist;
- do NOT begin the crankshaft.

Then stop completely.

Your ONLY response must be:

BLOCK 1 COMPLETE

---

# BLOCK 2 — MOVING PARTS

After I say `Continue`, silently reload the project state and create:

- crankshaft;
- connecting_rod;
- piston;
- piston_pin;
- flywheel;
- optional hand crank components if appropriate.

The crankshaft and flywheel should receive extra visual attention because they are important showcase parts.

While recording the crankshaft and connecting rod creation:

- keep the feature being created clearly visible;
- frequently fit the model;
- do not allow the feature tree/model to move off-screen while continuing operations.

Validate and save every part.

Before finishing this block, inspect the final crankshaft carefully and confirm:

- both throws exist;
- their crank radius is approximately 28 mm;
- their phase differs by 180 degrees;
- crank pins are correctly positioned;
- the result is one valid body;
- connecting rod clearances are plausible.

Update SHOWCASE_STATE.md.

Then stop.

Your ONLY response must be:

BLOCK 2 COMPLETE

---

# BLOCK 3 — COMPLETE ASSEMBLY

After I say `Continue`, silently reload the project state.

Create:

Assembly/inline2_engine.SLDASM

Build the complete mechanism.

Target global geometry:

- base plate fixed;
- crankshaft axis along X;
- crankshaft axis approximately 60 mm above the base;
- bearing supports at approximately X = -110, 0, +110 mm;
- cylinders centered around X = -60 and +60 mm;
- piston motion along Z;
- connecting rods connect the two crank pins to the two piston pins;
- flywheel mounted on a crankshaft end.

Use appropriate mates and reference geometry.

Requirements:

### Base

Fix the base.

### Bearing supports

Mount the three supports rigidly to the base.

Their bores must share the crankshaft axis.

### Crankshaft

Constrain the crankshaft:

- concentric with the bearing-support bores;
- axially located;
- rotationally FREE.

Do not accidentally lock crankshaft rotation.

### Cylinder guides

Mount both guides rigidly to the base.

Their piston axes must be vertical and parallel.

### Pistons

Each piston must:

- remain centered in its guide;
- move only along the intended vertical axis as far as practical;
- remain free to reciprocate.

Avoid unnecessary rotational DOF if it can be eliminated robustly.

### Connecting rods

Mate each rod:

- big end to its crank pin;
- small end to the corresponding piston pin.

Preserve the required rotational freedom at the joints.

### Piston pins

Properly locate each piston pin through its piston and connecting rod.

### Flywheel

Mount the flywheel concentrically and rigidly to the crankshaft.

The flywheel must rotate together with the crankshaft.

### Optional hand crank

If created, mount it so it follows the crankshaft/flywheel.

---

# ASSEMBLY QUALITY

The completed mechanism must not merely look assembled.

Inspect:

- mate errors;
- component locations;
- unexpected interference;
- obviously floating components;
- obviously over-defined mates;
- unintended free translations.

Correct problems before continuing.

Keep the entire engine visually framed during the final assembly stage.

Once all components are inserted, spend a moment with the complete assembly fitted clearly in the viewport before moving to validation.

Update SHOWCASE_STATE.md.

Then stop.

Your ONLY response must be:

BLOCK 3 COMPLETE

---

# BLOCK 4 — MOTION, MECHANICAL VALIDATION AND VISUAL REFINEMENT

After I say `Continue`, silently reload project state.

This block is especially important for the final GitHub video.

## Motion

Demonstrate the actual mechanism moving.

Rotate the crankshaft/flywheel through a complete revolution while allowing the mates to solve the mechanism.

The two pistons must visibly reciprocate.

Because the crank throws are 180 degrees apart, the two piston motions must show the intended phase relationship.

Test several crank angles across the full 360-degree cycle.

Suggested checkpoints:

0°
45°
90°
135°
180°
225°
270°
315°
360°

You do not need to narrate these checkpoints.

The purpose is to verify the mechanism.

If necessary, use smaller angular increments.

## Critical video requirement

During the motion demonstration:

- use a useful isometric view;
- fit the whole engine in the viewport;
- do not zoom into only one piston;
- keep the crankshaft, connecting rods and both pistons visible simultaneously;
- perform enough continuous or sequential motion that the recorded footage clearly demonstrates the mechanism working.

This should be one of the strongest visual moments of the entire run.

## Validate

Check:

- mate errors;
- interference at multiple crank positions;
- connecting rod behavior;
- piston guidance;
- crankshaft rotation;
- component clearance;
- assembly rebuild health.

Correct meaningful issues.

If purely cosmetic contact or tiny tolerance issues exist, correct clearances rather than disabling validation.

Do not intentionally suppress real interference just to obtain a clean report.

## Final visual state

Return the engine to a visually attractive isometric configuration.

Fit it cleanly in the viewport.

Leave the completed mechanism clearly visible for a short period before finishing the block.

Save the assembly.

Update SHOWCASE_STATE.md.

Then stop.

Your ONLY response must be:

BLOCK 4 COMPLETE

---

# BLOCK 5 — TECHNICAL DRAWINGS

After I say `Continue`, silently reload project state.

Create manufacturing-style drawings for the project.

Use native SOLIDWORKS drawings.

Use:

- ISO standard;
- millimetres;
- first-angle projection;
- sensible sheet sizes;
- clean readable layout;
- appropriate scales.

Do not overload drawings with every possible dimension.

Include the dimensions required to communicate the manufactured geometry clearly.

Use CADapter drawing inspection and quality capabilities to evaluate and improve the result.

If a first automatic layout is cluttered, refine it.

The video should visibly show drawings evolving and being improved.

---

## REQUIRED PART DRAWINGS

Create drawings for at least:

1. crankshaft;
2. connecting rod;
3. flywheel;
4. piston;
5. piston pin;
6. bearing support;
7. cylinder guide;
8. base plate.

Repeated component instances do not require duplicate drawings.

For complex parts use appropriate combinations of:

- front view;
- top view;
- side view;
- isometric view;
- section view;
- center marks;
- centerlines.

The crankshaft drawing should be one of the richest part drawings.

The connecting rod and flywheel drawings should also be visually strong.

Use section views where they genuinely improve clarity.

---

# DIMENSIONING

Dimension enough geometry to define the part while keeping the sheet readable.

Prefer manufacturing-relevant dimensions.

Include where appropriate:

- overall dimensions;
- hole diameters;
- bore diameters;
- center distances;
- thicknesses;
- radii;
- pattern information;
- crank throw / eccentric offset;
- critical mounting locations.

Do not intentionally duplicate the same dimension across multiple views.

Use the drawing quality tools to inspect for:

- overlapping views;
- dimension clashes;
- annotations outside the border;
- annotations on the title block;
- missing important dimensions;
- unreadable layout.

Refine the drawing when meaningful problems are detected.

---

# ASSEMBLY DRAWING

Create:

Drawings/inline2_engine_assembly.SLDDRW

Use an A3 landscape sheet unless another size is clearly more appropriate.

Include:

- principal orthographic view(s);
- isometric assembled view;
- exploded isometric view if supported reliably;
- BOM;
- balloons;
- important assembly-level dimensions where useful;
- title block information.

The assembly drawing should visually communicate the full machine clearly.

Arrange the sheet professionally.

Do not crowd every possible view onto the sheet.

---

# DRAWING OUTPUT

Save every drawing as `.SLDDRW`.

Export corresponding PDFs into:

PDF/

At minimum export PDFs for:

- crankshaft;
- connecting rod;
- flywheel;
- cylinder guide;
- base plate;
- complete assembly.

Prefer exporting all completed drawings if reliable.

---

# DRAWING RECORDING RULE

The drawing phase is also being recorded.

When a sheet is created:

fit the sheet.

When views are inserted:

fit the sheet again.

When dimensions appear:

keep the affected region visible.

When rearranging views:

show the layout changing.

When the drawing is complete:

return to a full-sheet view and leave it visible briefly.

For the final assembly drawing, leave the completed sheet clearly visible before finishing the project.

---

# FINAL PROJECT VALIDATION

Before declaring the project complete:

Verify that the project directory contains the expected:

- SLDPRT files;
- SLDASM;
- SLDDRW files;
- PDFs.

Open or inspect the final assembly.

Confirm that it rebuilds correctly.

Confirm that the primary motion still works.

Confirm that the major drawings exist.

Confirm that the project state file reflects completion.

Do not alter CADapter source code.

Do not create or modify tests.

Do not create public-repository content during this run.

Update SHOWCASE_STATE.md one final time.

Then stop completely.

Your ONLY response must be:

PROJECT COMPLETE
