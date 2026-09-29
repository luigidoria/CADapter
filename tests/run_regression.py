# -*- coding: utf-8 -*-
r"""
The REGRESSION battery in one command: every live family + the V1 benchmarks, each in its
own process (a crash in one does not take the others down), and a PASS/FAIL table per
domain at the end -- the table the release notes quote, for whichever SOLIDWORKS answers.

This is opt-in extended validation, not an installation check (that is
tests/smoke_installation.py). It takes one to two hours and opens hundreds of documents.

Usage (from the repo root, SOLIDWORKS open, unrelated work saved, no other CAD client):
  .venv\Scripts\python.exe -u tests\run_regression.py                 # everything
  .venv\Scripts\python.exe -u tests\run_regression.py part drawing    # some families
  ... --out D:\somewhere     NEW or empty output root for this run (default: see below)
  ... --allow-restart        DESTRUCTIVE recovery, see below

Output. Each family writes under ONE output root for the run, so a later family reuses
what an earlier one built IN THIS RUN (the drawing family reads the clevis). In isolated
mode (the public default, solidworks/tests/isolation.py) the root is --out, or a new
temporary directory; in development it is tests/validation_assembly/. --out must be new
or empty: a non-empty one is refused (nothing is deleted), because a family reuses a
saved part or assembly when the file already exists. A CADAPTER_TEST_OUT inherited from
a parent runner (run_v1_battery.ps1, which made it fresh) is used as it is. The
per-family logs and summary.json go to <root>/regression/.

Documents. In isolated mode a family closes only the documents it created or opened.
Documents that were open before the run stay open, unsaved changes included.

Hangs. A family silent for STALL_MINUTES is stopped (this runner's own subprocess only)
and recorded as HUNG. Without --allow-restart nothing else happens: if SOLIDWORKS no
longer answers, every later family is recorded as FAIL ("did not answer"), and you
restart SOLIDWORKS yourself. With --allow-restart (the development default) the runner
force-kills EVERY SLDWORKS.exe the user can terminate -- all versions and instances,
discarding unsaved work in all of their documents -- clears the recovery journals and
relaunches it.

Results per family (tests/release_gate.py has the definitions): PASS, FAIL (a check
failed), ERROR (crash, nonzero exit or no summary), HUNG, NOT RUN (SOLIDWORKS did not
answer before it), INCOMPLETE (a [SKIP] or [KNOWN] line that release_gate does not
declare optional/accepted). An optional SKIP or accepted KNOWN leaves the family PASS
but is listed next to it and never counted as a passing check.

Exit code: 0 every family PASS; 1 any other result; 2 SOLIDWORKS did not answer at the
start, --out is not empty, or bad arguments.
"""
import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from solidworks.tests import isolation as T  # noqa: E402  (no COM at import)
from tests import release_gate as G  # noqa: E402  (no COM)

PY = sys.executable
# a suite that prints nothing for this long is taken as HUNG. Measured: after 36 minutes of
# the sheet-metal smoke, SOLIDWORKS stopped answering in the benchmark (no dialog open,
# ~20% of one core) and the run waited with no end; the same step alone took 71 s.
STALL_MINUTES = 15

# domain -> (label, script, args, regex that reads "ok/total" from the log). The smokes
# print "SUMMARY: ok/n PASS" or "ok/n PASS"; the last match in the log is the total.
# release_gate.family_result applies the same pattern.
_SMOKE = r"^(?:SUMMARY:\s*)?(\d+)/(\d+) PASS"
SUITES = {
    "part": ("Part + Sketch + Inspection", "solidworks/tests/smoke/smoke_parts.py", ["all"], _SMOKE),
    "assembly": ("Assembly", "solidworks/tests/smoke/smoke_assembly.py", [], _SMOKE),
    "clevis": ("Assembly (clevis joint)", "solidworks/tests/smoke/smoke_clevis.py", [], _SMOKE),
    "drawing": ("Drawing", "solidworks/tests/smoke/smoke_drawing.py", ["all"], _SMOKE),
    "sheetmetal": ("Sheet Metal", "solidworks/tests/smoke/smoke_sheetmetal.py", ["all"], _SMOKE),
    "benchmark": ("End-to-end benchmark A-D", "mcp_server/tests/benchmark_v1.py", [], _SMOKE),
    "drawings": ("Usable drawings (3 models)", "solidworks/tests/benchmark/benchmark_drawings.py", [],
                 _SMOKE),
}

