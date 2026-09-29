# -*- coding: utf-8 -*-
r"""
The release gate's verdicts, in one place and with no SOLIDWORKS: what counts as a PASS
for a compatibility report, for a regression family and for the acceptance battery.
Every function here is pure (or touches only the paths it is given), so the semantics
are tested by tests/test_release_gate.py without CAD.

The results, per step (version battery) or per check/case (regression families):

  PASS     it ran, and the check it carries held. Only this is counted as a pass.
  FAIL     it ran and a check did not hold (a failed expectation).
  ERROR    it raised, crashed or exited nonzero with no verdict of its own.
  SKIP     it did not run because a resource is absent or the operator asked. Accepted
           only when the reason is declared optional below; otherwise the run is
           INCOMPLETE. Never counted as a pass.
  KNOWN    a declared version limitation reproduced. Accepted only when declared (the
           scenario's `known` map, or KNOWN_ACCEPTED below); never counted as a pass.
  NOT RUN  it should have run and did not (the scenario stopped earlier, or SOLIDWORKS
           did not answer before the family). Always a failure of a required run.

Two versions producing the same ERROR does not make it acceptable: nothing here reads
the other version's report.

The command-line helpers are what tests/run_v1_battery.ps1 calls, so the PowerShell
keeps the orchestration and the decisions stay here, testable:
  python tests\release_gate.py fresh-out <dir>
  python tests\release_gate.py revision <expected> <observed>
  python tests\release_gate.py compare --report <json> --rev <rev> --out <dir>
Exit code: 0 accepted, 1 refused/failed, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL, ERROR, SKIP, KNOWN, NOT_RUN = "PASS", "FAIL", "ERROR", "SKIP", "KNOWN", "NOT RUN"
RESULTS = (PASS, FAIL, ERROR, SKIP, KNOWN, NOT_RUN)


# ── compatibility report (tests/version/suite_version.py) ─────────────────────
def classify_steps(scenario: dict, n_steps: int, known: dict | None = None) -> list:
    """(step index, verb, result, detail) for every step the scenario DECLARES, including
    the ones it never reached. `known` is the scenario's explicit {step index: reason};
    an error or failed expectation at a declared index is KNOWN, anywhere else it stands."""
    known = {int(k): v for k, v in (known or {}).items()}
    out = []
    for p in scenario.get("steps", []):
        i, verb = p.get("i"), p.get("verb")
        if not p.get("ok"):
            res, detail = ERROR, p.get("error", "")
        elif p.get("expect_failed"):
            res, detail = FAIL, "; ".join(map(str, p["expect_failed"]))
        else:
            res, detail = PASS, ""
        if res in (ERROR, FAIL) and i in known:
            res, detail = KNOWN, f"{known[i]} ({detail})"
        out.append((i, verb, res, detail))
    for i in range(len(scenario.get("steps", [])), n_steps):
        out.append((i, None, NOT_RUN, f"the scenario stopped at step {scenario.get('parou_em')}"))
    return out


def suite_verdict(report: dict, declared: dict) -> dict:
    """Counts per result and the exit code of a completed compatibility run.

    `declared` maps scenario name -> (number of steps, known map). KNOWN is accepted
    because it was declared step by step, with a reason; ERROR, FAIL and NOT RUN are not."""
    counts = dict.fromkeys(RESULTS, 0)
    findings = []
    for sc in report.get("scenarios", []):
        n, known = declared.get(sc["name"], (len(sc.get("steps", [])), {}))
        for i, verb, res, detail in classify_steps(sc, n, known):
            counts[res] += 1
            if res != PASS:
                findings.append((sc["name"], i, verb, res, detail))
    bad = counts[ERROR] + counts[FAIL] + counts[NOT_RUN]
    return {"counts": counts, "findings": findings, "exit_code": 1 if bad else 0}


def format_counts(counts: dict) -> str:
    return "  ".join(f"{counts.get(k, 0)} {k}" for k in RESULTS)


# ── regression family (tests/run_regression.py) ───────────────────────────────
# What a family may leave out and still PASS. A [SKIP] or [KNOWN ...] line whose text
# matches none of these makes the family INCOMPLETE. Declared here, per family, so that
# accepting one is a reviewed change to this file and not a string a smoke chose to print.
SKIP_OPTIONAL = {
    # resources installed with SOLIDWORKS under lang/<language>/ -- absent on some installs
    "sheetmetal": [r"gauge table -- none installed", r"sample\.btl -- not installed",
                   r"base bend table\.xls -- not installed"],
    # the operator declared the machine cannot carry the case (CADAPTER_DRAWINGS_SKIP)
    "drawings": [r"CADAPTER_DRAWINGS_SKIP"],
}
KNOWN_ACCEPTED = {
    # MEASURED on SW 25.3.0 (probe_sw17_v1.py): the bend table breaks the flat pattern in
    # ~40% of documents; the family then checks the verb's refusal and rollback instead
    "sheetmetal": [r"^\[KNOWN SW2017\] the bend table was refused on",
                   r"^\[KNOWN SW2017\] sample\.btl refused",
                   r"^\[KNOWN SW2017\] the table was refused on the miter flange"],
}

_CHECK = re.compile(r"^\s*\[(PASS|FAIL)\s*\]")
_SKIP = re.compile(r"\[SKIP\]")
_KNOWN = re.compile(r"\[KNOWN\b")
_SUMMARY = re.compile(r"^(?:SUMMARY:\s*)?(\d+)/(\d+) PASS", re.MULTILINE)


def family_result(family: str, code, lines: list, hung: bool = False) -> dict:
    """The verdict of one family from its exit code and full output.

    PASS needs ALL of: exit 0, not hung, a summary line, at least one check, every check
    PASS, and every SKIP/KNOWN line declared acceptable above. The check counts are the
    [PASS]/[FAIL] lines themselves, never a fraction whose denominator left cases out."""
    text = "\n".join(lines)
    found = _SUMMARY.findall(text)
    n_pass = sum(1 for ln in lines if _CHECK.match(ln) and "PASS" in ln)
    n_fail = sum(1 for ln in lines if _CHECK.match(ln) and "FAIL" in ln)
    skips = [ln.strip() for ln in lines if _SKIP.search(ln)]
    knowns = [ln.strip() for ln in lines if _KNOWN.search(ln)]
    opt = SKIP_OPTIONAL.get(family, [])
    acc = KNOWN_ACCEPTED.get(family, [])
    skip_required = [s for s in skips if not any(re.search(p, s) for p in opt)]
    known_unaccepted = [k for k in knowns if not any(re.search(p, k) for p in acc)]
    ok, total = (int(found[-1][0]), int(found[-1][1])) if found else (n_pass, n_pass + n_fail)
    crash = ""
    if not found or code != 0:
        tail = [ln.strip() for ln in lines if ln.strip()]
        crash = tail[-1] if tail else f"exit code {code}, no output"
    if hung:
        result = "HUNG"
    elif n_fail or (found and ok != total):
        result = FAIL
    elif code != 0 or not found or total == 0 or n_pass == 0:
        result = ERROR
    elif skip_required or known_unaccepted:
        result = "INCOMPLETE"
    else:
        result = PASS
    return {"result": result, "checks": f"{ok}/{total}", "exit_code": code,
            "passed": n_pass, "failed": n_fail, "crash": crash,
            "skip_optional": [s for s in skips if s not in skip_required][:20],
            "skip_required": skip_required[:20],
            "known_accepted": [k for k in knowns if k not in known_unaccepted][:20],
            "known_unaccepted": known_unaccepted[:20],
            "failures": [ln.strip() for ln in lines if "[FAIL" in ln][:20]}


def family_not_run(reason: str) -> dict:
    return {"result": NOT_RUN, "checks": "0/0", "exit_code": None, "passed": 0, "failed": 0,
            "crash": reason, "skip_optional": [], "skip_required": [], "known_accepted": [],
            "known_unaccepted": [], "failures": []}


def family_notes(r: dict) -> str:
    notes = []
    for key, label in (("skip_optional", "SKIP optional"), ("skip_required", "SKIP REQUIRED"),
                       ("known_accepted", "KNOWN accepted"),
                       ("known_unaccepted", "KNOWN NOT ACCEPTED")):
        if r.get(key):
            notes.append(f"{len(r[key])} {label}")
    return f"  ({', '.join(notes)})" if notes else ""


def regression_tally(table: dict) -> str:
    """One line with the totals the release notes quote -- every result kind, apart."""
    fam = {}
    for r in table.values():
        fam[r["result"]] = fam.get(r["result"], 0) + 1
    checks_pass = sum(r["passed"] for r in table.values())
    checks_fail = sum(r["failed"] for r in table.values())
    skip = sum(len(r["skip_optional"]) + len(r["skip_required"]) for r in table.values())
    known = sum(len(r["known_accepted"]) + len(r["known_unaccepted"]) for r in table.values())
    fams = ", ".join(f"{n} {k}" for k, n in sorted(fam.items()))
    return (f"families: {fams} | checks: {checks_pass} PASS, {checks_fail} FAIL | "
            f"{skip} SKIP | {known} KNOWN")


# ── acceptance battery (tests/run_v1_battery.ps1) ─────────────────────────────
def fresh_output_problem(path: str) -> str | None:
    """None when `path` may hold a NEW acceptance run: absent, or an empty directory.
    Nothing is ever deleted -- a reused directory is refused, because the families reuse
    a saved part or assembly when the file already exists."""
    if not os.path.exists(path):
        return None
    if not os.path.isdir(path):
        return f"Output path exists and is not a directory: {path}"
    if os.listdir(path):
        return (f"Output directory is not empty: {path}. "
                "Use a new directory for release validation.")
    return None


def revision_matches(expected: str, observed: str) -> bool:
    """`expected` names the leading components of RevisionNumber: '25' accepts any
    25.x.y (SOLIDWORKS 2017), '25.3.0' only that one. Component-wise, so '25.3' does not
    accept '25.30.0'."""
    exp = [x for x in str(expected).strip().split(".") if x != ""]
    obs = str(observed).strip().split(".")
    return bool(exp) and exp == obs[:len(exp)]


def compare_phase(report: str, rev: str, out_dir: str, reports_dir: str | None = None,
                  run=None) -> tuple[int, list]:
    """Compare this run's report against every OTHER committed baseline. A requested
    comparison that cannot happen (no report, no baseline) is a failure, not a pass.
    Returns (exit code, messages). `run(argv) -> exit code` is injectable for tests."""
    reports_dir = reports_dir or os.path.join(ROOT, "tests", "version", "reports")
    run = run or (lambda argv: subprocess.run(argv, cwd=ROOT).returncode)
    tag = "sw" + str(rev).strip().replace(".", "_")
    msgs, code = [], 0
    if not os.path.isfile(report):
        return 1, [f"compare: FAIL -- no compatibility report from this run ({report})"]
    refs = sorted(p for p in glob.glob(os.path.join(reports_dir, "BASELINE_sw*.json"))
                  if os.path.basename(p) != f"BASELINE_{tag}.json")
    if not refs:
        return 1, [f"compare: FAIL -- no baseline of another version in {reports_dir}"]
    for ref in refs:
        # REF = SW 2017, NEW = SW 2026 (compare_version's convention)
        a, b = (report, ref) if str(rev).startswith("25.") else (ref, report)
        name = os.path.splitext(os.path.basename(ref))[0]
        rc = run([sys.executable, "-u", os.path.join(ROOT, "tests", "version",
                                                     "compare_version.py"),
                  a, b, "--json", os.path.join(out_dir, f"compare_{name}.json")])
        msgs.append(f"compare {name}: {'PASS' if rc == 0 else f'FAIL (exit {rc})'}")
        code = code or (1 if rc != 0 else 0)
    return code, msgs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fresh-out")
    p.add_argument("path")
    p = sub.add_parser("revision")
    p.add_argument("expected")
    p.add_argument("observed")
    p = sub.add_parser("compare")
    p.add_argument("--report", required=True)
    p.add_argument("--rev", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "fresh-out":
        problem = fresh_output_problem(a.path)
        print(problem or f"output directory OK (new or empty): {a.path}")
        return 1 if problem else 0
    if a.cmd == "revision":
        print(f"Expected SolidWorks revision: {a.expected}")
        print(f"Observed SolidWorks revision: {a.observed}")
        if revision_matches(a.expected, a.observed):
            return 0
        print("ERROR: wrong SolidWorks version is active.")
        return 1
    code, msgs = compare_phase(a.report, a.rev, a.out)
    for m in msgs:
        print(m, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
