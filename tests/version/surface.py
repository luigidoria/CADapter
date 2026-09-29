# -*- coding: utf-8 -*-
r"""
surface.py -- how the version battery calls a verb.

There is ONE verb catalog since 2026-09-27: the curated layer, `solidworks.catalog`,
called by NAME in this process, with no server (193 verbs, the 48 SHEET METAL ones
included). Running the battery on the SW 2017 laptop is `git pull` + venv.

Until then there were three surfaces -- the cloud (a signed bundle from a server), the
same compiled verbs run locally, and the curated one -- and this module hid that the two
`inspect`s answered in different shapes. The compiled catalog and the cloud are retired now; `open_()` still recognises their names so an old command line fails with
a sentence instead of a KeyError.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class Curated:
    """A curated verb called by NAME, directly in the process. No server.

    A verb that returns COM objects hands them back as they are: the next step of the
    same scenario receives the object itself through `$N`, and the report keeps only the
    interface name (see `normalise` in suite_version.py).
    """

    name = "curated"

    def __init__(self, on_thread=None):
        from solidworks import catalog
        self._K = catalog
        self._on = on_thread or (lambda fn: fn())

    def __call__(self, verb, params=None):
        return self._on(lambda: self._K.call(verb, params or {}))


def cloud_available() -> bool:
    """The cloud harness is legacy: never available to this battery."""
    return False


def open_(name: str, on_thread=None):
    if name == "curated":
        return Curated(on_thread)
    if name in ("cloud", "local"):
        raise ValueError(f"surface '{name}' was the compiled catalog, retired on "
                         f"2026-09-27 -- the battery runs on the curated layer only")
    raise ValueError(f"surface '{name}' does not exist (curated)")
