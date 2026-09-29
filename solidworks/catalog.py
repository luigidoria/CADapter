# -*- coding: utf-8 -*-
"""
catalog.py -- the INDEX of the curated verbs, for whoever needs to call them by NAME.

Until now the curated layer was only reachable by writing a Python script and running it
through the venv: `from solidworks import sw_sheetmetal as SM; SM.box_flanges(20)`. That
left the curated verbs out of reach of any surface that speaks name +
parameters -- the local MCP server first of all. This module closes that gap for all FOUR domains at once,
because the gap was never about sheet metal: it was about all of them.

What it is NOT: a second implementation of the verbs. It reflects over the modules that
already exist -- a public function of a curated module becomes a verb, with the signature
and the docstring it already has. A new verb in `sw_sheetmetal.py` shows up here by
itself, with no list to maintain in two places (which is how two lists come to diverge).

  part.*     sw_parts        part
  sketch.*   sw_sketch       low-level sketching
  inspect.*  sw_inspect      inspection and validation
  asm.*      sw_assembly     assembly
  dwg.*      sw_drawing      technical drawing
  sheet.*    sw_sheetmetal   sheet metal

Units: the SAME as the verbs (mm and degrees at the boundary). Nothing is converted here.
"""

from __future__ import annotations

import difflib as _difflib
import inspect as _inspect

from . import sw_assembly, sw_drawing, sw_inspect, sw_parts, sw_sheetmetal, sw_sketch

MODULES = {
    "part": sw_parts,
    "sketch": sw_sketch,
    "inspect": sw_inspect,
    "asm": sw_assembly,
    "dwg": sw_drawing,
    "sheet": sw_sheetmetal,
}

DOMAINS = tuple(MODULES)


class VerbError(ValueError):
    """Invalid request (verb or parameter) -- the CALLER's error, not SolidWorks'.

    Kept separate from the COM error on purpose: the caller needs to tell "you asked for
    the wrong thing" (fix it and call again) from "SW refused" (go investigate the
    geometry).
    """


def _public_functions(mod):
    """Public functions DEFINED in the module (not the ones it imported).

    The `__module__` filter matters: `sw_parts` imports `sw_com`, `sw_sketch` and
    `sw_inspect`, and without it the same verb would show up in three domains under
    different names -- three ways to call the same thing, which is how a catalog loses
    the trust of whoever reads it.
    """
    return {name: fn for name, fn in vars(mod).items()
            if _inspect.isfunction(fn) and not name.startswith("_")
            and fn.__module__ == mod.__name__}


def _build() -> dict:
    out = {}
    for dom, mod in MODULES.items():
        for name, fn in _public_functions(mod).items():
            out[f"{dom}.{name}"] = fn
    return out


VERBS = _build()


# ── description and signature (what a catalog has to deliver) ────────────────
def _summary(fn) -> str:
    doc = (fn.__doc__ or "").strip()
    return doc.splitlines()[0].strip() if doc else ""


def _type_name(annotation) -> str:
    if annotation is _inspect.Parameter.empty:
        return ""
    return getattr(annotation, "__name__", str(annotation))


def parameters(name: str) -> list:
    """[{name, type, required, default}] in the ORDER of the verb's signature."""
    fn = verb(name)
    out = []
    for p in _inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        out.append({
            "name": p.name,
            "type": _type_name(p.annotation),
            "required": p.default is _inspect.Parameter.empty,
            "default": None if p.default is _inspect.Parameter.empty else p.default,
        })
    return out


def verb(name: str):
    fn = VERBS.get(name)
    if fn is None:
        raise VerbError(f"unknown verb: '{name}'. {_suggest(name)}")
    return fn


