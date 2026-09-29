# -*- coding: utf-8 -*-
"""verb_engine.py -- compiles catalog verbs and executes them locally.

Compilation uses the shared normalization in `verbs.compile_verb`. Execution uses
one COM thread, validates the allowlist and applies configured path restrictions.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config_env
config_env.load_module("engine")
from engine import executor
from verb_catalog import schemas, verbs  # noqa: E402


class VerbError(RuntimeError):
    """An unknown verb, or a verb that refused its parameters."""


def catalog() -> dict:
    """The parameter schemas, one entry per verb."""
    return schemas.build()


def names() -> list[str]:
    return sorted(verbs.VERBS)


def default_cfg() -> dict:
    """The only key the executor reads out of the config is `allowed_dirs`.

    Empty means "no restriction", which is what a local engine wants by default: there is
    no second party here to protect a machine from its own user. `SOLIDMCP_ALLOWED_DIRS`
    (a `os.pathsep`-separated list) narrows it for whoever does want the sandbox.
    """
    raw = os.environ.get("SOLIDMCP_ALLOWED_DIRS", "")
    return {"allowed_dirs": [p for p in raw.split(os.pathsep) if p.strip()]}


def compile_(verb: str, params: dict | None = None) -> dict:
    """Compile a bundle without executing SOLIDWORKS operations.

    Always use `verbs.compile_verb` to normalize parameters. Calling a registered
    verb directly skips boolean normalization: `through_all="False"` then builds
    a through hole instead of a blind one.
    """
    if verb not in verbs.VERBS:
        raise VerbError(f"unknown verb: {verb!r}. `names()` lists the {len(verbs.VERBS)} "
                        f"that exist.")
    return verbs.compile_verb(verb, dict(params or {}))


def call(verb: str, params: dict | None = None, *,
         cfg: dict | None = None, overwrite: bool = False):
    """Compile `verb` and execute its bundle locally."""
    bundle = compile_(verb, params)
    return executor.execute(bundle, cfg or default_cfg(), overwrite=overwrite)


def connect(launch: bool = True) -> str:
    """Open/attach SolidWorks and return the revision. Same COM thread as every call."""
    return executor.connect(launch=launch)


def handle_count() -> int:
    return executor.handle_count()


def clear_handles() -> int:
    return executor.clear_handles()
