# Local execution engine

`executor.py` runs bundles on one COM thread with an allowlist and path checks.
`verb_engine.py` compiles verbs through `verb_catalog/` and executes locally.
It requires Windows, `solidworks/`, `verb_catalog/` and `config_env.py`.
Install `engine/requirements.txt` for pywin32.

`engine/.env.example` documents optional local execution path restrictions.
Import `engine.verb_engine` to compile and run verbs, or `engine.executor` to
execute bundles. The executor validates every bundle before running it.
