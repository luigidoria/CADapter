"""
bundles.py -- the PROTOCOL: a verb compiles into a BUNDLE of COM operations.

A bundle groups the ordered COM operations, references and guards for one verb.

Format:
  ops     an ordered list; each op references earlier results through "$oN"
  guards  checks the executor runs AFTER an op (aborts the bundle on error)
  returns what to return: "$oN", or a dict {key: spec}, with an optional transform
          {"$ref": "oN", "scale": 1000}

An op is one of these shapes:
  {"id","target","call","args",["cast"]}   a method call
  {"id","target","get",["cast"]}           a property read
  {"id","index","i"}                       unpacks a tuple (a DATA op)
  {"id","format","args"}                   builds a string from refs (a DATA op)
  {"id","helper","args"}                   an aggregate executor primitive (bulk read)
                                           bulk reads or loops over geometry

`target` is an alias the executor resolves AT THE MOMENT of the op (app, doc, ext, sm,
fm, part, asm, dwg) or "$oN". Resolving on the spot matters: after NewDocument, `doc`
already points at the new document.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

BUNDLE_TTL_SEC = 120          # a short window: a bundle is ephemeral, not an artifact


class BundleBuilder:
    def __init__(self, verb: str):
        self.verb = verb
        self.ops: list[dict] = []
        self.guards: list[dict] = []
        self._n = 0

    def _next(self) -> str:
        self._n += 1
        return f"o{self._n}"

    def call(self, target: str, method: str, *args, cast: str = "") -> str:
        oid = self._next()
        op = {"id": oid, "target": target, "call": method, "args": list(args)}
        if cast:
            op["cast"] = cast
        self.ops.append(op)
        return f"${oid}"

    def get(self, target: str, prop: str, cast: str = "") -> str:
        oid = self._next()
        op = {"id": oid, "target": target, "get": prop}
        if cast:
            op["cast"] = cast
        self.ops.append(op)
        return f"${oid}"

    def index(self, ref: str, i) -> str:
        """Extract item `i` from a TUPLE return (e.g. AddMate5 -> (mate, error)).
        A DATA op: it does not touch COM, it only unpacks -- so the meaning of the
        error code (NoError==1!) is interpreted by the verb.

        `i` may be a NEGATIVE index ("the last sketch in the tree") or a STRING key, for
        a helper that answers several things at once (`sm_sharp_bend_edges` gives back
        the bend edges AND the fixed face of the same corner, and the bundle addresses
        one of them at a time)."""
        oid = self._next()
        self.ops.append({"id": oid, "index": ref, "i": i if isinstance(i, str) else int(i)})
        return f"${oid}"

    def format(self, template: str, *args) -> str:
        """Build a string from pieces only known AT RUN TIME (the component name, the
        document title). A DATA op: it does not touch COM. It exists so the PATTERN of
        the qualified name ('ref@component@assembly') is defined by the verb and
        resolved from actual document and component names during execution."""
        oid = self._next()
        self.ops.append({"id": oid, "format": template, "args": list(args)})
        return f"${oid}"

    def set(self, target: str, prop: str, value) -> str:
        oid = self._next()
        self.ops.append({"id": oid, "target": target, "set": prop, "value": value})
        return f"${oid}"

    def helper(self, helper_name: str, **kwargs) -> str:
        """Call an aggregate executor primitive. `kwargs` become the op args.

        The first parameter must NOT share a name with a data argument: while it was
        called `name`, `b.helper("comp_by_name", name=x)` raised "got multiple values
        for argument 'name'" -- preventing bundle compilation. Measured 2026-08-19.
        """
        oid = self._next()
        self.ops.append({"id": oid, "helper": helper_name, "args": kwargs})
        return f"${oid}"

    def guard(self, ref: str, kind: str, msg: str, value=None, tol=None) -> None:
        """kind: truthy | not_null | nonempty | equals | in | contains | near | gte

        `contains` is `in` the other way round (the op read the LIST, the verb supplies
        the expected item); `near` is `equals` for a measurement, with `tol`."""
        g = {"op": ref.lstrip("$"), "assert": kind, "msg": msg}
        if value is not None:
            g["value"] = value
        if tol is not None:
            g["tol"] = tol
        self.guards.append(g)

    @staticmethod
    def handle(ref: str) -> dict:
        """Mark a return to become a HANDLE in the executor (a reusable COM object)."""
        return {"$handle": ref.lstrip("$")}

    def build(self, returns, params: dict | None = None) -> dict:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return {
            "bundle_id": "bdl_" + secrets.token_hex(10),
            "verb": self.verb,
            "params": params or {},
            "ops": self.ops,
            "guards": self.guards,
            "returns": returns,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=BUNDLE_TTL_SEC)).isoformat(),
        }


# -- units: the conversion happens HERE, -----------------------
def mm(v: float) -> float:
    return float(v) / 1000.0


def deg(v: float) -> float:
    from math import radians
    return radians(float(v))
