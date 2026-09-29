"""
verbs_asm.py -- ASSEMBLY verbs compiled into bundles.

Port of `solidworks/sw_assembly.py`. The knowledge that stays here: the 7 mate types
in a single verb, the `swAddMateError_NoError == 1` gate (not 0!), the QUALIFIED name
of component references ('plane@comp-1@Assembly'), and the mechanical quality gates
(interferencia zero + zero mate over-defined).
"""

from __future__ import annotations

from .bundles import BundleBuilder as B
from .bundles import deg, mm
from .verbs_part import VerbError, _req

TPL_ASSEMBLY = 9
# swMateType_e
MATE_KINDS = {"coincident": 0, "concentric": 1, "perpendicular": 2, "parallel": 3,
              "tangent": 4, "distance": 5, "angle": 6}
# swMateAlign_e
ALIGN = {"aligned": 0, "anti": 1, "closest": 2}
# swAddMateError_e -- NoError == 1 (NOT zero); ANGLE returns 5 (OverDefined) even when it works
MATE_OK = [1, 5]
SAVE_SILENT = 1


def _comp(b: B, value) -> str:
    """The component by HANDLE or by instance NAME ('clevis-1').

    A handle is what this project takes out of the model's hands: it changes number, 
    says nothing and only holds within the step -- and in an assembly EVERY verb needs 
    one. The instance name, by contrast, shows up in `asm.components`, in the feature 
    tree and in the previous step report: an address the model can repeat.

    Measured 2026-08-19: `asm.component_box(comp='probe_base-1')` answered "an
    argument that has to be a CAD OBJECT came in as text ... the right move is almost
    always to OMIT that argument" -- wrong advice, because `comp` is mandatory and the
    assembly has more than one component.

    The decision is one of COMPILATION (the value is already known here); resolving the
    execution is the helper, where the real names exist.

    It takes the VALUE, not the dict: with `_req(p, "comp")` in there, reading the
    parameter disappeared from the verb body and the schema deducer (section 3) left
    `comp` OUT of the schema -- the compiler then refused the correct call. Measured while
    """
    if isinstance(value, str) and not value.startswith("@h"):
        return b.helper("comp_by_name", instance_name=value)
    return value


def _align(p: dict) -> int:
    a = str(p.get("align", "closest"))
    if a not in ALIGN:
        raise VerbError(f"align '{a}' invalid ({sorted(ALIGN)})")
    return ALIGN[a]


def _mate_guard(b: B, res: str, label: str) -> str:
    """Unpacks (mate, error) and applies the NoError==1 gate. Returns the mate name."""
    m = b.index(res, 0)
    err = b.index(res, 1)
    b.guard(m, "not_null", f"{label} failed (SW did not create the mate)")
    b.guard(err, "in",
            f"{label} failed (ErrorStatus not OK; remember: NoError==1, not 0)",
            value=MATE_OK)
    return b.helper("last_mate_name")


# ── document and components ───────────────────────────────────────────────────
def new_assembly(p: dict) -> dict:
    b = B("asm.new_assembly")
    tpl = b.call("app", "GetUserPreferenceStringValue", TPL_ASSEMBLY)
    b.guard(tpl, "nonempty", "no default assembly template configured in SolidWorks")
    b.call("app", "NewDocument", tpl, 0, 0, 0)
    return b.build(b.call("doc", "GetTitle"), p)


def add_component(p: dict) -> dict:
    """Insert a part saved on disk. Returns the component HANDLE (use it in the mates)."""
    path = str(_req(p, "path"))
    xyz = p.get("xyz") or [0.0, 0.0, 0.0]
    b = B("asm.add_component")
    # AddComponent5 returns Nothing if the part is not LOADED -> make sure it is loaded
    b.helper("load_component", path=path)
    raw = b.call("asm", "AddComponent5", path, 0, "", False, "",
                 mm(xyz[0]), mm(xyz[1]), mm(xyz[2]), cast="IComponent2")
    b.guard(raw, "not_null",
            f"add_component failed: {path} (does the file exist and is it a valid part/assembly?)")
    b.call("doc", "EditRebuild3")
    if p.get("fixed"):
        b.helper("select_components", comps=[raw])
        b.call("asm", "FixComponent")
        b.call("doc", "ClearSelection2", True)
    return b.build(B.handle(raw), p)


