# -*- coding: utf-8 -*-
r"""
check_scenarios.py -- every step of the version battery is a curated verb, with only the
parameters its signature has. No CAD.

A scenario whose verb or parameter does not exist fails on the FIRST run on each
SolidWorks, and the two reports then agree on an error that is the battery's, not the
version's. Since the battery moved to the curated layer (2026-09-27) the signatures are
the contract, so they are checked here before any CAD time is spent.

    .venv\Scripts\python.exe -B tests\version\check_scenarios.py
"""

from __future__ import annotations

import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import suite_version  # noqa: E402
from solidworks import catalog  # noqa: E402


def problems() -> list[str]:
    out = []
    for c in suite_version.SCENARIOS:
        for i, (verb, params) in enumerate(c["steps"]):
            fn = catalog.VERBS.get(verb)
            if fn is None:
                out.append(f"{c['name']}#{i}: '{verb}' is not a curated verb")
                continue
            sig = inspect.signature(fn)
            if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
                continue
            out += [f"{c['name']}#{i}: {verb} has no parameter '{k}'"
                    for k in params if k not in sig.parameters]
    return out


if __name__ == "__main__":
    found = problems()
    for p in found:
        print("  FAIL", p)
    print(f"{len(suite_version.SCENARIOS)} scenarios -- "
          + (f"{len(found)} problem(s)" if found else "every step maps onto a curated verb"))
    sys.exit(1 if found else 0)
