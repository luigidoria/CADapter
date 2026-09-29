# Testing

Run from the repository root with the environment containing the selected modules.
Windows import checks may require pywin32 even when they do not open SOLIDWORKS.

```powershell
$env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python.exe -u solidworks/tests/unit/check_catalog.py
.venv\Scripts\python.exe -u solidworks/tests/unit/check_component_refs.py
.venv\Scripts\python.exe -u tests/version/check_scenarios.py
```

For live CAD tests, save unrelated work and stop other clients using SOLIDWORKS.
Run `tests/run_regression.py` with one family at a time: `part`, `assembly`, `clevis`,
`drawing`, `sheetmetal`, `benchmark`, or `drawings`. Inspect the PASS/FAIL table and
generated summary. The public runner uses isolated output and closes only documents it
owns; it does not restart SOLIDWORKS unless explicitly requested. Prefer the commands
and safety details in [`tests/README.md`](../tests/README.md).

## Frozen V1 validation

Validated code commit: `2490209328ffc48212dc7900d65d3ead6a173085`.

| Evidence | SOLIDWORKS 2017 | SOLIDWORKS 2026 |
|---|---:|---:|
| Revision | 25.3.0 | 34.3.2 |
| Required compatibility steps | 543 PASS | 543 PASS |
| Compatibility FAIL / ERROR / SKIP / KNOWN / NOT RUN | 0 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 0 |
| Assembly | 25/25 PASS | 25/25 PASS |
| Benchmark A-D | 4/4 cases, 23 checks PASS | 4/4 cases, 23 checks PASS |
| Clevis | 14/14 PASS | 14/14 PASS |
| Drawing | 72/72 PASS | 72/72 PASS |
| Usable drawings | 17/17 PASS | 17/17 PASS |
| Part + Sketch + Inspection | 64/64 PASS | 64/64 PASS |
| Sheet Metal | 234/234 PASS | 231/231 PASS |
| Additional outcomes | 2 accepted KNOWN | 2 optional SKIP |

The two SW2017 KNOWN outcomes concern installed bend-table behavior; they are accepted
known results, not passes. The two SW2026 optional skips occurred because installed
bend-table resources were unavailable; they are not passes. Later documentation or
release-evidence commits are not themselves claims of another SOLIDWORKS run.

### Same-commit cross-version comparison

The reports from the validated commit produced `ASSEMBLY=7`, `RESULT=29` and `FILE=3`.
These are investigated representation or solver differences, not compatibility-suite
failures: each version completed all 543 required steps.

- The seven assembly findings come from two underconstrained face-mate scenarios. A
  coincident top/bottom face mate leaves translation in X/Z and rotation about Y free.
  Both versions report zero mate errors and zero interference, but choose different
  free Z positions. The requested mate succeeds; final assembly coordinates are not
  identical across versions.
- The 29 result findings include `Front Plane` versus `Front`, filename-extension
  casing, equivalent list ordering, tiny floating-point differences,
  SOLIDWORKS-specific metadata/property names, one additional automatic section-view
  annotation in SW2026, and cut-list representation differences. None represents
  different requested geometry.
- Three generated files differ by more than 25% in size. No evidence indicates that
  they are invalid; SOLIDWORKS serialization and PDF generation vary by version.

On the tested SW2017 installation, cut-list `Mass` can be localized text such as
`"22,97"`; SW2026 can return the numeric value `22.97`. A targeted SW2017 A/B test
produced the localized text both when the sheet-metal scenario ran alone and when a
drawing scenario ran first. It was therefore not caused by CADapter's drawing
workflow. Treat this as a SOLIDWORKS version, environment or locale difference.
Cut-list property names can also vary (`Material` versus `MATERIAL`), and a property
may exist in only one version.

## The cross-version battery

Run identical source on SW2017 and SW2026 with
`tests/version/suite_version.py`. Compare reports with
`tests/version/compare_version.py <reference.json> <new.json>`. Keep version, source
revision and selected scenarios with each result. A meaningful compatibility claim
requires both reports to come from the same commit. Do not overwrite frozen baselines.
