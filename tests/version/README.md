# Cross-version battery

Runs the same scenarios on two SOLIDWORKS versions and records a geometric fingerprint
per step, so a difference can be traced to the version, to an old bug, or to the test
itself.

For frozen V1, SW2017 (25.3.0) and SW2026 (34.3.2) each completed all 543 required
steps on code commit `db6804ae183673d99440164bccdc30c3d10ede80`. The same-commit
comparison and its investigated `ASSEMBLY=7`, `RESULT=28`, `FILE=2` findings are in the
[validation record](../../docs/TESTING.md#frozen-v1-validation).

How it works, how to run and compare, how to add a scenario, and the results so far:
**[docs/TESTING.md § The cross-version battery](../../docs/TESTING.md#the-cross-version-battery)**.

```powershell
$env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python.exe -u tests\version\suite_version.py --local
.venv\Scripts\python.exe tests\version\compare_version.py <reference.json> <new.json>
```
