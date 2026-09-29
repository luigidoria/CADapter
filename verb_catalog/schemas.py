"""schemas.py -- derives each verb JSON Schema by reading the verb code itself.

WHY THIS EXISTS
-------------------
The catalog only gives the NAME and the description of a verb. That is
enough for a large model, which holds the 100 verbs in context and gets the parameter
name right from memory.

With the parameter declared in the schema -- typed, and with an ENUM where a closed
list exists -- the same models get it right first time. So this module publishes the
schema of every verb, and a caller can build FLATTENED, TYPED tools from it.

The schema describes parameters. COM call order, arguments, selection marks and
guards are defined by the compiled bundle.

HOW IT WORKS
-------------
Static analysis (AST) of each verb body, looking for the patterns the verbs already
usam de shape uniforme:

    _req(p, "k")                -> required
    p["k"]                      -> required
    p.get("k", default)         -> optional, with a default (kind comes from the default)
    "k" in p                    -> opcional
    CONST[k] / k not in CONST   -> ENUM, with the keys of a module constant

Helpers (`_align(p)`, `_sketch_common(p)`) are followed one level down, which is as far
verbs usam hoje.

If a new verb turns up with no recognized default, it comes out with `params: {}` and
`complete: false` -- the caller falls back to the generic dispatcher for that verb. A
It degrades, it never invents.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

from .verbs import VERBS

# -- kind heuristics for a required parameter (no default to infer from) -----
_ARRAY = {
    "at", "p1", "p2", "xyz", "place_at", "edge_at", "axis_dir", "axis_point_mm",
    # `bodies` was missing: the `select_bodies` helper ITERATES the list, so receiving
    # the string
    # delete_body "passed" without deleting any body.
    "picks", "entities", "comps", "edges", "faces", "bodies", "types", "profiles",
    "datums", "sections", "values", "dims",
    # [x,y,z] points that break ties between coplanar faces. `point_mm` MUST be here:
    # _ARRAY is consulted before the `_mm` suffix, otherwise it would become "number" --
    # `face_point`, with no rule at all, fell back to the "string" default and the
    # compiler refused the list with "is not of type 'string'".
    "face_point", "point_mm",
    # SHEET METAL: `line` is the two points of a bend drawn in the model ([[x,y,z],
    # [x,y,z]]) and `bend_edges`/`bends` are lists the verb iterates. Without them the
    # first fell back to "string" and the compiler refused the list it was given.
    "line", "bend_edges", "bends", "sketches",
}
_BOOL = {
    "reverse", "merge", "fixed", "exploded", "all_views", "want_max", "holes",
    "fillets", "slots", "diameter", "aligned", "flip", "top_only", "state",
    "through_all", "coincidence_counts", "all_instances", "reattach_mates",
    "basic", "overwrite",
    # sheet metal
    "on", "keep_body", "draft", "use_offset", "remove_bends", "simplify",
    "corner_treatment", "fix_projected_length",
}
# array parameters whose ELEMENT is itself a point, and how many coordinates it has
_ARRAY_OF_POINTS = {"line": 3}
_NUMBER_SUFFIXES = ("_mm", "_deg", "_x", "_y", "_z")
_NUMBER_NAMES = {
    "radius", "scale", "depth", "ra", "num", "den", "decimals", "step", "amax",
    "top_k", "anchor", "axis", "angle", "distance", "count", "sides", "index",
    # sketch geometry: short names, always in mm
    "x", "y", "z", "r", "x1", "y1", "x2", "y2", "cx", "cy",
    "width", "height", "length", "depth_mm", "offset", "gap", "pitch",
    # sheet metal
    "k_factor", "bend", "corner", "relief_ratio", "fraction", "end",
}

# parameters that are a COM object handle ('@h3') -- worth telling the model
_HANDLE = {"comp", "ref_a", "ref_b", "face", "edge", "comps", "edges", "faces",
           "entities", "picks", "asm_ref", "comp_ref",
           # sheet metal: the eyes (sheet.free_edges, sheet.bend_faces,
           # sheet.sharp_bend_edges) answer in handles, and these are where they go back
           "fixed_face", "bend_edges", "body"}

# COMPONENT: since 2026-08-19 the assembly verbs resolve the instance name
# ('clevis-1') as well as the handle -- and the name is the address the model can repeat
# between steps, so it is the one the description offers first.
_COMPONENT = {"comp", "comp_a", "comp_b", "comps"}

# a `float(...)`/`str(...)` around the parameter read is EVIDENCE of kind, and it counts
# for more than a guess from the name: `part.scale` reads `float(_req(p,"factor"))` and
# even so `factor` came out as a string (it is in no name list), which made the compiler
    # refuse 0.5 with "is not of type 'string'".
_CASTS = {"float": "number", "int": "number", "str": "string", "bool": "boolean"}

# helper kwargs that take a LIST of COM objects. The helper ITERATES the value, so the
# parameter arriving there is an array of handles -- deduced from the usage, like the
# isto, `part.shell(remove_faces=...)` e `part.combine(tool_bodies=...)` pediam string.
_LIST_KW = {"entities", "bodies", "faces", "edges", "tools", "comps"}


def _type_from_default(v: Any) -> str | None:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, dict):
        return "object"
    return None


def _type_from_name(name: str) -> str:
    if name in _ARRAY:
        return "array"
    if name in _BOOL:
        return "boolean"
    if name in _NUMBER_NAMES or name.endswith(_NUMBER_SUFFIXES):
        return "number"
    return "string"


class _Collector(ast.NodeVisitor):
    """Walk a verb body collecting parameters, defaults and enums."""

    def __init__(self, constants: dict[str, list[str]]):
        self.constants = constants
        self.required: set[str] = set()
        self.conditional_params: set[str] = set()
        self.optional_params: dict[str, Any] = {}
        # enums come from two sources, and the difference matters:
        #   `enums`     AUTHORITATIVE -- the keys of a dict indexed by the parameter
        #               (`MATE_KINDS[kind]`). The list is complete; it becomes an
        #               `enum` in the JSON Schema, and forbidding what is outside is right.
        #   `suggested`  INFERRED from loose comparisons (`if kind == "cut"`). It shows
        #               the branches as written, so it may be INCOMPLETE -- it becomes a
        #               hint in the description, never an `enum`, so a valid value is not
        self.enums: dict[str, list[str]] = {}
        self.suggested: dict[str, list[str]] = {}
        self.candidates: dict[str, set[str]] = {}   # raw, before becoming a suggestion
        # structural shape deduced from USAGE: list, list of tuples, map or text
        self.shapes: dict[str, tuple[str, int]] = {}
        self.casts: dict[str, str] = {}        # param -> kind, by float()/str()/...
        self.item_kinds: dict[str, str] = {}        # a `lst` param -> the ELEMENT's kind
        self._loop_vars: dict[str, str] = {}   # the loop variable -> the param iterated
        # a conditional param -> under which condition it is required
        self.conditional_on: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        self._guard: list[tuple[str, tuple[str, ...]]] = []
        self.helpers: set[str] = set()
        self._locals: dict[str, str] = {}     # a local variable -> the parameter's name
        self._branch = 0                      # >0 = inside if/try/for/while

    # A shape can be deduced from TWO pieces of evidence that disagree, and the stronger
    # one has to win. `part.shell` does `isinstance(remove_faces, str)` -- only to
    # TOLERATE somebody passing a loose handle -- and right afterwards passes the list to
    # iterates. With no ordering, `text` arrived first and the schema went back to
    # asking for a string,
    _FORCE = {"text": 1, "lst": 2, "list_of_tuples": 3, "map": 3}

    def _record_shape(self, param: str, shape: tuple[str, int]) -> None:
        current = self.shapes.get(param)
        if current is None or self._FORCE.get(shape[0], 0) > self._FORCE.get(current[0], 0):
            self.shapes[param] = shape

    def _require(self, key: str) -> None:
        """A mandatory read. Inside a branch, it only holds CONDITIONALLY.

        `part.sketch` reads x1/y1/x2/y2 in the 'rect' branch and r/cx/cy in the
        'circle' one: marking both groups as required would make the model fill in
        satisfazer o schema.

        But merely saying "conditional" is not enough -- that was the defect that brought
        whole. The tool announced "parameters used as the case requires: x1, x2,
        WHICH goes with WHICH shape, and the model asked for shape='rect' with no
        x1. That is why we also record the branch CONDITION.
        """
        if self._branch:
            self.conditional_params.add(key)
            if self._guard:
                self.conditional_on.setdefault(key, []).extend(self._guard)
        else:
            self.required.add(key)

    def _discriminator(self, cond_node) -> tuple[str, tuple[str, ...]] | None:
        """`if shape in ("rect","rectangle")` -> ('shape', ('rect','rectangle'))."""
        if not isinstance(cond_node, ast.Compare) or len(cond_node.ops) != 1:
            return None
        if not isinstance(cond_node.ops[0], (ast.In, ast.Eq)):
            return None
        try:
            value = ast.literal_eval(cond_node.comparators[0])
        except (ValueError, SyntaxError):
            return None
        options = ((value,) if isinstance(value, str)
                  else tuple(v for v in value if isinstance(v, str))
                  if isinstance(value, (list, tuple, set)) else ())
        param = self._param_of(cond_node.left)
        return (param, options) if param and options else None

    # -- recognizes an expression that READS a parameter, returning its name --
    # It searches the SUBTREE: the verbs wrap the read (`str(p.get(...)).lower()`,
    # `mm(p.get(...))`), so looking only at the top node misses almost everything.
    def _param_of(self, node) -> str | None:
        if node is None:
            return None
        for n in ast.walk(node) if not isinstance(node, ast.Name) else [node]:
            if isinstance(n, ast.Call):
                f = n.func
                if (isinstance(f, ast.Name) and f.id == "_req" and len(n.args) >= 2
                        and isinstance(n.args[1], ast.Constant)):
                    self._require(n.args[1].value)
                    return n.args[1].value
                if (isinstance(f, ast.Attribute) and f.attr == "get"
                        and isinstance(f.value, ast.Name) and f.value.id == "p"
                        and n.args and isinstance(n.args[0], ast.Constant)):
                    key = n.args[0].value
                    default = None
                    if len(n.args) > 1:
                        try:
                            default = ast.literal_eval(n.args[1])
                        except (ValueError, SyntaxError):
                            default = None
                    self.optional_params.setdefault(key, default)
                    return key
            if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                    and n.value.id == "p" and isinstance(n.slice, ast.Constant)
                    and isinstance(n.slice.value, str)):
                self._require(n.slice.value)
                return n.slice.value
            if isinstance(n, ast.Name) and n.id in self._locals:
                return self._locals[n.id]
        return None

    # -- branch depth: what is read inside an `if` is conditional --
    def _in_branch(self, node):
        self._branch += 1
        self.generic_visit(node)
        self._branch -= 1

    visit_Try = visit_While = _in_branch

    def visit_If(self, node):
        """Descend into the branch carrying WHICH condition guards it."""
        self.visit(node.test)                     # the test runs at the outer level
        disc = self._discriminator(node.test)
        if disc:
            self._guard.append(disc)
        self._branch += 1
        for child in node.body:
            self.visit(child)
        self._branch -= 1
        if disc:
            self._guard.pop()
        self._branch += 1
        for child in node.orelse:                 # the else does not inherit the condition
            self.visit(child)
        self._branch -= 1

    def visit_For(self, node):
        """`for (x, y) in positions:` says positions is a LIST OF PAIRS.

        Without this the parameter falls back to kind-by-name and becomes `string` --
        which is what happened to `part.hole`, which then asked the model for text
        instead of coordinates. Deducing from usage beats a fixed list of names, which
        would go silent again on the next verb with a sequence parameter.
        """
        # `for i in range(sides):` iterates a COUNT, not the parameter. Without this the
        # count was declared an array of numbers and the schema asked the model for a
        # list where the verb wants "how many" (measured on `sheet.box_flanges`).
        if (isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "range"):
            self._in_branch(node)
            return
        param = self._param_of(node.iter)
        tgt = node.target
        # `for i, sk in enumerate(sketches)`: the pair is NOT the element shape, it is
        # the (index, item) enumerate itself produces. Without this exception `loft`
        # declared `profile_sketches` as a list of PAIRS OF NUMBERS -- and it receives
        # sketch names.
        if (isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "enumerate"
                and isinstance(tgt, ast.Tuple) and len(tgt.elts) == 2):
            tgt = tgt.elts[1]
        if param:
            if isinstance(tgt, ast.Name):
                # the element gets a name: what they do with it (str(sk), float(v))
                # says the kind of the list ITEMS
                self._loop_vars[tgt.id] = param
            if isinstance(tgt, ast.Tuple):
                self._record_shape(param, ("list_of_tuples", len(tgt.elts)))
            else:
                # `for xy in pts:` with `xy[0]`/`xy[1]` in the body is also a list of
                # pairs -- just written with an index instead of unpacking. Without
                # looking at the body, the spline `points` came out as a list of NUMBERS
                # the compiler refused `[[0,0],[30,10]]` with "[0, 0] is not of type
                # The intent is the same; only the writer's style changes.
                arity = self._max_index(node)
                self._record_shape(param, ("list_of_tuples", arity) if arity
                                      else ("lst", 0))
        self._in_branch(node)

    @staticmethod
    def _max_index(node: ast.For) -> int:
        """Arity deduced from `target[0]`, `target[1]`... inside the loop (0 if none)."""
        if not isinstance(node.target, ast.Name):
            return 0
        tgt, highest = node.target.id, -1
        for child in ast.walk(node):
            if (isinstance(child, ast.Subscript)
                    and isinstance(child.value, ast.Name) and child.value.id == tgt
                    and isinstance(child.slice, ast.Constant)
                    and isinstance(child.slice.value, int)):
                highest = max(highest, child.slice.value)
        return highest + 1 if highest >= 0 else 0

    def _options_of(self, node) -> list[str] | None:
        """A CLOSED, authoritative list of options, if this node is one.

        A module const (`MATE_KINDS`) counts, and so does the literal dict
        written inline -- `{"add": .., "subtract": .., "common": ..}.get(...)`, which is
        how `part.combine` does it. Without the literal, the enum would come from loose
        and it would be INCOMPLETE (it would lose 'common'), forbidding a valid value.
        """
        if isinstance(node, ast.Name):
            return self.constants.get(node.id)
        if isinstance(node, ast.Dict) and node.keys:
            keys = [k.value for k in node.keys
                      if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            return sorted(keys) if len(keys) == len(node.keys) else None
        return None

    def _record_enum(self, param: str | None, source) -> None:
        options = self._options_of(source) if not isinstance(source, list) else source
        if param and options:
            self.enums.setdefault(param, options)

    @staticmethod
    def _has_builder(node) -> bool:
        """Does the expression pass through a bundle op (`b.call`, `b.helper`, `b.get`)?"""
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and isinstance(n.func.value, ast.Name) and n.func.value.id == "b"
                   for n in ast.walk(node))

    # -- x = p.get(...) : binds the local variable to the parameter --
    def visit_Assign(self, node):
        # It only fires when the variable CARRIES the parameter value (`kind =
        # str(p.get("kind"))`). `arestas = b.helper("edges", body=p.get("body"))`
        # holds the RESULT of an op -- and tying `edges` to `body` made the following
        # `entities=edges` declare `body` as a LIST of handles. Same with
        # `ents = [_req(p,"origin")]`, where the list is built here and the parameter
        # and ONE element of it.
        derived = not (isinstance(node.value, (ast.List, ast.Tuple, ast.Dict))
                        or self._has_builder(node.value))
        param = self._param_of(node.value) if derived else None
        if param:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self._locals[tgt.id] = param
        self.generic_visit(node)

    # -- CONST[param] / {"a":..}[param] : enum --
    def visit_Subscript(self, node):
        if self._options_of(node.value):
            self._record_enum(self._param_of(node.slice), node.value)
        self._param_of(node)
        self.generic_visit(node)

    # -- `param not in CONST` / `"k" in p` / `param in ("a","b")` / `param == "a"` --
    def visit_Compare(self, node):
        if len(node.ops) == 1:
            op, right = node.ops[0], node.comparators[0]
            if isinstance(op, (ast.In, ast.NotIn)):
                if isinstance(right, ast.Name) and right.id == "p":
                    if isinstance(node.left, ast.Constant):
                        self.optional_params.setdefault(node.left.value, None)
                elif self._options_of(right):
                    self._record_enum(self._param_of(node.left), right)
                else:
                    # `if s in ("rect", "rectangle")`: varios verbs despacham by
                    # a literal tuple instead of the module constant. Union of the branches.
                    self._candidates_from(node.left, right)
            elif isinstance(op, (ast.Eq, ast.NotEq)):
                self._candidates_from(node.left, right)
        self.generic_visit(node)

    def _candidates_from(self, tgt, values) -> None:
        try:
            v = ast.literal_eval(values)
        except (ValueError, SyntaxError):
            return
        options = ([v] if isinstance(v, str)
                  else list(v) if isinstance(v, (list, tuple, set)) else [])
        options = [o for o in options if isinstance(o, str)]
        if not options:
            return
        param = self._param_of(tgt)
        if param:
            self.candidates.setdefault(param, set()).update(options)

    def visit_Call(self, node):
        f = node.func
        # CONST.get(param) / {"a":..}.get(param) -- a lookup in a closed list,
        # BUT only when there is no fallback. `MATERIALS.get(name, name)` returns the
        # parameter itself when it does not find one: it is a NORMALIZATION table with
        # free passage, not a closed domain. Treating that as an enum forbade the real
        # SolidWorks names ('1060 Alloy'), which is exactly what the table does not cover.
        if (isinstance(f, ast.Attribute) and f.attr == "get"
                and node.args and self._options_of(f.value)):
            tgt = self._param_of(node.args[0])
            if len(node.args) > 1:
                options = self._options_of(f.value) or []
                if tgt and options:
                    self.candidates.setdefault(tgt, set()).update(options)
            else:
                self._record_enum(tgt, f.value)

        # `param.items()` and `isinstance(param, dict)` -> the parameter is a MAP
        if isinstance(f, ast.Attribute) and f.attr in ("items", "keys", "values"):
            tgt = self._param_of(f.value)
            if tgt:
                self._record_shape(tgt, ("map", 0))
        if (isinstance(f, ast.Name) and f.id == "isinstance" and len(node.args) == 2
                and isinstance(node.args[1], ast.Name)):
            # the verb asks itself what arrived: the most direct evidence there is.
            # `pattern_circular` does `isinstance(axis, str)` because it accepts the axis
            # NAME -- and even so `axis` came out 'number', from the name (which in
            # `inspect.faces` really is a number, 0..2). The usage breaks the tie.
            tgt = self._param_of(node.args[0])
            shape = {"dict": ("map", 0), "str": ("text", 0),
                     "list": ("lst", 0), "tuple": ("lst", 0)}.get(node.args[1].id)
            if tgt and shape:
                self._record_shape(tgt, shape)

        # float(param) / str(param) / int(param) / bool(param) -- kind from USAGE
        if isinstance(f, ast.Name) and f.id in _CASTS and len(node.args) == 1:
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in self._loop_vars:
                self.item_kinds.setdefault(self._loop_vars[arg.id], _CASTS[f.id])
            else:
                tgt = self._param_of(arg)
                if tgt:
                    self.casts.setdefault(tgt, _CASTS[f.id])

        # helper(entities=param) -- the helper ITERATES, so param is a list of handles
        for kw in node.keywords:
            if kw.arg not in _LIST_KW or isinstance(kw.value, (ast.List, ast.Tuple)):
                continue                  # `entities=[target]` passes ONE, not a list
            tgt = self._param_of(kw.value)
            if tgt:
                self._record_shape(tgt, ("lst", 0))
                self.item_kinds.setdefault(tgt, "string")
        self._param_of(node)
        # helper(p) -- the parameters may be inside it
        if (isinstance(f, ast.Name) and f.id.startswith("_")
                and any(isinstance(a, ast.Name) and a.id == "p" for a in node.args)):
            self.helpers.add(f.id)
        self.generic_visit(node)


def _module_constants(tree: ast.Module) -> dict[str, list[str]]:
    """Module constants that are a closed list of options (they become an ENUM)."""
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        tgts = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not tgts or not tgts[0].isupper():
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, dict) and value and all(isinstance(k, str) for k in value):
            out[tgts[0]] = sorted(value)
        elif (isinstance(value, (list, tuple)) and value
                and all(isinstance(v, str) for v in value)):
            out[tgts[0]] = sorted(value)
    return out


def _functions_and_constants(module) -> tuple[dict[str, ast.FunctionDef], dict[str, list[str]]]:
    tree = ast.parse(inspect.getsource(module))
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    return funcs, _module_constants(tree)


def _verb_schema(fn) -> dict:
    module = inspect.getmodule(fn)
    try:
        funcs, constants = _functions_and_constants(module)
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return {"params": {}, "required": [], "complete": False}

    c = _Collector(constants)
    c.visit(tree)
    for helper in sorted(c.helpers):            # one level of helper
        tgt = funcs.get(helper)
        if tgt is None:
            continue
        c2 = _Collector(constants)
        # `_draw(b, shape, p)` receives the value ALREADY extracted from p. Inside the
        # helper the validation is `if shape not in SKETCH_SHAPES`, over the argument --
        # without this seeding the enum of the verb most important parameter would be lost.
        c2._locals = {a.arg: a.arg for a in tgt.args.args if a.arg not in ("b", "p")}
        c2.visit(tgt)
        c.required |= c2.required
        c.conditional_params |= c2.conditional_params
        for k, v in c2.optional_params.items():
            c.optional_params.setdefault(k, v)
        for k, v in c2.enums.items():
            c.enums.setdefault(k, v)
        for k, v in c2.candidates.items():
            c.candidates.setdefault(k, set()).update(v)
        for k, v in c2.shapes.items():
            c.shapes.setdefault(k, v)
        for k, v in c2.casts.items():
            c.casts.setdefault(k, v)
        for k, v in c2.item_kinds.items():
            c.item_kinds.setdefault(k, v)
        for k, v in c2.conditional_on.items():
            c.conditional_on.setdefault(k, []).extend(v)

    # an enum inferred from literals: it only counts with 2+ options (a lone
    # `kind == "cut"` would badly describe a parameter that also accepts "boss"). The
    # default joins the list, which is exactly the other side of this if.
    for name, options in c.candidates.items():
        if name in c.enums:
            continue
        complete = set(options)
        default = c.optional_params.get(name)
        if isinstance(default, str):
            complete.add(default)
        if len(complete) >= 2:
            c.suggested[name] = sorted(complete)

    # a parameter with an explicit default is OPTIONAL, even if it also appears
    # read as p["k"] in another branch of the verb.
    required = sorted(c.required - set(c.optional_params))
    conditionals = sorted(c.conditional_params - set(c.optional_params) - set(required))

    params: dict[str, dict] = {}
    for name in required + conditionals + sorted(c.optional_params):
        is_optional = name in c.optional_params
        default = c.optional_params.get(name)
        kind = ((_type_from_default(default) if is_optional else None)
                or c.casts.get(name) or _type_from_name(name))
        if name in c.enums or name in c.suggested:
            kind = "string"
        # the shape deduced from USAGE wins: it came from how the verb consumes the
        # value, not from a guess based on the name.
        shape, arity = c.shapes.get(name, ("", 0))
        if shape == "map":
            kind = "object"
        elif shape == "text":
            kind = "string"
        elif shape:
            kind = "array"
        spec: dict[str, Any] = {"type": kind}
        if shape == "map":
            spec["additionalProperties"] = {"type": "number"}
        elif shape == "list_of_tuples":
            spec["items"] = {"type": "array", "items": {"type": "number"},
                             "minItems": arity, "maxItems": arity}
        if name in c.enums:
            spec["enum"] = c.enums[name]
        if is_optional and default is not None:
            spec["default"] = default
        # it only guesses `items` if the shape deduced from usage has not already set it.
        # The ELEMENT kind also comes from usage where possible (`str(sk)` inside the
        # loop); the name list only kicks in when there was no evidence at all.
        if kind == "array" and "items" not in spec:
            deduced = c.item_kinds.get(name)
            if name in _ARRAY_OF_POINTS:
                # the element is itself a POINT: `line` is TWO points of THREE
                # coordinates, and declaring it a list of numbers would have the model
                # send six loose values
                arity = _ARRAY_OF_POINTS[name]
                spec["items"] = {"type": "array", "items": {"type": "number"},
                                 "minItems": arity, "maxItems": arity}
            else:
                spec["items"] = {"type": deduced} if deduced else (
                    {"type": "number"} if name not in {
                        "picks", "entities", "comps", "edges", "faces", "types",
                        "profiles", "datums",
                        # sheet metal: bend NAMES and bend EDGE handles, both text
                        "bends", "bend_edges", "sketches"} else {"type": "string"})
        description = []
        if name in c.suggested:
            description.append("known options: " + " | ".join(c.suggested[name]))
        if name == "plane":
            # Without this the model invented 'Front Plane', '@FrontPlane' and
            # '@OriginPlane1' (measured). It does not become an enum because the literal
            description.append(
                "use 'Front', 'Top' or 'Right' -- they are resolved by the REAL name in "
                "the tree, including on a pt-BR template. Alternative: the literal plane "
                "name. NEVER prefix it with '@' (that is a handle, not a plane)")
        if name.endswith("_mm"):
            description.append("in millimeters")
        elif name.endswith("_deg"):
            description.append("in degrees")
        if name in _COMPONENT and "enum" not in spec:
            # the component is the only "handle" with a stable ADDRESS: the instance name
            # shows up in `asm.components`, in the tree and in the previous step report --
            # so the description leads with it, and the handle comes as the alternative.
            plural = name == "comps"
            description.append(
                ("a LIST of instance names, as they appear in asm.components "
                 "(['clevis-1', 'pin-1']) -- even for a single one; handles work too"
                 if plural else
                 "the component INSTANCE NAME, as it appears in "
                 "asm.components ('garfo-1'); handle ('@h3') also works"))
        elif name in _HANDLE and "enum" not in spec:
            # with `enum` the domain is CLOSED (hole_on `face` only accepts
            # top/front/...), and promising a handle there would be lying to the model --
            # the same contradiction that already bit us the other way in `sketch_begin`.
            description.append("accepts a COM object handle ('@h3')")
        if name in conditionals:
            description.append("required depending on the other parameters")
        if description:
            spec["description"] = "; ".join(description)
        params[name] = spec

    # "when X is Y, these parameters are required" -- it is what was missing for the
    # model to build the right call first time.
    combinations: dict[str, dict[str, list[str]]] = {}
    for name in conditionals:
        for disc, values in c.conditional_on.get(name, []):
            key = "|".join(sorted(values))
            combinations.setdefault(disc, {}).setdefault(key, [])
            if name not in combinations[disc][key]:
                combinations[disc][key].append(name)
    for disc in combinations:
        for key in combinations[disc]:
            combinations[disc][key].sort()

    return {"params": params, "required": required,
            "conditionals": conditionals, "combinations": combinations,
            "complete": bool(params) or _no_parameters(fn)}


def _no_parameters(fn) -> bool:
    """A verb that legitimately has no parameter (new_part, mass, explode...)."""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    return "p.get(" not in src and "_req(" not in src and 'p["' not in src


def build() -> dict:
    return {name: _verb_schema(fn) for name, fn in sorted(VERBS.items())}


SCHEMAS = build()
