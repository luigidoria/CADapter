# Compiled verb catalog

This module owns schemas and compilation into operation bundles. It does not connect
to SOLIDWORKS. The local engine executes the compiled bundles.
Keep verb names, parameter types/defaults and bundle semantics stable during cleanup.

`schemas.py` derives parameter schemas by inspecting source. Verb docstrings supply
catalog descriptions; keep their CAD meaning and parameter guidance intact.