def component_count(p: dict) -> dict:
    b = B("asm.component_count")
    return b.build(b.call("asm", "GetComponentCount", bool(p.get("top_only", False))), p)


def components(p: dict) -> dict:
    """Lists the components (name + fixed) AND returns handles in the same call."""
    b = B("asm.components")
    comps = b.helper("components")
    return b.build({"info": b.helper("comp_info"), "handles": B.handle(comps)}, p)


def mates(p: dict) -> dict:
    """The mates the assembly ALREADY has: name, kind and error.

    `asm.mate_errors` shows only the broken ones -- and a list that only shows what
    went wrong makes the right ones look nonexistent. Without this one, the model saw
    no mate at all and recreated what was already there (the phantom step of 7k)."""
    b = B("asm.mates")
    return b.build(b.helper("mates"), p)


def fix(p: dict) -> dict:
    b = B("asm.fix")
    b.helper("select_components", comps=[_comp(b, _req(p, "comp"))])
    b.call("asm", "FixComponent")
    b.call("doc", "ClearSelection2", True)
    return b.build(True, p)


def float_(p: dict) -> dict:
    b = B("asm.float")
    b.helper("select_components", comps=[_comp(b, _req(p, "comp"))])
    b.call("asm", "UnfixComponent")
    b.call("doc", "ClearSelection2", True)
    return b.build(True, p)


def remove_component(p: dict) -> dict:
    """Remove the component from the assembly. Returns how many are LEFT.

    The removal is checked in the executor (the `remove_component` helper), not here:
    the old version selected, asked to delete and returned the count -- which could come
    IDENTICAL, proving in its own result that nothing had been deleted, and it still
    counted as success. Measured 2026-08-19: the model repeated the same removal eight
    times, all "successful", and the duplicated part stayed right there."""
    b = B("asm.remove_component")
    return b.build(b.helper("remove_component", comp=_comp(b, _req(p, "comp"))), p)


def suppress(p: dict) -> dict:
    state = bool(p.get("state", True))
    b = B("asm.suppress")
    b.helper("select_components", comps=[_comp(b, _req(p, "comp"))])
    ok = b.call("doc", "EditSuppress2" if state else "EditUnsuppress2")
    b.guard(ok, "truthy", f"suppress({state}) failed")
    b.call("doc", "ClearSelection2", True)
    return b.build(state, p)


def replace_component(p: dict) -> dict:
    b = B("asm.replace_component")
    b.helper("select_components", comps=[_comp(b, _req(p, "comp"))])
    ok = b.call("asm", "ReplaceComponents2", str(_req(p, "path")),
                str(p.get("config", "")), bool(p.get("all_instances", True)), False,
                bool(p.get("reattach_mates", True)))
    b.guard(ok, "truthy",
            "replace_component failed (does the replacement part have compatible mate references?)")
    b.call("doc", "EditRebuild3")
    b.call("doc", "ClearSelection2", True)
    return b.build(True, p)


def mirror_component(p: dict) -> dict:
    """GOTCHA: MirrorComponents2 requires ComponentOrientations of the SAME size as comps.

    `comps` has accepted an instance NAME since 2026-08-20. Until then this was the
    ONLY assembly write verb that still required a handle -- all the others were
    converted on 2026-08-19 (section 7i) and this one was left behind because the list
    passed straight to the helper, with no `_comp`. In practice that made it
    unreachable for the model: to mirror, it would have to call `asm.components`, keep
    handles and use them in the same step -- exactly what this project takes away.
    """
    comps = _req(p, "comps")
    if isinstance(comps, str):
        comps = [comps]          # 'pin-1' is a list of one, not 5 characters
    b = B("asm.mirror_component")
    b.call("doc", "ClearSelection2", True)
    real = b.helper("resolve_plane", plane=str(_req(p, "mirror_plane")))
    ok = b.call("ext", "SelectByID2", real, "PLANE", 0, 0, 0, False, 0, None, 0)
    b.guard(ok, "truthy", f"did not select plane '{p.get('mirror_plane')}'")
    b.helper("mirror_components", comps=[_comp(b, c) for c in comps])
    return b.build(b.call("asm", "GetComponentCount", True), p)