# a check line, as every smoke prints it: "  [PASS] name" / "  [FAIL ] name"
_CHECK = re.compile(r"^\s*\[(PASS|FAIL)\s*\]")
# what a family declares it did NOT validate: an absent resource, or a version limitation
# ("[KNOWN SW2017] ..."). Echoed live; whether it is acceptable is release_gate's call.
_SKIP = re.compile(r"\[SKIP\]")
_KNOWN = re.compile(r"\[KNOWN\b")


def _probe_code(launch):
    return ("import sys; sys.path.insert(0, r'%s'); from solidworks import sw_com as C; "
            "print(C.connect(launch=%s).RevisionNumber())" % (ROOT, launch))


_RESTART = ("import sys; sys.path.insert(0, r'%s'); from solidworks import sw_com as C; "
            "print(C.restart().RevisionNumber())" % ROOT)


def _run_py(code, timeout):
    """Run a snippet in a fresh process; the revision it prints, or None."""
    try:
        r = subprocess.run([PY, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    out = r.stdout.strip().splitlines()
    return out[-1] if r.returncode == 0 and out else None


def ensure_sw(destructive):
    """The SW revision. Only with `destructive` does a silent SW get killed and relaunched;
    otherwise the probe only attaches -- it does not even launch a SW that is gone."""
    rev = _run_py(_probe_code(launch=destructive), 90)
    if rev or not destructive:
        return rev
    print("   SOLIDWORKS does not answer -- killing and relaunching it (--allow-restart) ...",
          flush=True)
    return _run_py(_RESTART, 240)


def _kill_sw():
    subprocess.run(["taskkill", "/F", "/IM", "SLDWORKS.exe"], capture_output=True)


def _pump(stream, q):
    for ln in stream:
        q.put(ln)
    q.put(None)


def run_suite(script, args, log, destructive):
    """Run one suite, echoing its check lines live and keeping the full output in `log`.
    A suite silent for STALL_MINUTES is stopped and gets a 'HUNG' line; SOLIDWORKS is
    killed with it only when `destructive`. Returns (exit code, full text, hung)."""
    lines, q, hung = [], queue.Queue(), False
    with open(log, "w", encoding="utf-8") as f:
        p = subprocess.Popen([PY, "-u", os.path.join(ROOT, script)] + args, cwd=ROOT,
                             env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=_pump, args=(p.stdout, q), daemon=True).start()
        last = time.time()
        while True:
            try:
                ln = q.get(timeout=10)
            except queue.Empty:
                if time.time() - last < STALL_MINUTES * 60:
                    continue
                hung = True
                msg = (f"HUNG: no output for {STALL_MINUTES} min -- suite "
                       + ("and SOLIDWORKS killed" if destructive else
                          "stopped; SOLIDWORKS left running (restart it yourself if it "
                          "does not answer)"))
                print(f"   {msg}", flush=True)
                f.write(msg + "\n")
                lines.append(msg)
                p.kill()                      # our own child process
                if destructive:
                    _kill_sw()
                break
            if ln is None:
                break
            last = time.time()
            f.write(ln)
            f.flush()
            lines.append(ln.rstrip("\n"))
            s = ln.strip()
            # live view: the checks, the block headers and anything that looks like a crash
            # ... and what a suite declares it did NOT validate ([SKIP], [KNOWN SW2017]):
            # a skipped case must show up next to the PASS lines, not only in the full log
            if _CHECK.match(ln) or s.startswith("==") or "Error" in s or "Traceback" in s \
                    or _SKIP.search(s) or _KNOWN.search(s):
                print(f"   {s}", flush=True)
        code = p.wait()
    return code, lines, hung


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suites", nargs="*", help=f"families to run (default: all): {list(SUITES)}")
    ap.add_argument("--out", help="NEW or empty output root for this run (created if missing)")
    ap.add_argument("--allow-restart", action="store_true",
                    help="DESTRUCTIVE: on a hang, force-kill every SLDWORKS.exe and relaunch it")
    a = ap.parse_args(argv)
    which = a.suites or list(SUITES)
    unknown = [k for k in which if k not in SUITES]
    if unknown:
        print(f"unknown suite(s) {unknown}; choose from {list(SUITES)}")
        return 2
    # the development runner always recovered this way; isolated mode needs the flag
    destructive = a.allow_restart or not T.isolated()
    if a.out:
        problem = G.fresh_output_problem(os.path.abspath(a.out))
        if problem:
            print(problem)
            return 2
        os.environ[T.ENV_OUT] = os.path.abspath(a.out)
    out = os.path.join(T.output_root(), "regression")   # exported to every family run
    os.makedirs(out, exist_ok=True)
    if destructive:
        print("!! --allow-restart: a hang force-kills EVERY SLDWORKS.exe (all versions and "
              "instances, unsaved work lost) and relaunches it", flush=True)
    rev = ensure_sw(destructive)
    if not rev:
        print("SOLIDWORKS does not answer over COM -- start it (and close any dialog), then "
              "run again" + ("" if destructive else "; nothing was killed or relaunched"))
        return 2
    print(f"SOLIDWORKS rev {rev} | suites: {which} | isolated: {T.isolated()} | "
          f"output: {os.path.dirname(out)}", flush=True)
    table = {}
    for key in which:
        label, script, args, _ = SUITES[key]
        log = os.path.join(out, f"{key}.log")
        t0 = time.time()
        print(f"\n== {label} ({script}) ...", flush=True)
        if not ensure_sw(destructive):
            table[key] = dict(G.family_not_run("SOLIDWORKS did not answer before the suite"),
                              label=label, minutes=0.0)
            print(f"   {G.NOT_RUN}: SOLIDWORKS did not answer before the suite", flush=True)
            continue
        code, lines, hung = run_suite(script, args, log, destructive)
        # the verdict (and what SKIP/KNOWN may be accepted) lives in release_gate, where
        # it is tested without SOLIDWORKS
        table[key] = dict(G.family_result(key, code, lines, hung), label=label,
                          minutes=round((time.time() - t0) / 60, 1))
        r = table[key]
        tail = f" -- {r['crash']}" if r["crash"] else ""
        print(f"   {r['result']} {r['checks']} ({r['minutes']} min){G.family_notes(r)}{tail}",
              flush=True)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"solidworks_revision": rev, "isolated": T.isolated(), "suites": table},
                  f, indent=1)
    print(f"\nSOLIDWORKS rev {rev}")
    for key, r in table.items():
        print(f"{r['label']:<30} {r['result']:<10} {r['checks']}{G.family_notes(r)}")
        for k, tag in (("failures", ""), ("skip_required", "REQUIRED "),
                       ("known_unaccepted", "NOT ACCEPTED "), ("skip_optional", "optional "),
                       ("known_accepted", "accepted ")):
            for ln in r[k]:
                print(f"    {tag}{ln}")
        if r["crash"]:
            print(f"    CRASH: {r['crash']}")
    print(G.regression_tally(table))
    n_ok = sum(r["result"] == G.PASS for r in table.values())
    print(f"{n_ok}/{len(table)} suites PASS")
    return 0 if n_ok == len(table) else 1


if __name__ == "__main__":
    sys.exit(main())
