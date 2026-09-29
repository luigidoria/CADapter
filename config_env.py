"""Optional environment defaults, with no third-party dependency.

Import loads the legacy root .env (or SOLIDMCP_ENV_FILE) for existing installations.
SOLIDMCP_ENV_FILE is the pre-CADapter name, kept as a compatibility contract.
An optional module explicitly calls load_module before reading its settings.
Precedence is process environment, legacy or selected file, then module defaults.
No module configuration is discovered or loaded implicitly.

The parser supports KEY=value, blank lines, full-line comments and quoted values.
It does not expand variables or implement shell syntax. Repository-relative paths
belong in code, derived from __file__, rather than in environment configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repository root. It is the answer for every internal path -- use this constant
# instead of writing the absolute path of your own machine.
ROOT = Path(__file__).resolve().parent

ENV_PATH = Path(os.environ.get("SOLIDMCP_ENV_FILE", ROOT / ".env"))

_loaded = False


def _parse(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    if not key:
        return None
    return key, value


def load(path: Path | None = None, force: bool = False) -> dict[str, str]:
    """Read `.env` and set in the environment whatever is NOT already defined.

    Returns what was effectively applied. With no file, returns {} silently --
    `.env` is optional by design: the defaults in the code have to be enough to
    run with no configuration at all.
    """
    global _loaded
    if _loaded and not force:
        return {}
    _loaded = True

    target = Path(path) if path else ENV_PATH
    if not target.exists():
        return {}

    applied: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        pair = _parse(line)
        if pair is None:
            continue
        key, value = pair
        if key in os.environ:        # the real environment beats the file
            continue
        os.environ[key] = value
        applied[key] = value
    return applied


def load_module(name: str) -> dict[str, str]:
    """Load defaults from one module's .env without overriding existing values.

    Missing modules/configuration are optional. Restrict names to a single package
    component so a caller cannot accidentally select a file outside the repository.
    """
    if not name.isidentifier():
        raise ValueError("module name must be a single Python package name")
    return load(ROOT / name / ".env", force=True)


load()