# -- selecting references for a mate -------------------------------------------
def find_face(p: dict) -> dict:
    """A COMPONENT face by geometry. radius_mm tells the hole from the fillet."""
    b = B("asm.find_face")
    ref = b.helper("asm_find_face", comp=_comp(b, _req(p, "comp")), kind=str(p.get("kind", "plane")),
                   axis=int(p.get("axis", 1)), want_max=bool(p.get("want_max", True)),
                   radius_mm=p.get("radius_mm"))
    b.guard(ref, "not_null", "no face of the component matches that criterion")
    return b.build(B.handle(ref), p)


def faces_perp(p: dict) -> dict:
    """Planar faces perpendicular to the axis, ORDERED by position (inner vs outer)."""
    b = B("asm.faces_perp")
    return b.build(B.handle(b.helper("faces_perp", comp=_comp(b, _req(p, "comp")),
                                     axis=int(p.get("axis", 0)))), p)


def component_box(p: dict) -> dict:
    """The component bounding box IN THE ASSEMBLY (min/max/center/size in mm).

    It is the positioning proof that does not depend on a mate: 'the tongue is centered
    in the slot', 'the pin goes through the whole U'. Without it the only option would
    be to trust the mate the assembly itself created.
    """
    b = B("asm.component_box")
    return b.build(b.helper("comp_box", comp=_comp(b, _req(p, "comp"))), p)


def cylinder_axis_world(p: dict) -> dict:
    """The cylindrical face axis in ASSEMBLY coordinates + its REAL radius (radius_mm).

    The radius comes along because it is what measures the clearance of an already
    assembled fit (hole radius - pin radius), without reopening the parts.
    """
    b = B("asm.cylinder_axis_world")
    ref = b.helper("cylinder_axis_world", comp=_comp(b, _req(p, "comp")),
                   radius_mm=p.get("radius_mm"))
    b.guard(ref, "not_null", "no cylindrical face with that radius on the component")
    return b.build(ref, p)


# ── mates ─────────────────────────────────────────────────────────────────────
# WHAT is being selected in the qualified name. Without this list the model wrote
# sel_type='axis' (lowercase) or invented 'AXIS_REF', and the error that came back was
# select", which sends you looking in the wrong place.
_SEL_TYPES = ("PLANE", "AXIS", "FACE", "EDGE", "VERTEX")

# orientation -> (axis, extreme): the same table as `part._FACES`, so the face
# vocabulary is ONE across both surfaces (the model already says 'top'/'front').
_LADOS = {"top": (1, True), "bottom": (1, False), "front": (2, True),
          "back": (2, False), "right": (0, True), "left": (0, False)}


def _face_do_comp(b: B, label: str, comp, side: str) -> str:
    """A component face by its SIDE, with the message that teaches in the right place."""
    axis, maior = _LADOS[side]
    ref = b.helper("asm_find_face", comp=_comp(b, comp), kind="plane",
                   axis=axis, want_max=maior, radius_mm=None)
    b.guard(ref, "not_null",
            f"mate_faces: the component of face_{label}='{side}' has no planar face "
            f"perpendicular to that axis. Check the component orientation in the "
            f"assembly (asm.component_box gives its box) or use asm.mate_planes with a "
            f"NAMED reference (a hole axis, a symmetry plane).")
    return ref


