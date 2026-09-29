# -*- coding: utf-8 -*-
r"""
compare_version.py -- a diff between two `suite_version.py` reports.

The suite's oracle is not in it: it is in the OTHER SolidWorks version. This script
lines the two reports up step by step and says where they disagree -- and, what matters
more, SEPARATES "it stopped working on the 2026" from "it never worked".

It needs neither SolidWorks nor the server: it reads two JSON files and prints.

Uso:
  .venv\Scripts\python.exe tests\version\compare_version.py REF.json NOVO.json
  .venv\Scripts\python.exe tests\version\compare_version.py REF.json NOVO.json --all
  .venv\Scripts\python.exe tests\version\compare_version.py REF.json NOVO.json --json saida.json

REF is the baseline (the SW 2017); NEW is what is being validated (the SW 2026).
Exit code 0 when there is no relevant divergence.
"""

from __future__ import annotations

import argparse
import json
import sys

# a RELATIVE tolerance for volume/area. The same sequence of operations should give
# the same solid: this is slack for the kernel's last digit, not for "nearly equal".
TOL_REL = 1e-4
TOL_ABS_MM = 0.01

# order of severity -- what appears first in the report
SEVERITY = {
    "REGRESSION": 0,       # worked on the REF, broke (or stopped doing it) on the NEW
    "OLD_BUG": 1,          # delivers wrongly on BOTH -- not from the version change
    "FIX": 2,              # broken on the REF, works on the NEW
    "GEOMETRY": 3,         # both executed, the solid came out different
    "ASSEMBLY": 4,         # position/mates diverged
    "DIFFERENT_ERROR": 5,  # both failed, for different reasons
    "UNVERIFIED": 5,       # the fingerprint died: the step went unchecked on a side
    "RESULT": 6,           # the returned value changed
    "FILE": 7,             # a generated file vanished or changed size a lot
    "COVERAGE": 8,         # a scenario/step exists on one side only
}


def _num_differs(a, b) -> bool:
    if a is None or b is None:
        return a is not b
    return abs(a - b) > max(TOL_REL * max(abs(a), abs(b)), TOL_ABS_MM)


def _list_differs(a, b) -> bool:
    a, b = a or [], b or []
    if len(a) != len(b):
        return True
    return any(_num_differs(x, y) for x, y in zip(a, b))


def compare_signature_part(ref: dict, new: dict) -> list[str]:
    """Diferencas de SOLIDO, em linguagem de engenheiro."""
    d = []
    for field, unit in (("volume_mm3", "mm3"), ("area_mm2", "mm2")):
        if _num_differs(ref.get(field), new.get(field)):
            d.append(f"{field}: {new.get(field)} {unit} "
                     f"(reference {ref.get(field)})")
    if ref.get("corpos") != new.get("corpos"):
        d.append(f"bodies: {new.get('corpos')} (reference {ref.get('corpos')})")
    if _list_differs(ref.get("com_mm"), new.get("com_mm")):
        d.append(f"centre of mass: {new.get('com_mm')} "
                 f"(reference {ref.get('com_mm')})")
    ca, cb = ref.get("caixa_mm"), new.get("caixa_mm")
    partial = ref.get("caixa_parcial") or new.get("caixa_parcial")
    if bool(ca) != bool(cb):
        d.append("one of the two has no solid")
    elif partial and _list_differs(ca, cb):
        # MULTI-BODY: the box came from a face list that covers ONE body, and which one
        # is not stable across versions. Volume, area and the body count above already
        # compare the WHOLE part, so nothing is lost by staying quiet here -- whereas
        # reporting it cost the 2026-09-21 battery one of its two "real differences".
        pass
    elif ca and _list_differs(ca, cb):
        dims = lambda c: [round(c[i + 3] - c[i], 2) for i in range(3)]  # noqa: E731
        # SIZE and POSITION are different findings, and printing the size for a box that
        # only MOVED reads as a broken tool: `gab_solid_combine` reported
        # "box: [60, 20, 60] (reference [60, 20, 60])" on 2026-09-21 -- two identical
        # triples and no hint that the solid sat 40 mm away in X. The comparison is on
        # the CORNERS, so say which of the two actually moved.
        if dims(ca) == dims(cb):
            delta = [round(cb[i] - ca[i], 2) for i in range(3)]
            d.append(f"box of the SAME size {dims(cb)} in ANOTHER POSITION: "
                     f"origin {cb[:3]} (reference {ca[:3]}), displaced {delta}")
        else:
            d.append(f"box: {dims(cb)} (reference {dims(ca)})")
    if ref.get("faces") != new.get("faces"):
        d.append(f"faces by kind: {new.get('faces')} "
                 f"(reference {ref.get('faces')})")
    ra, rb = ref.get("raios_mm") or [], new.get("raios_mm") or []
    if _list_differs(ra, rb):
        # the empty-pattern case lands exactly here: 1 radius instead of 3
        d.append(f"cylindrical radii: {rb} (reference {ra})"
                 + ("  <- a different number of holes/fillets"
                    if len(ra) != len(rb) else ""))
    aa, ab = ref.get("arestas") or {}, new.get("arestas") or {}
    if aa != ab:
        d.append(f"edges: {ab} (reference {aa})")
    return d


