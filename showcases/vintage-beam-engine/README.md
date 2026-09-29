# Vintage Beam Engine

**Requirements-Driven Design**

This case study starts with an engineering brief, not a complete set of component
dimensions. Unlike the Inline-2 Engine's emphasis on CAD workflow breadth, it focuses
on deriving detailed geometry from requirements and constraints.

The brief specifies a maximum envelope, stroke range, approximate flywheel size,
mechanism architecture, full 360-degree motion, and no unintended interference.
The AI system determines the remaining dimensions, proportions, pivot locations,
linkage lengths, and detailed geometry, using CADapter as the SOLIDWORKS automation
layer.

The mechanism includes a flywheel, crank, connecting rod, rocking beam,
piston/cylinder, crosshead, and bearing pedestals.

## Workflow

**requirements + constraints -> detailed design -> parts -> assembly -> validated motion**

The resulting assembly must be checked against the brief, including full rotation
and interference clearance. These are acceptance criteria, not a claim that this
showcase has already passed a recorded validation.

## Prompt and recording

- [Recorded demonstration prompt](prompt.md)
- Recording location: `showcases/vintage-beam-engine/demo.mp4`.

The exact recorded prompt is included in [prompt.md](prompt.md). Generated CAD project
artifacts are not distributed with this showcase.

[Watch the demonstration](demo.mp4)

The [constrained clevis workflow](../../solidworks/tests/smoke/smoke_clevis.py)
provides separate reproducible evidence for assembly operations; it does not
validate this engine's design.

[Back to CADapter](../../README.md#showcases)