def mate_faces(p: dict) -> dict:
    """A mate between two faces found by ORIENTATION -- with no handle at all.

    The pair of `part.hole_on` for assembly, and it exists for the same reason
    as mating two parts required `asm.find_face` on each to get a HANDLE and
    only then `asm.mate` -- three calls, and handles that die between steps. Here the
    address is what the model already knows how to say: the component NAME and its SIDE.

        asm_mate_faces(comp_a='base-1', face_a='top',
                       comp_b='tampa-1', face_b='bottom', kind='coincident')

    `face_*` are the six orientations (top/bottom/front/back/right/left), measured in
    the assembly: 'top' is that component's highest planar face in Y WHERE IT IS.
    For a NAMED reference (a hole axis, a symmetry plane) use `asm.mate_planes`, which
    is more robust when one exists -- a face by extreme does not tell two faces at the
    altura."""
    kind = str(p.get("kind", "coincident"))
    if kind not in MATE_KINDS:
        raise VerbError(f"kind '{kind}' invalid. Opcoes: {sorted(MATE_KINDS)}")
    # The four parameters are read LITERALLY, one by one. In a loop
    # (`_req(p, f"face_{side}")`) the schema deducer (section 3) does not resolve the key
    # disappear from the schema -- the compiler then refuses the correct call for an
    # "unknown parameter". That is what happened in the first version of this verb.
    face_a = str(_req(p, "face_a")).lower()
    if face_a not in _LADOS:
        raise VerbError(f"face_a '{face_a}' invalid ({', '.join(sorted(_LADOS))})")
    face_b = str(_req(p, "face_b")).lower()
    if face_b not in _LADOS:
        raise VerbError(f"face_b '{face_b}' invalid ({', '.join(sorted(_LADOS))})")
    comp_a = _req(p, "comp_a")
    comp_b = _req(p, "comp_b")
    d, ang = mm(p.get("distance_mm", 0.0)), deg(p.get("angle_deg", 0.0))
    b = B("asm.mate_faces")
    b.call("doc", "ClearSelection2", True)
    # The two sides are written SEPARATELY on purpose. Sweeping
    # `for label, comp, side in (("a", comp_a, face_a), ...)` fazia o dedutor de schema
    # read the TUPLE as the shape of the data: `comp_a` came out "an array of triples of
    # `face_a`, "array of pairs" (section 3). A parameter inside a swept tuple is a trap.
    ref_a = _face_do_comp(b, "a", comp_a, face_a)
    ref_b = _face_do_comp(b, "b", comp_b, face_b)
    n = b.helper("select", entities=[ref_a, ref_b])
    b.guard(n, "equals", f"mate_faces {kind}: selection of the two faces failed", value=2)
    res = b.call("asm", "AddMate5", MATE_KINDS[kind], _align(p), False, d, d, d,
                 1, 1, ang, ang, ang, False, False, 0)
    name = _mate_guard(b, res, f"mate_faces {kind}")
    b.call("doc", "ClearSelection2", True)
    return b.build(name, p)


def mate(p: dict) -> dict:
    """One verb for all 7 types. refs = face/axis handles (asm.find_face)."""
    kind = str(p.get("kind", "coincident"))
    if kind not in MATE_KINDS:
        raise VerbError(f"kind '{kind}' invalid. Opcoes: {sorted(MATE_KINDS)}")
    d, a = mm(p.get("distance_mm", 0.0)), deg(p.get("angle_deg", 0.0))
    b = B("asm.mate")
    b.call("doc", "ClearSelection2", True)
    n = b.helper("select", entities=[_req(p, "ref_a"), _req(p, "ref_b")])
    b.guard(n, "equals", f"mate {kind}: reference selection failed", value=2)
    res = b.call("asm", "AddMate5", MATE_KINDS[kind], _align(p), False, d, d, d,
                 1, 1, a, a, a, False, False, 0)
    name = _mate_guard(b, res, f"mate {kind}")
    b.call("doc", "ClearSelection2", True)
    return b.build(name, p)


