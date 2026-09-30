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

Validated code commit: `db6804ae183673d99440164bccdc30c3d10ede80`.

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
| Additional outcomes | 3 accepted KNOWN | 2 optional SKIP |

The three SW2017 KNOWN outcomes are bend tables that SOLIDWORKS 2017 intermittently
refuses (on a 3.0 mm sheet, with `sample.btl`, and on a miter flange); the verb reports
the refusal and restores the default allowance. They are accepted known results, not
passes, and their number can vary between runs. The two SW2026 optional skips occurred
because installed bend-table resources were unavailable; they are not passes. Later
documentation or release-evidence commits are not themselves claims of another
SOLIDWORKS run.

Test environment: both versions ran on the same Windows machine, where SOLIDWORKS 2017
and 2026 are installed side by side, each in a freshly started session with no other
CAD client attached. CADapter attaches to `SldWorks.Application` and binds to the
highest registered SOLIDWORKS type library; on this machine both resolve to 2026. For
the SW2017 run, per-user (HKCU) registry overrides pointed both at the 2017
installation and were removed afterwards. Without them, a SW2017 session driven through
the 2026 type library fails on `IComponent2.GetSuppression2`. The factory default part
and assembly templates were missing from that SW2017 installation, so it used the
installed English tutorial templates with the reference planes renamed to
`Front Plane`, `Top Plane` and `Right Plane`.

### Same-commit cross-version comparison

The reports from the validated commit produced `ASSEMBLY=7`, `RESULT=28` and `FILE=2`.
These are investigated representation or solver differences, not compatibility-suite
failures: each version completed all 543 required steps.

- The seven assembly findings come from two underconstrained face-mate scenarios. A
  coincident top/bottom face mate leaves translation in X/Z and rotation about Y free.
  Both versions report zero mate errors and zero interference, but choose different
  free Z positions. The requested mate succeeds; final assembly coordinates are not
  identical across versions.
- The 28 result findings are `Front Plane` (SW2017) versus `Front` (SW2026) as the
  sketch plane name, filename-extension casing (`.sldprt` versus `.SLDPRT`), equivalent
  list ordering (sheet bodies, explode steps), SOLIDWORKS-specific metadata and
  property names (`ExplView1` versus `Exploded View1`, `Material` versus `MATERIAL`,
  `Sheet Metal Gauge` present only in SW2026), and one additional automatic
  section-view annotation in SW2026. None represents different requested geometry.
- Two generated PDF files differ by more than 25% in size. No evidence indicates that
  they are invalid; SOLIDWORKS serialization and PDF generation vary by version.

Cut-list `Mass` can come back as localized text such as `"22,97"` from some SW2017
installations (observed on an earlier validation machine, independent of CADapter's
drawing workflow) and as the numeric value `22.97` from others; in this run both
versions returned numbers. Treat it as a SOLIDWORKS version, environment or locale
difference. Cut-list property names can also vary (`Material` versus `MATERIAL`), and a
property may exist in only one version.

## The cross-version battery

Run identical source on SW2017 and SW2026 with
`tests/version/suite_version.py`. Compare reports with
`tests/version/compare_version.py <reference.json> <new.json>`. Keep version, source
revision and selected scenarios with each result. A meaningful compatibility claim
requires both reports to come from the same commit. Do not overwrite frozen baselines.
