# Inline-2 Engine

**Complete CAD Workflow**

This case study connects part creation, assembly construction, mechanism motion,
and technical drawings. Its purpose is to demonstrate workflow breadth across CAD
stages, not only geometry generation.

The design includes a crankshaft, two pistons, two connecting rods, a flywheel,
bearing supports, cylinder guides, and supporting components.

## Workflow

**part creation -> assembly -> motion -> technical drawings**

An AI system uses CADapter to create native SOLIDWORKS components, assemble them
with mates, check mechanism motion and interference, then create and refine drawings.
These are the workflow and acceptance criteria; this page is not a completed
validation report.

## Prompt and recording

- [Recorded demonstration prompt](prompt.md)
- Recording location: `showcases/inline-2-engine/demo.mp4`.

The exact recorded prompt is included in [prompt.md](prompt.md). Generated CAD project
artifacts are not distributed with this showcase.

[Watch the demonstration](demo.mp4)

Reproducible engineering evidence is available separately:
[part creation and inspection](../../solidworks/tests/smoke/smoke_parts.py),
[technical drawing benchmark](../../solidworks/tests/benchmark/benchmark_drawings.py),
and [constrained clevis workflow](../../solidworks/tests/smoke/smoke_clevis.py).
These tests are not recordings or validation results for this engine.

[Back to CADapter](../../README.md#showcases)
