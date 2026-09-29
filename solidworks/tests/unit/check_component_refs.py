"""check_component_refs.py -- the default planes must not reach the model, in ANY language.

Deterministic: no SolidWorks, no COM. It feeds `_component_refs` a fake feature tree,
which is enough because the only thing under test is which features it DROPS.

Why it exists: `_component_refs` recognised the default planes by name, and its English
half was wrong -- it listed "front" while SolidWorks says "Front Plane". On a pt-BR
installation the filter worked and on an English one all three planes went through to
the model. The 2026-09-21 version battery then filed it as a difference between
SolidWorks 2017 and 2026, because the two machines happened to run different interface
languages. A phantom version difference is the most expensive kind: it sends the next
session looking inside SolidWorks for something that was never there.

    .venv\\Scripts\\python.exe -u solidworks\\tests\\unit\\check_component_refs.py

Moved here from the compiled engine on 2026-09-27: the helper under test is now the
curated `solidworks.sw_assembly._component_refs`, the one both surfaces run.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))))

from solidworks import sw_assembly as _A  # noqa: E402

FAILED = 0


def check(what, got, want):
    global FAILED
    ok = got == want
    if not ok:
        FAILED += 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {what}: {got}"
          + ("" if ok else f"   (esperado {want})"))


class _Feature:
    def __init__(self, name, type_name, nxt=None):
        self.Name = name
        self._type = type_name
        self._next = nxt

    def GetTypeName2(self):
        return self._type

    def GetNextFeature(self):
        return self._next


class _Doc:
    def __init__(self, first):
        self._first = first

    def FirstFeature(self):
        return self._first


class _Comp:
    def __init__(self, doc):
        self._doc = doc

    def GetModelDoc2(self):
        return self._doc


class _FakeC:
    """The `C` module the verb uses -- `cast` is identity on our fakes."""
    @staticmethod
    def cast(obj, _interface):
        return obj


def tree(*features):
    """Chain (name, type) pairs into a feature list, and wrap it in a component."""
    nxt = None
    for name, type_name in reversed(features):
        nxt = _Feature(name, type_name, nxt)
    return _Comp(_Doc(nxt))


def _component_refs(fake_c, comp):
    """The curated helper, with the fake `C` in its module for the call."""
    saved = _A.C
    try:
        _A.C = fake_c
        return _A._component_refs(comp)
    finally:
        _A.C = saved


def names(comp):
    return [r["name"] for r in _component_refs(_FakeC, comp)]


PT = [("Plano frontal", "RefPlane"), ("Plano superior", "RefPlane"),
      ("Plano direito", "RefPlane"), ("Origem", "OriginProfileFeature")]
EN = [("Front Plane", "RefPlane"), ("Top Plane", "RefPlane"),
      ("Right Plane", "RefPlane"), ("Origin", "OriginProfileFeature")]

print("== the default planes, by NAME ==")
check("a pt-BR installation offers nothing of its own", names(tree(*PT)), [])
check("nor does an ENGLISH one -- the case that leaked",
      names(tree(*EN)), [])

print("\n== and what the part really does have DOES come through ==")
check("a named datum next to the pt-BR defaults",
      names(tree(*PT, ("datum_meio", "RefPlane"))), ["datum_meio"])
check("and next to the English ones",
      names(tree(*EN, ("datum_meio", "RefPlane"))), ["datum_meio"])
check("an axis, which is what `clevis` mates by",
      names(tree(*EN, ("body_axis", "RefAxis"))), ["body_axis"])
check("planes and axes together, in tree order",
      names(tree(*EN, ("datum_meio", "RefPlane"), ("body_axis", "RefAxis"),
                 ("csys_base", "CoordSys"))),
      ["datum_meio", "body_axis", "csys_base"])

print("\n== by POSITION, which is what survives a language nobody here has seen ==")
XX = [("Vorderansicht", "RefPlane"), ("Draufsicht", "RefPlane"),
      ("Rechte Ansicht", "RefPlane"), ("Ursprung", "OriginProfileFeature")]
check("three leading planes are the defaults whatever they are called",
      names(tree(*XX)), [])
check("and a datum after them still comes through",
      names(tree(*XX, ("datum_meio", "RefPlane"))), ["datum_meio"])

print("\n== the position rule must not eat a REAL plane ==")
# /!\ The counter-proof. A rule that drops "the first three planes" would silently eat
# a user's datum in a part whose tree starts differently -- a filter that hides what the
# mate needs is worse than one that shows three planes too many.
check("only TWO leading planes: a third, created later, survives",
      names(tree(("Front Plane", "RefPlane"), ("Top Plane", "RefPlane"),
                 ("Boss-Extrude1", "Extrusion"), ("datum_meio", "RefPlane"))),
      ["datum_meio"])
check("a plane created AFTER solid geometry is never a default",
      names(tree(*EN, ("Boss-Extrude1", "Extrusion"), ("datum_a", "RefPlane"),
                 ("datum_b", "RefPlane"), ("datum_c", "RefPlane"))),
      ["datum_a", "datum_b", "datum_c"])

print("\n== a part that is not loaded says nothing, and does not crash ==")
check("GetModelDoc2() -> None", _component_refs(_FakeC, _Comp(None)), [])

print("\n== SCOREBOARD ==")
print("  all ok" if not FAILED else f"  {FAILED} FAILED")
sys.exit(1 if FAILED else 0)
