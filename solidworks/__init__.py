"""
solidworks/ package -- curated layers of CADapter (see docs/ARCHITECTURE.md).

  sw_com      layer 0: COM plumbing, early binding, encapsulated gotchas (not a tool)
  sw_parts    layer 1: PART verbs (part.*)
  sw_assembly layer 1: ASSEMBLY verbs (asm.*)
  sw_drawing  layer 1: TECHNICAL DRAWING verbs (dwg.*)
  sw_sheetmetal layer 1: SHEET METAL verbs (sheet.*)
  sw_inspect  read-only inspection (list_features, find_entities, screenshot)

The deterministic engine IMPORTS these modules; the MCP exposes the verbs as thin tools.
"""
