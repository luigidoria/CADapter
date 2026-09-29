# Tests

CADapter is validated in four levels, from "no SOLIDWORKS needed" to "reproduce the
cross-version evidence". Start at the top and stop where your question is answered.
Commands run from the repository root, in the Windows virtual environment
(`.venv\Scripts\python.exe`) unless a section says otherwise. The prerequisites and the
full procedure are in the [testing guide](../docs/TESTING.md).

The frozen V1 result is 543/543 required compatibility steps on both SOLIDWORKS 2017
(25.3.0) and SOLIDWORKS 2026 (34.3.2), using code commit
`2490209328ffc48212dc7900d65d3ead6a173085`. The complete regression counts and the
investigated cross-version differences are in the
[V1 validation record](../docs/TESTING.md#frozen-v1-validation).

| Level | Needs SOLIDWORKS | Touches your open documents | Time |
|---|---|---|---|
| 1. Deterministic | no | no | seconds |
| 2. Installation smoke | yes | no: creates and closes one unsaved part | < 1 min |
| 3. Integration / regression | yes | no in the default isolated mode (see below) | 1-2 h |
| 4. Compatibility | yes | no in the default isolated mode | ~1 h per version |

## 1. Deterministic checks

### Cross-platform, no CAD

Stdlib only; runnable on Linux or macOS as well as Windows.

```powershell
python -m unittest tests.test_modularity          # configuration, module boundaries, test safety rules
python mcp_server\tests\test_knowledge.py         # optional-RAG and signature-cache boundary
python tests\version\check_compare.py             # the version comparator against known findings
python -m unittest tests.test_release_gate        # PASS/SKIP/KNOWN semantics and exit codes of the gate
```

### Windows, no live CAD

These never connect to SOLIDWORKS, but they import the curated `solidworks/` layer and
therefore need Windows with pywin32 installed (`requirements.txt`).

```powershell
.venv\Scripts\python.exe solidworks\tests\unit\check_catalog.py          # catalog contract assertions, no live CAD
.venv\Scripts\python.exe solidworks\tests\unit\check_component_refs.py   # language-independent datum filtering
.venv\Scripts\python.exe tests\version\check_scenarios.py                # every battery step is a valid verb call
```

The catalog script above currently reports `61/61 PASS`, meaning 61 contract
assertions passed. The API separately contains 193 registered curated functions.
Neither number is the compatibility suite's step count or a count of live CAD tests.

## 2. Installation smoke

Answers "is CADapter installed and talking to my SOLIDWORKS?" with one disposable part.

```powershell
.venv\Scripts\python.exe -u tests\smoke_installation.py            # SOLIDWORKS already open
.venv\Scripts\python.exe -u tests\smoke_installation.py --launch   # start it if it is not
```

It attaches, prints the SOLIDWORKS revision, creates a new part, extrudes a 40 x 30 x
10 mm block, rebuilds and validates it, measures its volume and height, and closes that
part without saving. It saves nothing, never closes another document and never restarts
SOLIDWORKS. Exit code 0 = passed, 1 = a check or the cleanup failed, 2 = could not run.

## 3. Integration / regression (opt-in)

Long live validation of the advertised verbs, through Python and through the MCP entry
point. Save your work first and close other CAD clients (MCP servers, agents).

```powershell
.venv\Scripts\python.exe -u tests\run_regression.py                        # all seven families
.venv\Scripts\python.exe -u tests\run_regression.py part drawing --out D:\cadapter-run
```

| Family | Script |
|---|---|
| part + sketch + inspection | `solidworks/tests/smoke/smoke_parts.py` |
| assembly | `solidworks/tests/smoke/smoke_assembly.py` |
| clevis joint (constrained assembly) | `solidworks/tests/smoke/smoke_clevis.py` |
| drawing | `solidworks/tests/smoke/smoke_drawing.py` |
| sheet metal | `solidworks/tests/smoke/smoke_sheetmetal.py` |
| MCP end-to-end benchmark A-D | `mcp_server/tests/benchmark_v1.py` |
| usable drawings | `solidworks/tests/benchmark/benchmark_drawings.py` |

**Isolated mode** is the default ([`solidworks/tests/isolation.py`](../solidworks/tests/isolation.py)):

- every run writes to a NEW output directory (`--out`, or a temporary one), shared by
  all families of that run, so nothing from an earlier run is reused; a non-empty
  `--out` is refused, never cleaned;
- a test closes only documents it created or opened; documents that were open before
  stay open and untouched;
- a hung family is stopped and reported `HUNG`; SOLIDWORKS is never killed or relaunched
  unless you pass `--allow-restart`, which force-kills EVERY `SLDWORKS.exe` (all
  versions, unsaved work lost).

Results (defined in [`release_gate.py`](release_gate.py)):

| Result | Meaning | Counted as a pass | Family can still PASS |
|---|---|---|---|
| `PASS` | ran, and its check held | yes | yes |
| `FAIL` | ran, and a check did not hold | no | no |
| `ERROR` | crashed, nonzero exit, or no summary | no | no |
| `HUNG` | silent for 15 min, stopped | no | no |
| `NOT RUN` | should have run and did not | no | no |
| `[SKIP]` optional | a resource `release_gate.SKIP_OPTIONAL` declares optional is absent | no | yes, listed |
| `[SKIP]` required | any other skip: the family is `INCOMPLETE` | no | no |
| `[KNOWN ...]` accepted | a version limitation `release_gate.KNOWN_ACCEPTED` declares | no | yes, listed |
| `[KNOWN ...]` not accepted | any other: the family is `INCOMPLETE` | no | no |

"4 PASS, 1 SKIP" out of five required cases is therefore `INCOMPLETE`, never a PASS.
The drawing family needs the clevis built earlier in the same run: run it as
`run_regression.py clevis drawing`, or it is `INCOMPLETE`. Exit code 0 only when every
family is `PASS`.

Optional SOLIDWORKS-installed resources are used when present and never copied into
this repository: `sample.btl` and the bend/gauge tables under the installation's `lang`
folder (sheet metal). No test needs a model shipped with SOLIDWORKS. Templates, sheet formats and material databases must be the
standard ones. Environment overrides: `CADAPTER_TEST_OUT` (output root),
`CADAPTER_DRAWINGS_SKIP=<case>,...` (skip a drawing case the machine cannot carry).

## 4. Compatibility

The cross-version battery runs 93 scenarios (543 steps) and records a geometric
fingerprint after every step, so two SOLIDWORKS versions can be compared step by step.
See [its README](version/README.md).

```powershell
.venv\Scripts\python.exe -u tests\version\suite_version.py --out sw_report.json
.venv\Scripts\python.exe tests\version\compare_version.py <reference.json> sw_report.json
```

`suite_version.py` exits 1 when any step is `ERROR` (raised), `FAIL` (ran but did not do
what was asked) or `NOT RUN` (its scenario stopped earlier); a step is `KNOWN` only when
its scenario declares it in a `known` map with a reason. The same error on both versions
is still an `ERROR`. Exit 2 means it could not run.

To validate a change, `--scenario <name>` (repeatable) or `--domain` runs a subset through
the same code path and verdict. Such a run is labelled `FILTERED RUN`, its report is named
`partial_sw<rev>_<stamp>.json` and records `meta.filter`, and it refuses `--baseline`: a
subset is never a compatibility reference. `--expected-revision 25` (SW 2017) or `34`
(SW 2026) exits 2 before any scenario when another SOLIDWORKS answers over COM.

`tests/version/reports/BASELINE_*.json` are the committed reference reports (no host
name, no absolute paths). Evidence is only comparable between reports produced from the
SAME commit; the report records the commit, the Python version and the SOLIDWORKS
revision.

The whole acceptance workflow -- deterministic checks, the battery, the seven families
and the comparison -- is one command. It writes everything outside the repository and
exits nonzero when any phase fails:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_v1_battery.ps1 -Out D:\cadapter-v1 -ExpectedRevision 25.3.0
```

- `-Out` must be a new or empty directory (and `<Out>.zip` must not exist); anything else
  is refused before SOLIDWORKS is used, and nothing is deleted.
- `-ExpectedRevision` (optional, recommended where several versions are installed) stops
  with exit 2 unless the SOLIDWORKS that answers COM has that `RevisionNumber`; `25`
  accepts any SW 2017 (25.x.y), `25.3.0` only that one. It only verifies.
- The comparison against the other version's baseline is required: a missing report or
  baseline, or a comparator finding, fails the run. `-Skip compare` (or any other phase)
  is reported `SKIPPED BY REQUEST`, never PASS.
- The evidence `.zip` is part of acceptance: if it cannot be written, the run fails.

`-CloseAll` and `-AllowRestart` restore the destructive session-wide cleanup and
kill/relaunch recovery; both are off by default.
