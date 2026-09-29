# -*- coding: utf-8 -*-
r"""
Where a live test writes, and which SOLIDWORKS documents it may close.

Two modes, one source:

  SHARED (the development default): the historical behaviour. Every family writes to
      tests/validation_assembly/, which later families and development probes read
      (the clevis parts, the benchmark inputs), and hygiene is session-wide:
      `close_untitled()` and `CloseAllDocuments(True)`.
  ISOLATED (the public default): a fresh output directory per run, and a test closes
      only documents that were NOT open when it started. Documents the user already
      had open are never closed, saved or modified by the hygiene calls.

Either default can be overridden:

  CADAPTER_TEST_ISOLATED=1 / 0   force isolated / shared mode
  CADAPTER_TEST_OUT=<dir>        output root (created if missing); a runner sets it once
                                 so every family of the run, and every subprocess a
                                 family starts to build its inputs, shares ONE root

Nothing here imports COM at module import: the orchestrators and the no-CAD checks
import this module without touching SOLIDWORKS.
"""
from __future__ import annotations

import os
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_ISOLATED = "CADAPTER_TEST_ISOLATED"
ENV_OUT = "CADAPTER_TEST_OUT"
SHARED_OUT = os.path.join(ROOT, "tests", "validation_assembly")

_DEFAULT_ISOLATED = True

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def isolated() -> bool:
    value = os.environ.get(ENV_ISOLATED, "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return _DEFAULT_ISOLATED


def output_root() -> str:
    """The directory that stands in for tests/validation_assembly/ in this run.

    Isolated mode with no CADAPTER_TEST_OUT creates a NEW temporary directory and
    exports it through the environment, so that subprocesses started from here (the
    drawing benchmark builds its inputs with the other families) write next to it
    instead of creating their own. A new directory is what makes stale output from an
    earlier run impossible to pick up by accident: the drawing tests reuse a saved part
    or assembly when the file already exists."""
    explicit = os.environ.get(ENV_OUT, "").strip()
    if explicit:
        path = os.path.abspath(explicit)
        os.makedirs(path, exist_ok=True)
        return path
    if not isolated():
        return SHARED_OUT
    path = tempfile.mkdtemp(prefix=f"cadapter-tests-{time.strftime('%Y%m%d-%H%M%S')}-")
    os.environ[ENV_OUT] = path
    print(f"test output: {path}", flush=True)
    return path


# ── documents ─────────────────────────────────────────────────────────────────
# A document is identified by its full path once saved, by its title while it has
# none. SOLIDWORKS does not reuse an untitled title within a session, and it cannot
# hold two documents with the same file name open, so a document the test creates
# cannot take the key of one that was open before.
_FOREIGN: set | None = None

_DOC_DRAWING, _DOC_ASSEMBLY = 3, 2   # swDocumentTypes_e


def _key(md) -> str:
    path = md.GetPathName() or ""
    return path.lower() if path else "untitled:" + md.GetTitle()


def _documents() -> list:
    """Every open document as IModelDoc2, including the invisible ones an open
    assembly loads for its components."""
    from solidworks import sw_com as C
    sw = C.app()
    iface = C.module().IModelDoc2
    try:
        raw = sw.GetDocuments()
        return [iface(getattr(d, "_oleobj_", d)) for d in (raw or ())]
    except Exception:  # noqa: BLE001 -- fall back to the older enumeration below
        pass
    out, d = [], sw.GetFirstDocument()
    while d is not None and len(out) < 1000:
        md = iface(getattr(d, "_oleobj_", d))
        out.append(md)
        d = md.GetNext()
    return out


def open_document_keys() -> set:
    return {_key(md) for md in _documents()}


def begin(force: bool = False) -> None:
    """Record the documents that belong to the user. Call right after connecting.
    Idempotent: the first snapshot of the process is the one that counts."""
    global _FOREIGN
    if _FOREIGN is not None and not force:
        return
    _FOREIGN = open_document_keys() if isolated() else set()
    if isolated() and _FOREIGN:
        print(f"note: {len(_FOREIGN)} document(s) were already open; this test leaves "
              f"them open and untouched", flush=True)


def _close_owned(want) -> list:
    """Close the documents that appeared after `begin()` and satisfy `want(md)`.
    Drawings first, then assemblies, then parts: a part an open assembly or drawing
    references stays loaded until they close. Several passes, because closing an
    assembly releases (and may reveal) its component documents."""
    from solidworks import sw_com as C
    if _FOREIGN is None:
        begin()          # a late snapshot only ever protects MORE documents
    sw = C.app()
    closed = []
    for _ in range(5):
        docs = []
        for md in _documents():
            try:
                if _key(md) not in _FOREIGN and want(md):
                    docs.append(md)
            except Exception:  # noqa: BLE001 -- a document released mid-scan
                continue
        if not docs:
            break
        docs.sort(key=lambda md: {_DOC_DRAWING: 0, _DOC_ASSEMBLY: 1}.get(md.GetType(), 2))
        for md in docs:
            try:
                title = md.GetTitle()
                active = sw.ActiveDoc
                if active is not None and C.cast(active, "IModelDoc2").GetTitle() == title:
                    C.close_active_sketch(md)   # an open sketch can block the close
                sw.CloseDoc(title)
                closed.append(title)
            except Exception:  # noqa: BLE001 -- already released with its assembly
                continue
    return closed


def close_scratch():
    """Replaces `sw_com.close_untitled()`: the unsaved documents. Isolated mode closes
    only the unsaved documents this test created."""
    from solidworks import sw_com as C
    if not isolated():
        return C.close_untitled()
    return len(_close_owned(lambda md: not md.GetPathName()))


def close_all():
    """Replaces `CloseAllDocuments(True)`. Isolated mode closes every document this
    test created or opened, saved or not, and nothing else."""
    from solidworks import sw_com as C
    if not isolated():
        C.app().CloseAllDocuments(True)
        return None
    return len(_close_owned(lambda md: True))


def close_titles(titles) -> None:
    """Close documents by title, as the families do before rebuilding their fixtures.
    Isolated mode closes a title only when this test owns that document."""
    from solidworks import sw_com as C
    if not isolated():
        for t in titles:
            try:
                C.app().CloseDoc(t)
            except Exception:  # noqa: BLE001
                pass
        return
    wanted = {t.lower() for t in titles}
    _close_owned(lambda md: md.GetTitle().lower() in wanted)


def finish() -> None:
    """End of a family. Isolated mode closes what the test left open; shared mode keeps
    the historical end state (some families leave their last document open)."""
    if isolated():
        close_all()