def limit_mate_faces(p: dict) -> dict:
    """LIMITS the travel between two faces found by SIDE -- with no handle at all.

    The pair of `asm.mate_faces` for MOTION. Limiting a part rotation required two
    `asm.find_face` calls to get handles and only then `limit_mate`; here the address is
    the component name and its side, which survives across steps.

        asm_limit_mate_faces(comp_a='garfo-1', face_a='top',
                             comp_b='lingueta-1', face_b='top',
                             kind='angle', min_deg=0, max_deg=30, nominal_deg=15)

    A LIMIT is not a lock: it lets the part rotate within the range and stops it
    from entering the other part material. The honest maximum angle comes from the
    measured collision (`asm.sweep_collision_angle`), not from a chosen number."""
    kind = str(p.get("kind", "angle"))
    if kind not in ("angle", "distance"):
        raise VerbError(f"kind '{kind}' invalid (angle|distance)")
    face_a = str(_req(p, "face_a")).lower()
    if face_a not in _LADOS:
        raise VerbError(f"face_a '{face_a}' invalid ({', '.join(sorted(_LADOS))})")
    face_b = str(_req(p, "face_b")).lower()
    if face_b not in _LADOS:
        raise VerbError(f"face_b '{face_b}' invalid ({', '.join(sorted(_LADOS))})")
    comp_a = _req(p, "comp_a")
    comp_b = _req(p, "comp_b")
    b = B("asm.limit_mate_faces")
    b.call("doc", "ClearSelection2", True)
    ref_a = _face_do_comp(b, "a", comp_a, face_a)
    ref_b = _face_do_comp(b, "b", comp_b, face_b)
    n = b.helper("select", entities=[ref_a, ref_b])
    b.guard(n, "equals", f"limit_mate_faces {kind}: selection of the two faces failed",
            value=2)
    return _limita(b, p, kind)


def limit_mate(p: dict) -> dict:
    """LIMITS the travel (does not lock the DOF) -- stops rotating/sliding into material."""
    kind = str(p.get("kind", "angle"))
    b = B("asm.limit_mate")
    b.call("doc", "ClearSelection2", True)
    n = b.helper("select", entities=[_req(p, "ref_a"), _req(p, "ref_b")])
    b.guard(n, "equals", "limit_mate: reference selection failed", value=2)
    return _limita(b, p, kind)


def _limita(b: B, p: dict, kind: str) -> dict:
    """The part both `limit_mate` verbs share: the AddMate5 with min/max."""
    flip = bool(p.get("flip", False))
    if kind == "angle":
        mind, maxd = float(p.get("min_deg", 0.0)), float(p.get("max_deg", 90.0))
        nom = deg(p.get("nominal_deg") if p.get("nominal_deg") is not None else mind)
        res = b.call("asm", "AddMate5", MATE_KINDS["angle"], _align(p), flip,
                     0.0, 0.0, 0.0, 1, 1, nom, deg(maxd), deg(mind), False, False, 0)
    elif kind == "distance":
        mind, maxd = float(p.get("min_mm", 0.0)), float(p.get("max_mm", 0.0))
        nom = mm(p.get("nominal_mm") if p.get("nominal_mm") is not None else mind)
        res = b.call("asm", "AddMate5", MATE_KINDS["distance"], _align(p), flip,
                     nom, mm(maxd), mm(mind), 1, 1, 0.0, 0.0, 0.0, False, False, 0)
    else:
        raise VerbError(f"kind '{kind}' invalid (angle|distance)")
    name = _mate_guard(b, res, f"limit_mate {kind}")
    b.call("doc", "ClearSelection2", True)
    return b.build(name, p)