def compare_signature_asm(ref: dict, new: dict) -> list[str]:
    """ASSEMBLY differences: where each part ended up, and the mates' health."""
    d = []
    if ref.get("n_componentes") != new.get("n_componentes"):
        d.append(f"componentes: {new.get('n_componentes')} "
                 f"(referencia {ref.get('n_componentes')})")
    for field in ("interferencias", "erros_de_mate"):
        if ref.get(field) != new.get(field):
            d.append(f"{field}: {new.get(field)} (referencia {ref.get(field)})")
    if ref.get("mates") != new.get("mates"):
        d.append(f"mates: {new.get('mates')} (reference {ref.get('mates')})")
    for a, b in zip(ref.get("components") or [], new.get("components") or []):
        # case-insensitive, and here as WELL as in the fingerprint: the suite now writes
        # the name in lowercase, but the reports already recorded (and any report from an
        # older commit) carry whatever their SolidWorks answered -- `.SLDPRT` on 2026,
        # `.sldprt` on 2017. Comparing them as typed turned two identical assemblies into
        # 28 findings; normalising only on the writing side would have left every report
        # made before today uncomparable.
        if str(a.get("file") or "").lower() != str(b.get("file") or "").lower():
            d.append(f"the component order changed: {b.get('file')} "
                     f"where there was {a.get('file')}")
            continue
        if _list_differs(a.get("centro_mm"), b.get("centro_mm")):
            d.append(f"{b['file']}: centro {b.get('centro_mm')} "
                     f"(referencia {a.get('centro_mm')})")
        if a.get("fixo") != b.get("fixo"):
            d.append(f"{b['file']}: fixo={b.get('fixo')} "
                     f"(referencia {a.get('fixo')})")
        if a.get("refs") != b.get("refs"):
            d.append(f"{b['file']}: referencias nomeadas {b.get('refs')} "
                     f"(referencia {a.get('refs')})")
    return d


