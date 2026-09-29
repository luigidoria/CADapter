# -*- coding: utf-8 -*-
"""
check_catalog.py -- a DETERMINISTIC test of the curated verb catalog.

It needs no SolidWorks: everything here is reflection over the `solidworks/` modules
and parameter validation, which happens BEFORE any COM call. It runs in seconds and is
the first test after touching `solidworks/catalog.py` or adding a verb to any of the
six modules.

What it protects, in order of importance:

  1. the catalog COVERS the six modules and loses no verb (the list is born by
     reflection precisely so it cannot diverge -- this test is what proves it still is);
  2. a verb name is NOT repeated and does not collide across domains;
  3. parameter validation REFUSES before the CAD (an unknown parameter, a missing
     required one) with a message that says the next step;
  4. the coercion of a boolean from TEXT -- `bool("False")` is True, and that defect has
     already cost dearly on the cloud (verb_catalog/verbs.py). Here it is tested both ways;
  5. the handle resolver descends into a LIST and a DICT (sheet metal passes geometry in a list).

Usage:
  .venv\\Scripts\\python.exe -u solidworks\\tests\\unit\\check_catalog.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from solidworks import catalog as K  # noqa: E402

OK = FAILED = 0


def chk(name, cond, detail=""):
    global OK, FAILED
    if cond:
        OK += 1
    else:
        FAILED += 1
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))
    return bool(cond)


def _raises(fn, text=""):
    """True if `fn` raises VerbError (and, optionally, if the message TEACHES)."""
    try:
        fn()
    except K.VerbError as exc:
        return text.lower() in str(exc).lower() if text else True
    except Exception:
        return False
    return False


# ── 1. coverage ───────────────────────────────────────────────────────────────
def coverage():
    chk("the six domains are in the catalog",
        set(K.DOMAINS) == {"part", "sketch", "inspect", "asm", "dwg", "sheet"},
        str(K.DOMAINS))
    for dom in K.DOMAINS:
        chk(f"domain '{dom}' is not empty", len(K.list_verbs(dom)) > 0)
    # the numbers of the 2026-08-31 validation:
    # inspect.measure/validate/sketch/model_summary, asm.mates/free_dof,
    # dwg.list_views/centerlines and the five part.*configuration verbs; and the usable-
    # drawing front (2026-09-25): asm.exploded_overlaps, dwg.hide_reference_geometry/
    # auto_dimension/tidy_dimensions/arrange/quality; and sheet.info (2026-09-27), the
    # one-call state reader ported from the compiled layer; and moving views after they
    # are placed (2026-09-28): dwg.view_info/model_to_sheet/move_view/align_view/
    # set_view_scale/delete_view. They change when a verb is born,
    # and then the change has to be DELIBERATE (the test fails and you update it here on
    # purpose)
    expected = {"part": 45, "sketch": 14, "inspect": 16, "asm": 34, "dwg": 36,
                "sheet": 48}
    real = {d: len(K.list_verbs(d)) for d in K.DOMAINS}
    chk("the per-domain count matches the last validation", real == expected,
        f"{real} != {expected}")
    chk("the total is the sum of the domains",
        len(K.VERBS) == sum(real.values()) == 193, len(K.VERBS))
    chk("no private verb leaked", not any("._" in n for n in K.VERBS))
    chk("no imported module became a verb (the __module__ filter)",
        "part.mm" not in K.VERBS and "sheet.select" not in K.VERBS
        and "part.cast" not in K.VERBS)
    # key verbs of each domain, so the test fails if the reflection changes shape
    for n in ("part.extrude", "sketch.dimension", "inspect.mass", "asm.mate",
              "dwg.new_drawing", "sheet.box_flanges", "sheet.jog", "sheet.gusset",
              "sheet.cut_lists"):
        chk(f"'{n}' is in the catalog", n in K.VERBS)


# ── 2. names ──────────────────────────────────────────────────────────────────
def names():
    chk("every verb has domain.name", all(n.count(".") == 1 for n in K.VERBS))
    chk("every verb has a one-line summary",
        all(K.catalog()[n] for n in K.VERBS),
        str([n for n in K.VERBS if not K.catalog()[n]])[:120])
    chk("the summary is ONE line", all("\n" not in v for v in K.catalog().values()))
    # the SAME short name in two domains is legitimate (part.save and asm.save), but the
    # QUALIFIED name must not repeat -- and the dict already guarantees that; what this
    # check catches is the domain having been misspelled in MODULES
    chk("every domain in a name exists in MODULES",
        all(n.split(".")[0] in K.MODULES for n in K.VERBS))


# -- 3. validation that REFUSES before the CAD --------------------------------
def validation():
    chk("a non-existent verb is refused", _raises(lambda: K.verb("sheet.does_not_exist")))
    chk("and the message SUGGESTS the near miss (one letter too many)",
        _raises(lambda: K.verb("sheet.box_flangess"), "sheet.box_flanges"))
    chk("it also suggests by a PARTIAL name",
        _raises(lambda: K.verb("sheet.flange"), "sheet.edge_flange"))
    chk("with nothing similar, the message lists the DOMAINS",
        _raises(lambda: K.verb("zzz.yyy"), "Domains"))
    chk("a non-existent domain is refused", _raises(lambda: K.list_verbs("chapa")))

    chk("a parameter the verb does not accept is refused",
        _raises(lambda: K.call("sheet.box_flanges", {"lengthmm": 20}),
                 "does not accept"))
    chk("and the message lists the ones it DOES accept",
        _raises(lambda: K.call("sheet.box_flanges", {"lengthmm": 20}),
                 "length_mm"))
    chk("a missing required parameter is refused",
        _raises(lambda: K.call("sheet.box_flanges", {}), "required parameter missing"))
    chk("the refusal happens BEFORE touching COM",
        _raises(lambda: K.call("sheet.new_sheet", {"plane": "Front"}),
                 "required parameter missing"))


# ── 4. coercion (the bool("False") defect) ────────────────────────────────────
def coercion():
    flatten = K.verb("sheet.flatten")
    for text in ("false", "False", "0", "nao", "não", "no", "off"):
        chk(f"'{text}' becomes False", K._coerce(flatten, {"on": text})["on"] is False)
    for text in ("true", "True", "1", "sim", "yes", "on"):
        chk(f"'{text}' becomes True", K._coerce(flatten, {"on": text})["on"] is True)
    chk("text that is NOT yes/no passes through intact (refusing would invent an error)",
        K._coerce(flatten, {"on": "maybe"})["on"] == "maybe")
    chk("a real boolean passes through intact",
        K._coerce(flatten, {"on": True})["on"] is True)

    thick = K.verb("sheet.set_thickness")
    chk("a number in text becomes a float when the signature asks for a float",
        K._coerce(thick, {"thickness_mm": "2.5"})["thickness_mm"] == 2.5)
    chk("text that is not a number passes through intact (let the verb complain)",
        K._coerce(thick, {"thickness_mm": "thick"})["thickness_mm"] == "thick")
    # the coercion is guided by the SIGNATURE, never by a parameter's name
    trim = K.verb("sheet.corner_trim")
    chk("a str parameter is not coerced into a bool",
        K._coerce(trim, {"kind": "0"})["kind"] == "0")


# -- 5. the handle resolver (what makes sheet metal work over MCP) ------------
def resolver():
    stored = {"@handle_1": "FACE-1", "@handle_2": "EDGE-2"}

    def resolve(v):
        if isinstance(v, list):
            return [resolve(x) for x in v]
        if isinstance(v, dict):
            return {k: resolve(x) for k, x in v.items()}
        return stored.get(v, v)

    seen = {}

    def spy(**kw):
        seen.update(kw)
        return "ok"

    K.VERBS["_test.spy"] = spy
    try:
        # the resolver has to descend into a LIST: corner_trim/gusset take geometry
        # in a list, and resolving only at the first level would leave exactly those out
        K.call("_test.spy", {"faces": ["@handle_1", "@handle_2"],
                                  "where": {"a": "@handle_1"}, "n": 3},
                 resolver=resolve)
        chk("a handle inside a LIST is resolved",
            seen.get("faces") == ["FACE-1", "EDGE-2"], str(seen.get("faces")))
        chk("a handle inside a DICT is resolved",
            seen.get("where") == {"a": "FACE-1"}, str(seen.get("where")))
        chk("a value that is not a handle passes through intact", seen.get("n") == 3)
    finally:
        K.VERBS.pop("_test.spy", None)


# ── 6. help ───────────────────────────────────────────────────────────────────
def help_():
    a = K.help_for("sheet.jog")
    chk("help carries the COMPLETE docstring (with the gotchas)",
        "DEGREES" in a["doc"], a["doc"][:60])
    chk("help carries the parameters in order",
        [p["name"] for p in a["parameters"]][:3] == ["line", "face", "fixed_at"],
        str([p["name"] for p in a["parameters"]][:3]))
    chk("help marks required/optional",
        all(p["required"] is False for p in a["parameters"]))
    b = K.help_for("sheet.new_sheet")
    required = [p["name"] for p in b["parameters"] if p["required"]]
    chk("new_sheet declares the required ones", required == ["plane", "w_mm", "h_mm"],
        str(required))
    chk("help for a non-existent verb is refused",
        _raises(lambda: K.help_for("sheet.nothing")))
    # the catalog has to be serialisable: it is what the MCP tool returns
    import json
    chk("the whole catalog serialises to JSON",
        len(json.dumps(K.catalog(), ensure_ascii=False)) > 5000)
    chk("the help serialises to JSON",
        len(json.dumps(K.help_for("sheet.gusset"), ensure_ascii=False, default=str)) > 200)


BLOCKS = (coverage, names, validation, coercion, resolver, help_)


def main():
    for block in BLOCKS:
        block()
    print(f"SUMMARY: {OK}/{OK + FAILED} PASS")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
