"""
analyze_api_families.py
Maps the SolidWorks COM API (mcp_server/cache/api_signatures.json, ~7,300 names / 12,320
method-by-interface entries) into FAMILIES, so the curated MCP verbs are derived from
DATA rather than from a guess.

The idea: half the API is a getter (Get*/Is*/Access* -> collapses into a handful of
inspection verbs); the construction core (Create/Insert/Add/Feature) is what becomes a
verb. Each family -> either one consolidated verb, or (long tail / specialist) goes to
the escape hatch (sw_call) + the recipe RAG.

Usage:
  python mcp_server/analyze_api_families.py                 # global landscape
  python mcp_server/analyze_api_families.py FeatureManager  # the families of one interface
"""

import json
import re
import sys
import collections
from pathlib import Path

INDEX = Path(__file__).parent / "cache" / "api_signatures.json"

# interfaces by domain (what matters for the eco-marathon / suspension engine)
DOMAINS = {
    "part":     ["FeatureManager", "SketchManager", "Sketch", "PartDoc"],
    "assembly": ["AssemblyDoc", "Component2"],
    "drawing":  ["DrawingDoc", "View", "Note", "Sheet"],
    "inspect":  ["Body2", "Face2", "Edge", "MassProperty", "Measure"],
    "doc":      ["ModelDoc2", "ModelDocExtension", "SldWorks"],
}


def load():
    d = json.load(open(INDEX, encoding="utf-8"))
    return [e for lst in d.values() for e in lst]


def action_prefix(method: str) -> str:
    """The first CamelCase word, with no version suffix (AddMate5 -> Add)."""
    mm = re.sub(r"\d+$", "", method)
    parts = re.findall(r"[A-Z][a-z]+", mm)
    return parts[0] if parts else mm


def family_key(method: str) -> str:
    """The semantic family: drops the action prefix and the version suffix.
    FeatureExtrusion3 / FeatureExtrusionThin2 -> 'Extrusion' / 'ExtrusionThin'."""
    base = re.sub(r"\d+$", "", method)
    return re.sub(r"^(Insert|Feature|Create|Add|Get|Set|Is|Access)", "", base) or base


def landscape(entries):
    by_iface = collections.Counter(e["interface"] for e in entries)
    by_action = collections.Counter(action_prefix(e["method"]) for e in entries)
    print(f"entries: {len(entries)} | interfaces: {len(by_iface)}")
    reads = sum(n for a, n in by_action.items() if a in ("Get", "Is", "Access"))
    print(f"reads (Get/Is/Access): {reads}  "
          f"({100*reads//len(entries)}% -> collapses into inspection)")
    print("\nTOP interfaces:")
    for iface, n in by_iface.most_common(20):
        dom = next((k for k, v in DOMAINS.items() if iface in v), "")
        print(f"  {n:5}  {iface:22} {('['+dom+']') if dom else ''}")
    print("\nTOP actions:")
    for a, n in by_action.most_common(18):
        print(f"  {n:5}  {a}")


def interface_families(entries, iface):
    # arg_count per method (to measure how much the shape diverges inside the family)
    argc = {e["method"]: e.get("arg_count", 0)
            for e in entries if e["interface"] == iface}
    meths = sorted(argc)
    constr = [m for m in meths if re.match(r"(Insert|Feature|Create|Add|Hole)", m)]
    fam = collections.defaultdict(list)
    for m in constr:
        fam[family_key(m)].append(m)
    print(f"\n== {iface}: {len(meths)} methods, {len(constr)} of construction, "
          f"{len(fam)} fine families ==")
    print("  fine-family  |  args (shape)  |  #variants")
    print("  (group related fine families into a COARSE family = a namespace; if their arg")
    print("   shapes diverge a lot -> sub-verbs, otherwise -> 1 verb with a kind)\n")
    for k in sorted(fam):
        members = fam[k]
        latest = max(members, key=lambda m: argc[m])  # the most complete version
        print(f"  {k:24} args={argc[latest]:<3} {len(members)}v")


def main():
    entries = load()
    if len(sys.argv) > 1:
        interface_families(entries, sys.argv[1])
    else:
        landscape(entries)


if __name__ == "__main__":
    main()
