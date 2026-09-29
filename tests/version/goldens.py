# -*- coding: utf-8 -*-
r"""
goldens.py -- harvests the `golden` of the agent's `suite_verbs.py` and hands them over as
scenarios of the version suite.

Why harvest instead of copy: those 37 cases have already passed the test of being FAIR
(the comment on `C()` there explains it -- if our own layer does not build the part,
demanding it of anyone measures our limitation, not theirs). Duplicating here would
create two lists that diverge on day one. Harvested, a new case there appears here on its own.

The translation is only of spelling: a flattened tool surface spells `part.block` as
`part_block` to fit a tool name, and both land on the SAME verb.

It READS BY AST, not by import: `suite_verbs` pulls in the whole agent, which is heavy
and assumes a model server is up. The goldens are literal data, so they can be read
without executing anything in the module.

/!\ WHERE `suite_verbs` IS NOT IN THE CHECKOUT, the goldens come from `goldens.json`,
a FROZEN copy of the same harvest. The public repository does not carry the agent's
batteries, and without the frozen copy `load()` returned [] there with no error -- the
battery silently ran 56 of its 93 scenarios. Refresh the copy after changing a golden:

    .venv\Scripts\python.exe tests\version\goldens.py --freeze

The release gate refuses a frozen copy that differs from the harvest.
"""

from __future__ import annotations

import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "agent", "tests", "suite_verbs.py")
FROZEN = os.path.join(HERE, "goldens.json")

# the `suite_verbs` families -> the domain here. They all build a PART.
DOMAIN = "part"


def _flat_to_dot(name: str) -> str:
    """`part_hole_on` -> `part.hole_on`. Only the FIRST `_` becomes a dot."""
    dom, _, rest = name.partition("_")
    return f"{dom}.{rest}" if rest else name


def load(source: str = SOURCE) -> list:
    """The cases with a golden, already in the version suite's scenario shape.

    Harvested from `source` when it exists, otherwise read from the frozen copy."""
    if not os.path.exists(source):
        return load_frozen()
    return harvest(source)


def load_frozen(path: str = FROZEN) -> list:
    """The frozen harvest, with each step back as a (verb, params) tuple."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"neither {SOURCE} nor {path} exists -- the version battery would silently "
            f"lose its golden scenarios")
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)
    for c in cases:
        c["steps"] = [(v, p) for v, p in c["steps"]]
    return cases


def freeze(source: str = SOURCE, path: str = FROZEN) -> int:
    """Write the harvest of `source` to the frozen copy. Returns the case count."""
    cases = harvest(source)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return len(cases)


def harvest(source: str = SOURCE) -> list:
    """Read the goldens out of `suite_verbs.py` by AST."""
    with open(source, encoding="utf-8") as stream:
        tree = ast.parse(stream.read())

    # the list lives in `CASES = [...]`, and each item is a call C(name, family,
    # prompt, golden, **checks)
    cases = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign)
                and any(getattr(a, "id", "") == "CASES" for a in node.targets)):
            continue
        for item in getattr(node.value, "elts", []):
            if not (isinstance(item, ast.Call) and getattr(item.func, "id", "") == "C"):
                continue
            if len(item.args) < 4:
                continue          # a case with no golden: it cannot be reproduced here
            try:
                name = ast.literal_eval(item.args[0])
                family = ast.literal_eval(item.args[1])
                golden = ast.literal_eval(item.args[3])
            except ValueError:
                continue          # a golden with an expression (not a literal) -- skip
            if not golden:
                continue
            steps = [(_flat_to_dot(v), dict(p or {})) for v, p in golden]
            cases.append({"name": f"gab_{family}_{name}", "domain": DOMAIN,
                          "steps": steps, "source": "suite_verbs"})
        break
    return cases


if __name__ == "__main__":
    if "--freeze" in sys.argv:
        print(f"{freeze()} scenarios frozen into {os.path.basename(FROZEN)}")
        sys.exit(0)
    cs = load()
    print(f"{len(cs)} scenarios harvested from {os.path.basename(SOURCE)}")
    verbs = sorted({v for c in cs for v, _ in c["steps"]})
    print(f"{len(verbs)} distinct verbs: {', '.join(verbs)}")
    for c in cs:
        print(f"  {c['name']:<44} {len(c['steps'])} steps")
