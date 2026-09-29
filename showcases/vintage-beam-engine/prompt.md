Build a complete compact bench-mounted vintage beam engine demonstrator in SOLIDWORKS using CADapter.

Create all required parts, assemble the mechanism, validate it, and demonstrate its motion.

This is being recorded for a LinkedIn launch demo.

Work directly in SOLIDWORKS. Do not narrate your reasoning, explain tool calls, provide progress updates, or describe what you are doing.

During execution, remain silent.

The only text responses allowed are the block completion markers defined below.

---

## Engineering requirements

Design the machine from the following requirements rather than from a fully dimensioned specification.

The finished machine must:

- fit within a maximum envelope of approximately **400 × 220 × 300 mm**;
- be suitable as a compact bench-mounted mechanical demonstrator;
- use one rotational input;
- convert crankshaft rotation into reciprocating piston motion through a rocking beam mechanism;
- allow a complete **360° crankshaft rotation**;
- have approximately **50–70 mm piston stroke**;
- use a large visible flywheel, approximately **140–180 mm diameter**;
- keep the main mechanism exposed and visually understandable;
- have no meaningful interference throughout its operating cycle;
- use mechanically plausible dimensions and proportions;
- avoid unnecessarily thin sections, unrealistic pins, or fragile geometry;
- use reusable components where appropriate.

Determine the remaining component dimensions, pivot locations, rod lengths, support heights, section sizes, hole locations, shaft lengths, clearances and detailed geometry yourself.

Before modeling, establish a coherent mechanism layout from these constraints and record the chosen principal dimensions in `SHOWCASE_SPEC.md`.

Do not ask me to choose dimensions unless a requirement is genuinely impossible to resolve.

---

## Architecture

The final machine should look like an old industrial / steam-era beam engine with exposed mechanical motion.

Include at least:

- substantial structural base;
- central beam stand;
- rocking beam;
- large spoked flywheel;
- main shaft;
- two bearing pedestals;
- crank mechanism;
- connecting rod between crank and one end of the beam;
- vertical piston and cylinder system on the opposite side;
- piston rod;
- guided crosshead / slider;
- vertical link between crosshead and beam;
- required shafts and pivot pins.

You may add a few useful structural or mechanical components if they improve the design.

Do not add unnecessary cosmetic fasteners.

The design should look intentionally engineered and vintage rather than like an assembly of primitive blocks.

Use rounded profiles, mounting feet, bosses, fillets, tapered links and other appropriate mechanical details where useful.

Prioritize:

1. convincing vintage-machine appearance;
2. clear exposed mechanism;
3. robust native SOLIDWORKS geometry;
4. reliable assembly and motion.

Use native parametric SOLIDWORKS features.

Use patterns where appropriate, especially for the flywheel.

Prefer CADapter curated verbs. Use raw COM only when necessary.

Do not modify CADapter source code, tests, configuration or documentation.

---

## Recording

Keep whatever you are working on clearly visible in SOLIDWORKS.

Frequently adjust the camera and use Zoom to Fit.

Never continue working on geometry that has moved outside the visible viewport.

For small features, zoom in temporarily and then return to a useful overall or isometric view.

During assembly and motion, keep the complete mechanism visible whenever practical.

The viewer should always be able to understand what is changing.

---

## Project state

Use the current `showcase4` directory as the project root.

Create only what is needed, such as:

- `Parts/`
- `Assembly/`
- `SHOWCASE_SPEC.md`
- `SHOWCASE_STATE.md`

`SHOWCASE_SPEC.md` should record:

- chosen principal dimensions;
- component list;
- mechanism layout;
- important design constraints.

Update `SHOWCASE_STATE.md` at the end of each block with:

- completed parts;
- important design decisions;
- deviations or simplifications;
- next block.

Conversation context will be compacted between blocks.

When I later say `Continue`, silently read these files and resume from the next incomplete block.

---

# BLOCK 1 — STRUCTURAL PARTS

Create and save the main stationary parts:

- base;
- beam stand;
- bearing pedestal;
- cylinder body;
- crosshead / slider guide.

Make them visually appropriate for a vintage engine.

Avoid overly simple rectangular geometry when a more mechanical shape can be created reliably.

Keep each part clearly visible while modeling.

Validate and save every part.

Update the project state.

Do not begin the moving mechanism yet.

Respond only:

`BLOCK 1 COMPLETE`

---

# BLOCK 2 — MOVING PARTS

After `Continue`, silently reload the project state.

Create and save:

- rocking beam;
- main shaft;
- large spoked flywheel with crank feature;
- connecting rod;
- piston;
- piston rod;
- crosshead / slider;
- vertical link;
- required pivot pins or shafts.

Give extra visual attention to the flywheel and beam because they are key showcase parts.

The flywheel should have a substantial rim, hub and repeated spokes or radial openings.

The beam should have a recognizable vintage cast or tapered profile with a central pivot and end connections.

Keep the active geometry clearly visible throughout modeling.

Validate and save every part.

Update the project state.

Respond only:

`BLOCK 2 COMPLETE`

---

# BLOCK 3 — ASSEMBLY

After `Continue`, silently reload the project state.

Create the complete beam-engine assembly.

Arrange it as a coherent old stationary engine:

- base fixed;
- beam stand near the center;
- flywheel and crankshaft on one side;
- cylinder and piston system on the opposite side;
- rocking beam above the machine;
- connecting rod between crank and one end of the beam;
- vertical linkage between the opposite beam end and the crosshead / piston system.

Mount the bearing pedestals rigidly to the base and support the main shaft through them.

The main shaft and flywheel must remain free to rotate.

The beam must pivot freely around its center.

The piston / crosshead system must remain correctly guided.

Preserve the required rotational freedom at every linkage joint.

After every few inserted components, fit the assembly in the viewport again.

Before completing the block:

- inspect mate errors;
- check unintended free motion;
- check for floating components;
- check obvious interferences;
- correct meaningful problems.

Save the assembly and update the project state.

Respond only:

`BLOCK 3 COMPLETE`

---

# BLOCK 4 — MOTION AND FINAL VALIDATION

After `Continue`, silently reload the project state.

Demonstrate the complete mechanism through a full crankshaft revolution.

The motion must clearly show:

- flywheel rotation;
- crank motion;
- connecting-rod motion;
- rocking-beam motion;
- piston / crosshead reciprocation.

Check multiple positions throughout the full 360° cycle.

Validate:

- mate health;
- crankshaft freedom;
- piston guidance;
- beam motion;
- linkage behavior;
- clearances;
- interference;
- assembly rebuild health.

Correct meaningful issues.

For the final recorded motion:

- use a clear isometric view;
- fit the complete machine in the viewport;
- keep the flywheel, beam and piston mechanism visible simultaneously;
- show enough motion that the mechanism is immediately understandable.

Return the engine to an attractive final isometric view.

Fit it cleanly in the viewport.

Save the final assembly and update `SHOWCASE_STATE.md`.

Leave the completed machine clearly visible.

Respond only:

`PROJECT COMPLETE`