def compare_step(scen: str, pa: dict, pb: dict) -> list[dict]:
    """Divergencias de UM passo. `pa` e a referencia, `pb` o novo."""
    findings = []

    def add(kind, msg):
        findings.append({"kind": kind, "scenario": scen, "step": pa.get("i"),
                        "verb": pa.get("verb"), "detail": msg})

    if pa.get("verb") != pb.get("verb"):
        add("COVERAGE", f"o passo {pa.get('i')} e outro verbo no novo: "
                         f"{pb.get('verb')} (referencia {pa.get('verb')})")
        return findings

    ok_a, ok_b = bool(pa.get("ok")), bool(pb.get("ok"))
    if ok_a and not ok_b:
        add("REGRESSION", f"funcionava e agora falha: {pb.get('error')}")
        return findings
    if not ok_a and ok_b:
        add("FIX", f"used to fail and now works (the error was: {pa.get('error')})")
        return findings
    if not ok_a and not ok_b:
        if pa.get("error") != pb.get("error"):
            add("DIFFERENT_ERROR", f"{pb.get('error')}  (referencia: {pa.get('error')})")
        return findings

    # Both executed with no error. The remaining question is whether they DID what was
    # asked -- and that is where the answer the user wants lives: a verb that delivers
    # wrongly on BOTH versions is not a 2026 regression, it is a defect that was always there.
    fa, fb = pa.get("expect_failed") or [], pb.get("expect_failed") or []
    if fa and fb:
        add("OLD_BUG", "executes with no error and does NOT do what was asked on BOTH "
                       "versions -- it is not a regression: " + "; ".join(fb))
    elif fb and not fa:
        add("REGRESSION", "executes with no error but stopped doing what was asked: "
                          + "; ".join(fb))
    elif fa and not fb:
        add("FIX", "did not do what was asked on the reference and now does: "
                   + "; ".join(fa))

    if pa.get("resultado") != pb.get("resultado"):
        add("RESULT", f"devolveu {pb.get('resultado')!r} "
                         f"(referencia {pa.get('resultado')!r})")

    # A fingerprint that DIED is not a light finding: that step went unverified, and an
    # unverified step looks exactly like a passing one. It used to land as COVERAGE,
    # which is hidden without `--all` -- so the run that lost every assembly signature
    # on SW 2017 showed nine confident FIXes and not one word about the cause.
    # ASYMMETRY is the signal, not the failure itself: right after `part.new_part` there
    # is no solid yet, so the fingerprint legitimately has nothing to read -- and that
    # happens identically on both sides, which is noise (227 of them on the first run of
    # this check). What matters is one side verifying a step the other did not.
    sig_fail_a, sig_fail_b = pa.get("assinatura_falhou"), pb.get("assinatura_falhou")
    if bool(sig_fail_a) != bool(sig_fail_b):
        side, msg = ("reference", sig_fail_a) if sig_fail_a else ("new", sig_fail_b)
        add("UNVERIFIED", f"the fingerprint failed ONLY on the {side} -- that side did "
                          f"not verify this step, so a verdict on it is not "
                          f"comparable: {msg}")

    sa, sb = pa.get("signature"), pb.get("signature")
    if sa and sb:
        is_asm = "components" in sa or "components" in sb
        diffs = (compare_signature_asm(sa, sb) if is_asm
                else compare_signature_part(sa, sb))
        for msg in diffs:
            add("ASSEMBLY" if is_asm else "GEOMETRY", msg)
    elif bool(sa) != bool(sb):
        add("COVERAGE", "only one of the sides recorded a signature on this step")
    return findings