def _qualified_ref(b: B, name: str, comp, comp_nome: str, title_ref: str,
                   side: str) -> tuple:
    """The qualified name 'reference@component@assembly'.

    Prefer passing the COMPONENT (`comp_a`/`comp_b`, the instance name or a handle): the
    name and the title come out at EXECUTION time, through the `format` op. The old
    (`comp_a_name` + `assembly_title`) still works, but it forces the caller to know the
    which component each datum belongs to -- and swapping the two only shows up as "did
    select', much later.

    The parameters are read BY THE VERB and arrive here as a value. While they were
    read in here through an f-string (`_req(p, f"name_{side}")`), the schema deducer
    resolve the key and `name_a`, `name_b`, `comp_a` and `comp_b` were left OUT of the
    that is, the only path to a mate by NAMED reference was invisible to the model.
    Measured 2026-08-19 in the `clevis` case: 15 calls in a row inventing names for
    parametro (`comp_ref`, `comp_type`, `comp_name`), nenhuma aceita.
    """
    if comp is not None:
        comp = _comp(b, comp)                     # handle OR instance name
        return (b.format("{0}@{1}@{2}", name, b.get(comp, "Name2"), title_ref),
                f"{name}@<{side}>")
    if not comp_nome:
        raise VerbError(
            f"mate_planes: you must say WHICH component reference '{name}' belongs to. "
            f"Pass comp_{side}='<instance name>' (what asm.components lists, "
            f"by exemplo 'garfo-1').")
    # /!\ `b.format` and NOT an f-string. `title_ref` is only a literal when the caller
    # passed `assembly_title`; otherwise it is the bundle reference that `doc_title`
    # resolves AT EXECUTION. An f-string froze that reference into the text, and what
    # reached SelectByID2 was `meio_base@cv_base-1@$o2` -- with the placeholder in it.
    # It failed with "did not select ... does the datum exist on THAT component?", which
    # sends you to the datum and the component, the two places that were fine. Measured
    # 2026-09-22, writing the first scenario that ever called this path.
    return (b.format("{0}@{1}@{2}", name, comp_nome, title_ref),
            f"{name}@{comp_nome}@<assembly>")


def mate_planes(p: dict) -> dict:
    """A mate between NAMED references of two components (an axis, a symmetry plane).

    It is the datum-to-mate workflow, and the most robust assembly path: an axis created by
    `part.axis_from_face` on the hole cannot be confused, whereas "the first
    cylindrical face" goes wrong as soon as the part gains a second hole.

        asm_mate_planes(comp_a='garfo-1', name_a='eixo_pivo',
                        comp_b='pino-1',  name_b='eixo_corpo',
                        kind='coincident', sel_type='AXIS')

    `name_*` is the datum name INSIDE that component; `comp_*` is the instance name.
    `sel_type` says what is being selected: AXIS for an axis, PLANE for a plane.
    SolidWorks GOTCHA: the qualified name requires the suffix with the assembly title --
    the compiler builds it at execution time, nobody needs to know that."""
    kind = str(p.get("kind", "coincident"))
    if kind not in MATE_KINDS:
        raise VerbError(f"kind '{kind}' invalid. Opcoes: {sorted(MATE_KINDS)}")
    # UPPERCASE: the model wrote sel_type='axis' and SelectByID2 matches nothing in
    # lowercase -- and the error comes out as "did not select", which sends you looking
    # wrong. Normalizing the shape is the funnel job (invariant 3).
    sel_type = str(p.get("sel_type", "PLANE")).upper()
    if sel_type not in _SEL_TYPES:
        raise VerbError(f"sel_type '{sel_type}' invalid ({', '.join(_SEL_TYPES)}). "
                        f"A reference axis is AXIS; a plane is PLANE.")
    name_a = str(_req(p, "name_a"))
    name_b = str(_req(p, "name_b"))
    comp_a = p.get("comp_a")
    comp_b = p.get("comp_b")
    comp_a_name = str(p.get("comp_a_name") or "")
    comp_b_name = str(p.get("comp_b_name") or "")

    b = B("asm.mate_planes")
    b.call("doc", "ClearSelection2", True)
    # `doc_title` and NOT `GetTitle`: measured 2026-08-20, GetTitle returns
    # "Assem98" on a new assembly and "tit2.sldasm" -- WITH the extension -- once saved,
    # and the qualified name uses the TREE name, with no extension. With GetTitle, this
    # verb worked on a new assembly and broke on the SAME assembly once saved.
    title = (str(p["assembly_title"]) if p.get("assembly_title")
             else b.helper("doc_title"))
    ra, rot_a = _qualified_ref(b, name_a, comp_a, comp_a_name, title, "a")
    rb, rot_b = _qualified_ref(b, name_b, comp_b, comp_b_name, title, "b")
    oka = b.call("ext", "SelectByID2", ra, sel_type, 0, 0, 0, False, 0, None, 0)
    b.guard(oka, "truthy",
            f"mate_planes: did not select '{rot_a}' (sel_type={sel_type}). O datum existe "
            f"on THAT component with that name? (swapping datum and component is the common cause)")
    okb = b.call("ext", "SelectByID2", rb, sel_type, 0, 0, 0, True, 0, None, 0)
    b.guard(okb, "truthy",
            f"mate_planes: did not select '{rot_b}' (sel_type={sel_type}). O datum existe "
            f"on THAT component with that name? (swapping datum and component is the common cause)")
    d = mm(p.get("distance_mm", 0.0))
    res = b.call("asm", "AddMate5", MATE_KINDS[kind], _align(p), False, d, d, d,
                 1, 1, 0.0, 0.0, 0.0, False, False, 0)
    name = _mate_guard(b, res, f"mate_planes {kind}")
    b.call("doc", "ClearSelection2", True)
    return b.build(name, p)


