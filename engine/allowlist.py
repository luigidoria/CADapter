"""
allowlist.py -- defines the operations permitted in SOLIDWORKS execution bundles.

Validates methods, properties and helpers before execution, and identifies
destructive method calls. New operations must be explicitly added to this policy.
"""

from __future__ import annotations

# methods allowed per target alias
METHODS: dict[str, set[str]] = {
    "app": {
        "GetUserPreferenceStringValue", "NewDocument", "OpenDoc6", "RevisionNumber",
        "GetOpenDocumentByName", "CloseDoc", "ActivateDoc3", "GetDocumentCount",
    },
    "doc": {
        # document / tree
        "GetTitle", "GetPathName", "GetType", "ClearSelection2", "FeatureByPositionReverse",
        "FirstFeature", "SaveAs", "Save3", "EditRebuild3", "ForceRebuild3", "SaveBMP",
        # parametric
        "Parameter", "GetEquationMgr",
        # features created by IModelDoc2 (not by FeatureManager)
        "InsertScale", "InsertFeatureShell", "InsertAxis", "InsertAxis2",
        "InsertSketch", "SketchManager", "SelectionManager",
        # sketch dimensions
        "AddDimension2", "AddHorizontalDimension2", "AddVerticalDimension2",
        # assembly/drawing use the same doc
        "EditDelete", "ViewZoomtofit2", "EditSuppress2", "EditUnsuppress2",
        "InsertNote", "InsertDatumTag2", "InsertGtol",
        # sheet metal features created by IModelDoc2 (not by FeatureManager)
        "InsertSheetMetalBreakCorner", "InsertSheetMetalClosedCorner",
        "InsertSheetMetalUnfold", "InsertSheetMetalFold", "InsertSheetMetalJog",
        "InsertRip", "ForceRebuild3",
    },
    "ext": {
        "SelectByID2", "CreateMassProperty", "CustomPropertyManager",
        "DeleteSelection2", "SetUserPreferenceInteger",
        "InsertSurfaceFinishSymbol3",
    },
    "sm": {
        "InsertSketch", "CreateCornerRectangle", "CreateCircleByRadius", "CreateLine",
        "CreateCenterLine", "CreateTangentArc", "CreatePoint", "CreatePolygon",
        "CreateSketchSlot", "CreateSpline", "ActiveSketch",
    },
    "fm": {
        "FeatureExtrusion2", "FeatureCut4", "FeatureExtrusionThin2", "FeatureRevolve2",
        "InsertProtrusionSwept3", "InsertProtrusionBlend2", "FeatureFillet3",
        "InsertFeatureChamfer", "InsertRefPlane", "InsertReferencePoint",
        "InsertCoordinateSystem", "FeatureLinearPattern5", "FeatureCircularPattern5",
        "InsertMirrorFeature2", "InsertDeleteBody", "InsertMultiFaceDraft",
        # sheet metal. The v1 of the base flange is DELIBERATE: the 19-arg v2 is a
        # silent no-op on SW 2017 (measured), and so is v1 of the convert with an edge
        # selected -- hence InsertConvertToSheetMetal2 and InsertSheetMetalBaseFlange.
        "InsertSheetMetalBaseFlange", "InsertConvertToSheetMetal2",
        "InsertSheetMetalEdgeFlange", "InsertSheetMetalMiterFlange",
        "InsertSheetMetalHem2", "InsertSheetMetal3dBend", "InsertSheetMetalCornerTrim",
        "InsertSheetMetalGussetFeature", "InsertSheetMetalLoftedBend",
        "InsertCrossBreak",
    },
    "part": {
        "GetBodies2", "SetMaterialPropertyName2", "GetMaterialPropertyName2",
        "InsertCombineFeature", "ExportFlatPatternView",
    },
    "selmgr": {"CreateSelectData"},
    "asm": {
        "AddComponent5", "GetComponentCount", "GetComponents", "FixComponent",
        "UnfixComponent", "ReplaceComponents2", "MirrorComponents2", "AddMate5",
        "AutoExplode", "ShowExploded", "GetExplodedViewNames",
        "InterferenceDetectionManager",
    },
    "dwg": {
        "GetCurrentSheet", "SetupSheet5", "NewSheet4", "ActivateSheet", "GetSheetNames",
        "CreateDrawViewFromModelView3", "ActivateView", "ActiveDrawingView",
        "GetFirstView", "InsertModelAnnotations3", "CreateSectionViewAt5",
        "AutoBalloon",
    },
}

# readable properties (the "get" op) -- they also apply to "$oN"/"@hN"
PROPERTIES: set[str] = {
    "Name", "Name2", "FullName", "Mass", "Volume", "SurfaceArea", "CenterOfMass",
    "GetTypeName2", "SystemValue", "Value", "Tolerance", "GetDimension2",
    "GetNextFeature", "Type",
}

# WRITABLE properties (the "set" op) -- a short, deliberate list
SETTABLE: set[str] = {"SystemValue", "Name", "Type", "Mark", "ScaleDecimal"}

