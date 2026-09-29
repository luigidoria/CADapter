# solidworks tests

This directory owns tests for `solidworks/`: the no-CAD contract checks in `unit/`, the
live domain families in `smoke/` and the drawing benchmark in `benchmark/`. Which to run,
in what order and with which safety defaults is in [the test levels](../../tests/README.md);
prerequisites are in [the testing guide](../../docs/TESTING.md). Live families share
[`isolation.py`](isolation.py): by default each run writes to its own output directory
and closes only the documents it opened.