def compare(ref: dict, new: dict) -> list[dict]:
    findings = []
    by_name_a = {c["name"]: c for c in ref.get("scenarios", [])}
    by_name_b = {c["name"]: c for c in new.get("scenarios", [])}

    for name in sorted(set(by_name_a) | set(by_name_b)):
        a, b = by_name_a.get(name), by_name_b.get(name)
        if a is None or b is None:
            side = "only on the new" if a is None else "only on the reference"
            findings.append({"kind": "COVERAGE", "scenario": name, "step": None,
                            "verb": "", "detail": f"the scenario exists {side}"})
            continue
        pa, pb = a.get("steps", []), b.get("steps", [])
        for i in range(max(len(pa), len(pb))):
            if i >= len(pa) or i >= len(pb):
                who = "novo" if i >= len(pa) else "reference"
                findings.append({
                    "kind": "COVERAGE", "scenario": name, "step": i, "verb": "",
                    "detail": f"the {who} has step {i} and the other does not "
                               "(one of the two stopped earlier)"})
                continue
            findings += compare_step(name, pa[i], pb[i])

        for file in sorted(set(a.get("arquivos") or {}) | set(b.get("arquivos") or {})):
            ta = (a.get("arquivos") or {}).get(file)
            tb = (b.get("arquivos") or {}).get(file)
            if (ta is None) != (tb is None):
                # it only matters when the two sides DISAGREE. A file that came out on
                # neither is not a divergence -- it is the step that failed, already
                # counted as an error. (Without this `!=`, comparing a report with
                # itself accused a divergence: caught in the self-test.)
                findings.append({"kind": "FILE", "scenario": name, "step": None,
                                "verb": "", "detail":
                                f"{file}: gerado={tb is not None} "
                                f"(referencia gerado={ta is not None})"})
            elif ta and abs(ta - tb) > 0.25 * max(ta, tb):
                findings.append({"kind": "FILE", "scenario": name, "step": None,
                                "verb": "", "detail":
                                f"{file}: {tb} bytes (referencia {ta}) "
                                "-- mais de 25% de diferenca"})
    return findings


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference", help="the baseline report (e.g. SW 2017)")
    ap.add_argument("new", help="the report to validate (e.g. SW 2026)")
    ap.add_argument("--all", action="store_true",
                    help="also shows RESULT/FILE/COVERAGE (by default only what matters)")
    ap.add_argument("--json", default="", help="writes the findings to this file")
    a = ap.parse_args(argv)

    ref = json.load(open(a.reference, encoding="utf-8"))
    new = json.load(open(a.new, encoding="utf-8"))

    ma, mb = ref.get("meta", {}), new.get("meta", {})
    print("=" * 74)
    print(f"REFERENCE   SW {ma.get('sw_rev')}  {ma.get('machine')}  "
          f"commit {ma.get('git_commit')}  {ma.get('generated_at')}")
    print(f"NEW         SW {mb.get('sw_rev')}  {mb.get('machine')}  "
          f"commit {mb.get('git_commit')}  {mb.get('generated_at')}")
    print("=" * 74)
    if ma.get("git_commit") and ma.get("git_commit") != mb.get("git_commit"):
        print("!! WARNING: the reports came from DIFFERENT commits of the repo.")
        print("   A difference may be from our code, not from SolidWorks.")
    if ma.get("server_verbs") != mb.get("server_verbs"):
        print(f"!! ATENCAO: catalogs de verbo diferentes -- "
              f"{ma.get('server_verbs')} vs {mb.get('server_verbs')}")

    findings = compare(ref, new)
    strong = {"REGRESSION", "OLD_BUG", "FIX", "GEOMETRY", "ASSEMBLY",
              "DIFFERENT_ERROR", "UNVERIFIED"}
    show = findings if a.all else [x for x in findings if x["kind"] in strong]
    show.sort(key=lambda x: (SEVERITY.get(x["kind"], 9), x["scenario"],
                                x["step"] if x["step"] is not None else -1))

    if not show:
        print("\nNenhuma divergencia relevante. As duas versoes se comportaram igual.")
    else:
        current = None
        for x in show:
            if x["kind"] != current:
                current = x["kind"]
                print(f"\n### {current}")
            where = f"{x['scenario']}"
            if x["step"] is not None:
                where += f" passo {x['step']} ({x['verb']})"
            print(f"  {where}\n      {x['detail']}")

    count = {}
    for x in findings:
        count[x["kind"]] = count.get(x["kind"], 0) + 1
    print(f"\n{'=' * 74}\nRESUMO: " + (", ".join(
        f"{k}={v}" for k, v in sorted(count.items(),
                                      key=lambda kv: SEVERITY.get(kv[0], 9)))
        or "nada"))
    if not a.all and len(findings) != len(show):
        print(f"({len(findings) - len(show)} achados leves ocultos -- use --all)")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(findings, fh, indent=2, ensure_ascii=False)
        print(f"achados gravados em {a.json}")

    return 1 if any(x["kind"] in strong for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
