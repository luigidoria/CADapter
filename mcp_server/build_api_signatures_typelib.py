"""
build_api_signatures_typelib.py
Generates `mcp_server/cache/api_signatures.json` from the REGISTERED SolidWorks TYPELIB, with no
dependency on Dassault's documentation (CHM/HTML).

Why it exists:
The original build_api_signatures.py builds the signature index from
HTML extracted from the SolidWorks API documentation. That documentation
is not distributed with this project.

This implementation instead reads API metadata — including parameter
names, order, and types — directly from the type library registered by
the user's local SolidWorks installation.

This also allows the generated index to reflect the SolidWorks version
installed on that machine, since API signatures may differ between
versions.

SolidWorks does not need to be running. The corresponding type library
only needs to be registered in Windows.

Usage:
    python mcp_server/build_api_signatures_typelib.py [--out PATH] [--compare OTHER.json]

The output format is identical to the old index (sw_api_signature does not change):
    { "<method in lowercase>": [ {interface, method, returns, signature, params[]} ] }
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

import pythoncom
from win32com.client import selecttlb

# Typelibs to walk: (pattern in 'desc', required?)
# sldworks.tlb is the API core (ISldWorks, IModelDoc2, IFeatureManager, ...).
TLB_PATTERNS = [
    ("sldworks", True),        # SldWorks <year> Type Library  -- the core
    ("dimxpert", False),       # DimXpert
    ("swpublished", False),    # interfaces exposed to add-ins
]

# Methods inherited from IUnknown/IDispatch: noise, not part of the API.
_COM_PLUMBING = {
    "QueryInterface", "AddRef", "Release",
    "GetTypeInfoCount", "GetTypeInfo", "GetIDsOfNames", "Invoke",
}

# VT_* -> the name in the VBA dialect (the same the doc uses, so the index reads
# familiar)
_VT_NAMES = {
    pythoncom.VT_BOOL: "Boolean",
    pythoncom.VT_I2: "Integer",
    pythoncom.VT_I4: "Long",
    pythoncom.VT_INT: "Long",
    pythoncom.VT_UI1: "Byte",
    pythoncom.VT_UI2: "Integer",
    pythoncom.VT_UI4: "Long",
    pythoncom.VT_I8: "LongLong",
    pythoncom.VT_R4: "Single",
    pythoncom.VT_R8: "Double",
    pythoncom.VT_BSTR: "String",
    pythoncom.VT_DATE: "Date",
    pythoncom.VT_CY: "Currency",
    pythoncom.VT_DISPATCH: "Object",
    pythoncom.VT_UNKNOWN: "Object",
    pythoncom.VT_VARIANT: "Variant",
    pythoncom.VT_VOID: "",
    pythoncom.VT_HRESULT: "",
    pythoncom.VT_ERROR: "Long",
    pythoncom.VT_LPSTR: "String",
    pythoncom.VT_LPWSTR: "String",
}

PARAMFLAG_FOUT = 0x2
_INVKIND_TAG = {
    pythoncom.INVOKE_FUNC: "",
    pythoncom.INVOKE_PROPERTYGET: "get",
    pythoncom.INVOKE_PROPERTYPUT: "put",
    pythoncom.INVOKE_PROPERTYPUTREF: "putref",
}


def _iface_name(raw: str) -> str:
    """'IFeatureManager' -> 'FeatureManager' (the old index drops the leading I)."""
    if len(raw) > 1 and raw[0] == "I" and raw[1].isupper():
        return raw[1:]
    return raw


def _type_name(tdesc, typeinfo) -> tuple[str, bool]:
    """Resolve a TYPEDESC -> (readable name, is_pointer). Recursive for VT_PTR/ARRAY."""
    if isinstance(tdesc, int):
        return _VT_NAMES.get(tdesc, f"VT_{tdesc}"), False

    kind = tdesc[0]
    if kind == pythoncom.VT_PTR:
        inner, _ = _type_name(tdesc[1], typeinfo)
        return inner, True
    if kind in (pythoncom.VT_SAFEARRAY, pythoncom.VT_CARRAY):
        inner, _ = _type_name(tdesc[1], typeinfo)
        return f"{inner}()", False
    if kind == pythoncom.VT_USERDEFINED:
        try:
            ref = typeinfo.GetRefTypeInfo(tdesc[1])
            return _iface_name(ref.GetDocumentation(-1)[0]), False
        except pythoncom.com_error:
            return "Object", False
    return _VT_NAMES.get(kind, f"VT_{kind}"), False


def _unwrap_elem(elem):
    """An ELEMDESC is (TYPEDESC, paramflags); a TYPEDESC is an int or (VT_*, sub). To
    disambiguate: if the first item is a TUPLE, an ELEMDESC came in -> return the TYPEDESC
    inside it."""
    if isinstance(elem, (tuple, list)) and elem and isinstance(elem[0], (tuple, list)):
        return elem[0]
    return elem


def _elem_flags(elem) -> int:
    """An ELEMDESC comes in different shapes depending on the typelib; pull the
    paramflags out of it."""
    if not isinstance(elem, (tuple, list)) or len(elem) < 2:
        return 0
    tail = elem[1]
    if isinstance(tail, int):
        return tail
    if isinstance(tail, (tuple, list)) and tail and isinstance(tail[0], int):
        return tail[0]
    return 0


def _walk_typeinfo(typeinfo, iface: str, out: dict) -> int:
    attr = typeinfo.GetTypeAttr()
    n = 0
    for i in range(attr.cFuncs):
        try:
            fd = typeinfo.GetFuncDesc(i)
            names = typeinfo.GetNames(fd.memid)
        except pythoncom.com_error:
            continue
        if not names:
            continue
        method = names[0]
        if method in _COM_PLUMBING:
            continue

        params = []
        for idx, elem in enumerate(fd.args):
            tname, is_ptr = _type_name(_unwrap_elem(elem), typeinfo)
            flags = _elem_flags(elem)
            pname = names[idx + 1] if idx + 1 < len(names) else f"arg{idx + 1}"
            params.append({
                "name": pname,
                "type": tname,
                # out-param: flagged by the paramflags, or by being a pointer
                "byref": bool(flags & PARAMFLAG_FOUT) or is_ptr,
            })

        returns, _ = _type_name(_unwrap_elem(fd.rettype), typeinfo)
        entry = {
            "interface": iface,
            "method": method,
            "returns": returns,
            "signature": f"{method}({', '.join(p['name'] for p in params)})",
            "params": params,
        }
        tag = _INVKIND_TAG.get(fd.invkind, "")
        if tag:
            entry["kind"] = tag          # property get/put -- no parens in early binding

        key = method.lower()
        bucket = out.setdefault(key, [])
        # dedup: same interface + same method + same arity
        if not any(e["interface"] == iface and e["method"] == method
                   and len(e["params"]) == len(params) and e.get("kind", "") == tag
                   for e in bucket):
            bucket.append(entry)
            n += 1
    return n


def _load_typelibs(verbose: bool = True) -> list:
    """Find the registered SolidWorks typelibs and load each one by path."""
    found = []
    for tlb in selecttlb.EnumTlbs():
        path = (tlb.dll or "")
        base = Path(path).name.lower()
        for pattern, _required in TLB_PATTERNS:
            if base.startswith(pattern) and base.endswith((".tlb", ".dll")):
                found.append((pattern, tlb, path))
                break

    # the same typelib may show up in several versions -> keep the HIGHEST one
    best = {}
    for pattern, tlb, path in found:
        ver = (int(str(tlb.major), 16) if not str(tlb.major).isdigit() else int(tlb.major),
               int(tlb.minor) if str(tlb.minor).isdigit() else 0)
        if pattern not in best or ver > best[pattern][0]:
            best[pattern] = (ver, tlb, path)

    loaded = []
    for pattern, (ver, tlb, path) in best.items():
        try:
            lib = pythoncom.LoadTypeLib(path)
        except pythoncom.com_error as exc:
            print(f"  [SKIP] {path}: {exc}", file=sys.stderr)
            continue
        if verbose:
            print(f"  typelib: {tlb.desc}  v{ver[0]}.{ver[1]}")
            print(f"           {path}")
        loaded.append((lib, tlb.desc))

    required = [p for p, req in TLB_PATTERNS if req]
    missing = [p for p in required if p not in best]
    if missing:
        raise RuntimeError(
            f"required typelib not found: {missing}. "
            "Is SolidWorks installed and registered on this machine?"
        )
    return loaded


def build(out_path: Path, verbose: bool = True) -> dict:
    pythoncom.CoInitialize()
    if verbose:
        print("Reading the registered SolidWorks typelibs...")
    index: dict = {}
    ifaces = 0
    for lib, _desc in _load_typelibs(verbose):
        for i in range(lib.GetTypeInfoCount()):
            try:
                typeinfo = lib.GetTypeInfo(i)
                attr = typeinfo.GetTypeAttr()
            except pythoncom.com_error:
                continue
            if attr.typekind not in (pythoncom.TKIND_INTERFACE, pythoncom.TKIND_DISPATCH):
                continue
            raw = lib.GetDocumentation(i)[0]
            if not raw:
                continue
            _walk_typeinfo(typeinfo, _iface_name(raw), index)
            ifaces += 1

    ordered = OrderedDict(sorted(index.items()))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ordered, indent=1, ensure_ascii=False), encoding="utf-8")

    total = sum(len(v) for v in ordered.values())
    if verbose:
        print(f"\n{len(ordered)} method names / {total} method-by-interface "
              f"from {ifaces} interfaces")
        print(f"written to {out_path}  ({out_path.stat().st_size / 1048576:.1f} MB)")
    return ordered


def compare(new: dict, old_path: Path) -> None:
    """Compare against the doc-derived index -- proof that nothing was lost."""
    old = json.loads(old_path.read_text(encoding="utf-8"))
    n_keys, a_keys = set(new), set(old)
    print(f"\n=== COMPARISON with {old_path.name} ===")
    print(f"  old (doc):        {len(a_keys)} names")
    print(f"  new (typelib):    {len(n_keys)} names")
    print(f"  in both:          {len(n_keys & a_keys)}")
    missing = sorted(a_keys - n_keys)
    print(f"  ONLY in the old:  {len(missing)}" +
          (f"  e.g.: {missing[:8]}" if missing else ""))
    added = sorted(n_keys - a_keys)
    print(f"  ONLY in the new:  {len(added)}" +
          (f"  e.g.: {added[:8]}" if added else ""))

    # spot-check: the beefiest method of the part workflow
    for probe in ("featureextrusion2", "featurecut4", "addmate5"):
        a = next((e for e in old.get(probe, []) if not e.get("kind")), None)
        n = next((e for e in new.get(probe, []) if not e.get("kind")), None)
        if not a or not n:
            print(f"  [{probe}] missing on one of the sides")
            continue
        same_n = len(a["params"]) == len(n["params"])
        same_names = [p["name"] for p in a["params"]] == [p["name"] for p in n["params"]]
        print(f"  [{probe}] args old={len(a['params'])} new={len(n['params'])} "
              f"{'OK' if same_n else 'DIVERGES'} | "
              f"names {'match' if same_names else 'differ'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).parent / "cache" / "api_signatures.json"))
    ap.add_argument("--compare", default="", help="old JSON to compare against "
                                                  "(optional)")
    args = ap.parse_args()

    new = build(Path(args.out))
    if args.compare:
        compare(new, Path(args.compare))


if __name__ == "__main__":
    main()