def mate_to_assembly(p: dict) -> dict:
    """ATENCAO: aterrar by 3 planos OVER-DEFINE -- prefira posicionar + asm.fix."""
    kind = str(p.get("kind", "coincident"))
    if kind not in MATE_KINDS:
        raise VerbError(f"kind '{kind}' invalid. Opcoes: {sorted(MATE_KINDS)}")
    ra = f"{_req(p, 'comp_ref')}@{_req(p, 'comp_name')}@{_req(p, 'assembly_title')}"
    b = B("asm.mate_to_assembly")
    b.call("doc", "ClearSelection2", True)
    oka = b.call("ext", "SelectByID2", ra, str(p.get("comp_type", "PLANE")),
                 0, 0, 0, False, 0, None, 0)
    b.guard(oka, "truthy", f"did not select the component reference '{ra}'")
    okb = b.call("ext", "SelectByID2", str(_req(p, "asm_ref")),
                 str(p.get("asm_type", "PLANE")), 0, 0, 0, True, 0, None, 0)
    b.guard(okb, "truthy", f"did not select the assembly reference '{p.get('asm_ref')}'")
    res = b.call("asm", "AddMate5", MATE_KINDS[kind], _align(p), False, 0.0, 0.0, 0.0,
                 1, 1, 0.0, 0.0, 0.0, False, False, 0)
    name = _mate_guard(b, res, f"mate_to_assembly {kind}")
    b.call("doc", "ClearSelection2", True)
    return b.build(name, p)


def delete_mate(p: dict) -> dict:
    b = B("asm.delete_mate")
    ok = b.call("ext", "SelectByID2", str(_req(p, "mate_name")), "MATE",
                0, 0, 0, False, 0, None, 0)
    b.guard(ok, "truthy", f"mate '{p.get('mate_name')}' not found")
    b.call("doc", "EditDelete")
    b.call("doc", "ClearSelection2", True)
    b.call("doc", "EditRebuild3")
    return b.build(True, p)


# ── mechanical quality gates ───────────────────────────────────────────────
def interferences(p: dict) -> dict:
    """Gate: an honest assembly has NO overlapping material. Ignores coincidences."""
    b = B("asm.interferences")
    return b.build(b.helper("interferences",
                            coincidence_counts=bool(p.get("coincidence_counts", False))), p)


def mate_errors(p: dict) -> dict:
    """The OVER-DEFINED gate -- catches what free_translations does not. Empty = healthy."""
    b = B("asm.mate_errors")
    return b.build(b.helper("mate_errors"), p)


def free_translations(p: dict) -> dict:
    """Empirical DOF by nudge. Reliable only for a part with NO free rotation."""
    b = B("asm.free_translations")
    return b.build(b.helper("free_translations", comp=_comp(b, _req(p, "comp")),
                            delta_mm=float(p.get("delta_mm", 3.0))), p)


