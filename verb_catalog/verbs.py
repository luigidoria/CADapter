"""verbs.py -- the central registry of compiled SOLIDWORKS verbs.

Each verb emits an operation bundle for the executor, including COM call order,
arguments, selection marks and guards.

  verbs_part  PART + inspection
  verbs_asm   ASSEMBLY
  verbs_dwg   TECHNICAL DRAWING
  verbs_sm    SHEET METAL
"""

from __future__ import annotations

from .verbs_part import VerbError  # noqa: F401  (re-export: app.py imports it from here)
from .verbs_part import VERBS as _PART

VERBS: dict = {}
VERBS.update(_PART)

try:
    from .verbs_asm import VERBS as _ASM
    VERBS.update(_ASM)
except ImportError:  # pragma: no cover
    _ASM = {}

try:
    from .verbs_dwg import VERBS as _DWG
    VERBS.update(_DWG)
except ImportError:  # pragma: no cover
    _DWG = {}

try:
    from .verbs_sm import VERBS as _SM
    VERBS.update(_SM)
except ImportError:  # pragma: no cover
    _SM = {}


def _describe(fn) -> str:
    doc = (fn.__doc__ or "").strip()
    return doc.splitlines()[0] if doc else ""


CATALOG = {name: {"description": _describe(fn)} for name, fn in sorted(VERBS.items())}

DOMAINS = {
    "part": sorted(n for n in VERBS if n.startswith("part.")),
    "inspect": sorted(n for n in VERBS if n.startswith("inspect.")),
    "asm": sorted(n for n in VERBS if n.startswith("asm.")),
    "dwg": sorted(n for n in VERBS if n.startswith("dwg.")),
    "sheet": sorted(n for n in VERBS if n.startswith("sheet.")),
}


# Text a model writes meaning FALSE. The list is of synonyms, and not a `bool()`:
# `bool("False")` is True, and that was the trap.
_FALSE_WORDS = {"false", "0", "nao", "não", "no", "off", "n", ""}
_TRUE_WORDS = {"true", "1", "sim", "yes", "on", "s", "y"}


def _normalize_booleans(name: str, params: dict) -> dict:
    """A boolean that arrived as TEXT becomes a real boolean -- in one place only.

    The verbs read flags with `bool(p.get("through_all"))`, and `bool("False")` is
    **True**: the model asks for "not through all" and gets through all, with no error at
    all, just the wrong part. There are 20+ such sites in `verbs_part.py` alone, and
    fixing them one by one is exactly the defect that cost this project the most. So the 
    normalization lives in the FUNNEL every verb goes through, and it holds for every 
    surface: flattened typed tools (which already coerce on the host, but only what the 
    schema types) and Claude's `sw_verb` (which coerces nothing).

    Driven by the SCHEMA, never by a list of names: it only touches a parameter the verb
    itself declares as boolean. Text that is not yes/no passes through intact -- refusing
    it here would be inventing an error on a value the verb may well accept.
    """
    if not params:
        return params or {}
    from .schemas import SCHEMAS          # late import: schemas imports verbs
    props = (SCHEMAS.get(name) or {}).get("params") or {}
    out = dict(params)
    for key, value in params.items():
        if not isinstance(value, str) or props.get(key, {}).get("type") != "boolean":
            continue
        text = value.strip().lower()
        if text in _FALSE_WORDS:
            out[key] = False
        elif text in _TRUE_WORDS:
            out[key] = True
    return out


def compile_verb(name: str, params: dict) -> dict:
    fn = VERBS.get(name)
    if fn is None:
        nearest = [n for n in VERBS if name.split(".")[-1] in n]
        raise VerbError(
            f"unknown verb: '{name}'."
            + (f" Nearest: {nearest[:5]}" if nearest
               else f" Domains: { {k: len(v) for k, v in DOMAINS.items()} }"))
    return fn(_normalize_booleans(name, params or {}))