# methods allowed on returned objects ($oN / @hN)
ON_OBJECT: set[str] = {
    # part / geometry
    "Name", "GetTypeName2", "GetNextFeature", "Select2", "Select4", "SetValues",
    "SetFitValues", "GetDimension2", "Add", "GetCount", "Equation", "EvaluateAll",
    "Add3", "Get5", "GetFaces", "GetEdges", "GetSurface", "GetCurve",
    "GetClosestPointOn", "IsPlane", "IsCylinder", "IsLine",
    # drawing: views, sheet, annotations
    "GetName", "GetName2", "SetScale", "ShowExploded", "IsExploded",
    "AutoInsertCenterMarks2", "InsertBomTable4", "GetAnnotation", "SetPosition",
    "SetLabel", "GetLabel", "SetFrameValues2", "SetFrameSymbols", "GetFrameValues",
}

# aggregate primitives implemented in the executor (bulk reads / loops)
HELPERS: set[str] = {
    # part / inspection
    "document",
    # title WITHOUT the extension: the qualified reference name uses the TREE's
    "doc_title",
    "resolve_plane", "list_features", "bodies", "faces", "edges", "edges_info",
    "faces_info", "find_face_at", "dimensions",
    "find_face", "find_edge", "select", "select_edges", "select_bodies",
    "select_sketch_entities",
    "combine", "relation", "spline", "screenshot", "equations", "close_other_doc",
    "load_component",
    # assembly (loops over components, transforms, interference detection)
    "comp_faces", "comp_box", "asm_find_face", "faces_perp", "components", "comp_info",
    "comp_by_name", "mates", "remove_component",
    "select_components", "last_mate_name", "mate_errors", "interferences",
    "free_translations", "cylinder_axis_world", "sweep_collision_angle",
    "mirror_components",
    # the assembly's WAY BACK (2026-08-20): deleting a mate does not bring the part
    # back, and until now no verb moved a component -- only remove and re-insert.
    "move_component",
    # drawing (annotation walks + paths of the LOCAL SolidWorks installation)
    "sheetformat_path", "delete_other_sheets", "view_names", "count_annotations",
    "pick_edge", "dedupe_dimensions", "view_center", "activate_view",
    "sheet_info",
    "import_model_dims",
    # SHEET METAL. The domain arrived with more primitives than the others because half
    # of its COM methods return None (or True) whether or not they built anything: the
    # verdict has to be GEOMETRIC, and a bundle cannot loop. `sm_modify` is the single
    # funnel for reading and writing a feature definition -- the executor uses
    # getattr/setattr with the interface and attribute specified in the bundle.
    "sm_names", "sm_feature", "sm_state", "sm_compare",
    "sm_base_face", "sm_free_edges", "sm_sharp_bend_edges", "sm_face_point",
    "sm_open_corners", "sm_bend_faces", "sm_edge_vertex",
    "sm_sketch_lines", "sm_flange_sketch", "sm_miter_profile",
    "sm_select_bends", "sm_select_segments", "sm_try_closed_corner",
    "sm_modify", "sm_allowance", "sm_restore_allowance", "sm_bend_info",
    "sm_sheet_bodies", "sm_cut_lists", "sm_export_flat",
}

# methods that require explicit confirmation because they are destructive
DESTRUCTIVE: set[str] = {"SaveAs", "CloseDoc", "Save3", "EditDelete", "InsertDeleteBody"}


class AllowlistError(PermissionError):
    """An op outside the local policy -- the whole bundle is refused."""


def check_bundle(bundle: dict) -> None:
    """Validate ALL the ops before running any of them (fail fast)."""
    for op in bundle.get("ops", []):
        if "helper" in op:
            if op["helper"] not in HELPERS:
                raise AllowlistError(f"helper not allowed: '{op['helper']}'")
            continue

        target = str(op.get("target", ""))
        is_object = target.startswith("$") or target.startswith("@")

        if "index" in op or "format" in op:
            continue                      # DATA ops: they do not touch COM

        if "call" in op:
            method = op["call"]
            if is_object:
                if method not in ON_OBJECT:
                    raise AllowlistError(
                        f"method not allowed on a returned object: '{method}'")
                continue
            allowed = METHODS.get(target)
            if allowed is None:
                raise AllowlistError(f"unknown target: '{target}'")
            if method not in allowed:
                raise AllowlistError(f"method outside the allowlist: {target}.{method}")
        elif "get" in op:
            if op["get"] not in PROPERTIES:
                raise AllowlistError(f"property outside the allowlist: '{op['get']}'")
        elif "set" in op:
            if op["set"] not in SETTABLE:
                raise AllowlistError(f"property is not writable: '{op['set']}'")
        else:
            raise AllowlistError(f"op with no call/get/set/helper: {op.get('id')}")


def destructive_ops(bundle: dict) -> list[str]:
    """Destructive ops present in the bundle (so confirmation can be required)."""
    return [op["call"] for op in bundle.get("ops", [])
            if op.get("call") in DESTRUCTIVE]