def _suggest(name: str) -> str:
    """A message that TEACHES the way out of the hole (this project's rule for guards):
    say the next step, not just what broke."""
    short = name.split(".")[-1].lower()
    # contains FIRST (it catches the partial name: "flange" -> the four flanges), and
    # only then fuzzy -- contains alone misses the most common case, the extra letter.
    nearest = sorted(n for n in VERBS if short and short in n.lower())
    nearest += [n for n in _difflib.get_close_matches(name, VERBS, n=6, cutoff=0.6)
                if n not in nearest]
    if nearest:
        return f"Nearest: {nearest[:6]}"
    return (f"Domains: {dict((d, len(list_verbs(d))) for d in DOMAINS)}. "
            f"Call the catalog with no filter to see the names.")


def list_verbs(domain: str = "") -> list:
    """Verb names, optionally for a single domain."""
    if not domain:
        return sorted(VERBS)
    if domain not in MODULES:
        raise VerbError(f"domain '{domain}' does not exist. Options: {list(DOMAINS)}")
    return sorted(n for n in VERBS if n.startswith(domain + "."))


def catalog(domain: str = "") -> dict:
    """{verb: summary} -- the single docstring line of each verb."""
    return {n: _summary(VERBS[n]) for n in list_verbs(domain)}


def help_for(name: str) -> dict:
    """FULL docstring + parameters of a verb. This is where the measured gotchas live:
    `jog`'s docstring explains that the angle goes in DEGREES, `set_k_factor`'s that K
    only changes through the FLANGE. Reading this before calling saves the measurement
    round that wrote the docstring."""
    fn = verb(name)
    return {"verb": name, "domain": name.split(".")[0],
            "doc": (fn.__doc__ or "").strip(),
            "parameters": parameters(name)}


# ── parameter normalization ───────────────────────────────────────────────────
# Text a model writes meaning FALSE. It is a synonym list, and not a `bool()`, because
# `bool("False")` is True: a request for "not through all" would silently become through
# all -- no error, just the wrong part. The same defect already cost dearly on the cloud
# side (verb_catalog/verbs.py) -- here the list lives in the funnel, not in each verb.
_FALSE_WORDS = {"false", "0", "nao", "não", "no", "off", "n", ""}
_TRUE_WORDS = {"true", "1", "sim", "yes", "on", "s", "y"}


def _coerce(fn, params: dict) -> dict:
    """Coerce by what the SIGNATURE declares, never by a list of names."""
    sig = _inspect.signature(fn)
    out = dict(params)
    for key, value in params.items():
        p = sig.parameters.get(key)
        if p is None or not isinstance(value, str):
            continue
        if p.annotation is bool:
            t = value.strip().lower()
            if t in _FALSE_WORDS:
                out[key] = False
            elif t in _TRUE_WORDS:
                out[key] = True
        elif p.annotation is float:
            try:
                out[key] = float(value)
            except ValueError:
                pass
        elif p.annotation is int:
            try:
                out[key] = int(value)
            except ValueError:
                pass
    return out


def _check(fn, name: str, params: dict) -> None:
    sig = _inspect.signature(fn)
    accepted = set(sig.parameters)
    # Open-ended verbs validate their own extra parameters; reject unknown keys only
    # when the signature defines a closed parameter set.
    open_ended = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
    leftover = [] if open_ended else [k for k in params if k not in accepted]
    if leftover:
        raise VerbError(
            f"{name}: parameter(s) this verb does not accept: {leftover}. "
            f"It accepts: {sorted(accepted)}")
    missing = [p.name for p in sig.parameters.values()
               if p.default is _inspect.Parameter.empty
               and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
               and p.name not in params]
    if missing:
        raise VerbError(f"{name}: required parameter missing: {missing}")


def call(name: str, params: dict = None, *, resolver=None):
    """Run a verb by NAME. `resolver` translates a COM object reference.

    `resolver(value)` is called on each parameter before it is passed along: it is how a
    surface that speaks JSON (the MCP) swaps "@handle_7" for the stored IFace2. Without
    it, the verbs that take geometry (`sheet.edge_flange`, `part.fillet`,
    `inspect.select`) would be left out -- which is half of sheet metal.
    """
    fn = verb(name)
    params = dict(params or {})
    if resolver is not None:
        params = {k: resolver(v) for k, v in params.items()}
    params = _coerce(fn, params)
    _check(fn, name, params)
    return fn(**params)