def move_component(p: dict) -> dict:
    """Puts the component CENTER at xyz (mm) -- the assembly WAY BACK.

    Measured 2026-08-20: without it, a part put in the wrong place by a mate never came
    The model deleted the five mates, exploded, collapsed and called free_translations
    part did not move -- deleting a mate does not bring a component back.

    `xyz` is the same point `asm.add_component` uses and `asm.component_box` returns in
    `center_mm`: the BOX CENTER. Moving to xyz and re-inserting at xyz give the same
    result.

    Until 2026-08-22 this verb put the part ORIGIN at xyz while declaring it used
    the add_component reference -- and the difference between the two is the
    of the part (30 mm on the test pin, 40 mm on the plate). A silent error in 207 calls.
    """
    xyz = p.get("xyz") or [0.0, 0.0, 0.0]
    b = B("asm.move_component")
    out = b.helper("move_component", comp=_comp(b, _req(p, "comp")), xyz=xyz)
    b.call("doc", "EditRebuild3")
    return b.build(out, p)


def sweep_collision_angle(p: dict) -> dict:
    """Derives the angular limit by rotating through a transform (no over-defined mates)."""
    b = B("asm.sweep_collision_angle")
    return b.build(b.helper("sweep_collision_angle", comp=_comp(b, _req(p, "comp")),
                            axis_point_mm=_req(p, "axis_point_mm"),
                            axis_dir=_req(p, "axis_dir"),
                            step=int(p.get("step", 15)),
                            amax=int(p.get("amax", 170))), p)


# -- exploded view, rebuild and output ---------------------------------------
def explode(p: dict) -> dict:
    """AutoExplode and the only path in SW 2017 (AddExplodeStep and no-op via pywin32)."""
    b = B("asm.explode")
    ok = b.call("asm", "AutoExplode")
    b.guard(ok, "truthy", "AutoExplode failed (does the assembly have movable components?)")
    b.call("doc", "EditRebuild3")
    return b.build(b.call("asm", "GetExplodedViewNames"), p)


def collapse(p: dict) -> dict:
    b = B("asm.collapse")
    b.call("asm", "ShowExploded", False)
    b.call("doc", "EditRebuild3")
    return b.build(True, p)


def rebuild(p: dict) -> dict:
    b = B("asm.rebuild")
    return b.build(b.call("doc", "EditRebuild3"), p)


def save(p: dict) -> dict:
    path = str(p.get("path", "") or "")
    b = B("asm.save")
    if path:
        b.helper("close_other_doc", path=path)
        ok = b.call("doc", "SaveAs", path)
        b.guard(ok, "truthy", f"SaveAs failed: {path} (file open in another window?)")
        return b.build(path, p)
    b.call("doc", "Save3", SAVE_SILENT)
    return b.build(b.call("doc", "GetPathName"), p)


def export(p: dict) -> dict:
    """Parasolid/STEP for Mechanical (format from the extension)."""
    path = str(_req(p, "path"))
    b = B("asm.export")
    b.helper("close_other_doc", path=path)
    ok = b.call("doc", "SaveAs", path)
    b.guard(ok, "truthy", f"export failed: {path}")
    return b.build(path, p)


VERBS = {
    "asm.new_assembly": new_assembly, "asm.add_component": add_component,
    "asm.component_count": component_count, "asm.components": components, "asm.mates": mates,
    "asm.fix": fix, "asm.float": float_, "asm.remove_component": remove_component,
    "asm.suppress": suppress, "asm.replace_component": replace_component,
    "asm.mirror_component": mirror_component,
    "asm.find_face": find_face, "asm.faces_perp": faces_perp,
    "asm.cylinder_axis_world": cylinder_axis_world,
    "asm.component_box": component_box,
    "asm.mate": mate, "asm.mate_faces": mate_faces, "asm.limit_mate": limit_mate, "asm.limit_mate_faces": limit_mate_faces, "asm.mate_planes": mate_planes,
    "asm.mate_to_assembly": mate_to_assembly, "asm.delete_mate": delete_mate,
    "asm.interferences": interferences, "asm.mate_errors": mate_errors,
    "asm.free_translations": free_translations,
    "asm.move_component": move_component,
    "asm.sweep_collision_angle": sweep_collision_angle,
    "asm.explode": explode, "asm.collapse": collapse, "asm.rebuild": rebuild,
    "asm.save": save, "asm.export": export,
}
