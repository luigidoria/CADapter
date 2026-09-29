"""Module-boundary checks. No COM, services, credentials or optional dependencies.

Cross-platform: SOLIDWORKS modules are either read as source or imported with stand-ins
for pywin32, so nothing here can reach a COM server.

    python -m unittest tests.test_modularity
"""
from pathlib import Path
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# the live tests the public ladder ships (tiers 2-4)
LIVE_TESTS = [
    "tests/smoke_installation.py", "tests/run_regression.py",
    "tests/version/suite_version.py",
    "solidworks/tests/smoke/smoke_parts.py", "solidworks/tests/smoke/smoke_assembly.py",
    "solidworks/tests/smoke/smoke_clevis.py", "solidworks/tests/smoke/smoke_drawing.py",
    "solidworks/tests/smoke/smoke_sheetmetal.py",
    "solidworks/tests/benchmark/benchmark_drawings.py", "mcp_server/tests/benchmark_v1.py",
]


class ConfigurationTests(unittest.TestCase):
    def run_loader(self, code, *, legacy="", module="", extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy2(ROOT / "config_env.py", root / "config_env.py")
            (root / "agent").mkdir()
            (root / ".env").write_text(legacy)
            (root / "agent" / ".env").write_text(module)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("SOLIDMCP_", "CADAPTER_", "TEST_"))}
            env.update(extra or {})
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=root,
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_requested_module_defaults_are_loaded(self):
        result = self.run_loader(
            "import config_env, os, json; config_env.load_module('agent'); "
            "print(json.dumps(os.environ.get('TEST_VALUE')))", module="TEST_VALUE=module\n")
        self.assertEqual(result, "module")

    def test_process_then_legacy_then_module_precedence(self):
        code = ("import config_env, os, json; config_env.load_module('agent'); "
                "print(json.dumps([os.environ.get(k) for k in ('TEST_A','TEST_B','TEST_C')]))")
        # the root .env is the "legacy" location; its values just say "root"
        result = self.run_loader(code, legacy="TEST_A=root\nTEST_B=root\n",
                                 module="TEST_A=module\nTEST_B=module\nTEST_C=module\n",
                                 extra={"TEST_A": "process"})
        self.assertEqual(result, ["process", "root", "module"])

    def test_base_does_not_load_optional_configuration(self):
        result = self.run_loader("import config_env,os,json; "
                                 "print(json.dumps(os.environ.get('TEST_VALUE')))",
                                 module="TEST_VALUE=private\n")
        self.assertIsNone(result)

    def test_missing_module_is_optional(self):
        result = self.run_loader("import config_env,json; "
                                 "print(json.dumps(config_env.load_module('absent')))")
        self.assertEqual(result, {})


class BoundaryTests(unittest.TestCase):

    def test_live_families_take_their_output_root_from_isolation(self):
        # no family may compute tests/validation_assembly/ on its own again: isolated
        # mode (the public default) depends on every OUT coming from output_root()
        for source in LIVE_TESTS:
            if source.startswith("tests/"):       # runners and the smoke write no CAD here
                continue
            text = (ROOT / source).read_text(encoding="utf-8")
            outs = [ast.unparse(n.value) for n in ast.parse(text).body
                    if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id in ("OUT", "VA") for t in n.targets)]
            self.assertTrue(any("output_root()" in o for o in outs), f"{source}: {outs}")
            self.assertNotIn('"validation_assembly"', text, source)

    def test_isolated_output_is_new_and_shared_by_subprocesses(self):
        from solidworks.tests import isolation
        env = {k: v for k, v in os.environ.items() if k != isolation.ENV_OUT}
        env[isolation.ENV_ISOLATED] = "1"
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            first = isolation.output_root()
            try:
                self.assertTrue(Path(first).is_dir())
                self.assertNotIn(ROOT, Path(first).resolve().parents)
                self.assertEqual(os.environ[isolation.ENV_OUT], first)   # children inherit it
                self.assertEqual(isolation.output_root(), first)
            finally:
                shutil.rmtree(first, ignore_errors=True)

    def test_live_tests_never_close_or_kill_the_whole_session(self):
        # Public live tests close only their own documents. The session-wide calls live in
        # isolation.py (shared mode only) and run_regression.py (behind --allow-restart).
        forbidden = {"CloseAllDocuments", "close_untitled", "restart", "ExitApp"}
        for source in LIVE_TESTS:
            if source.endswith("run_regression.py"):
                continue
            text = (ROOT / source).read_text(encoding="utf-8")
            self.assertNotIn("taskkill", text, source)
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, forbidden, f"{source}:{node.lineno}")

    def test_regression_runner_kills_nothing_without_the_flag(self):
        text = (ROOT / "tests/run_regression.py").read_text(encoding="utf-8")
        self.assertIn("destructive = a.allow_restart or not T.isolated()", text)
        tree = ast.parse(text)
        # every kill sits inside an `if destructive`, and every function that can kill or
        # relaunch is told whether it may
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and ast.unparse(node.test) == "destructive":
                for call in ast.walk(node):
                    call.guarded = True
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "_kill_sw":
                self.assertTrue(getattr(node, "guarded", False), f"line {node.lineno}")
        for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
            body = ast.unparse(fn)
            if fn.name != "_kill_sw" and ("_kill_sw()" in body or "_RESTART" in body):
                self.assertIn("destructive", [a.arg for a in fn.args.args], fn.name)

    def test_base_imports_with_optional_directories_removed(self):
        # Import-boundary test only: these stand-ins cannot execute COM.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for module in ("mcp_server", "solidworks"):
                (root / module).mkdir()
                for source in (ROOT / module).glob("*.py"):
                    shutil.copy2(source, root / module / source.name)
            shutil.copy2(ROOT / "config_env.py", root / "config_env.py")
            code = '''
import ast, pathlib, sys, types
for name in ("pythoncom", "pywintypes", "win32com", "win32com.client",
             "win32com.client.dynamic", "mcp", "mcp.server", "mcp.server.fastmcp"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["pythoncom"].com_error = type("ComError", (Exception,), {})
tree = ast.parse(pathlib.Path("mcp_server/mcp_solidworks.py").read_text(encoding="utf-8"))
for node in ast.walk(tree):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id == "pythoncom" and node.attr.startswith("VT_"):
            setattr(sys.modules["pythoncom"], node.attr, 0)
class FastMCP:
    def __init__(self, name): self.tools = {}
    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register
sys.modules["mcp.server.fastmcp"].FastMCP = FastMCP
from mcp_server import mcp_solidworks as server
assert "sw_verb" in server.mcp.tools
assert "sw_api_signature" in server.mcp.tools
try:
    server.sw_search_api("mate")
except RuntimeError as exc:
    assert "optional rag/" in str(exc)
else:
    raise AssertionError("missing RAG must report an explicit error")
assert not any(n.split(".")[0] in {"agent", "cloud", "bridge", "engine", "rag"}
               for n in sys.modules)
server._com_pool.shutdown()
'''
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("SOLIDMCP_", "CADAPTER_", "PYTHONPATH"))}
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=root,
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


    def test_base_requirements_exclude_optional_libraries(self):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for package in ("chromadb", "langgraph", "httpx", "cryptography", "beautifulsoup4"):
            self.assertNotIn(package, text)

    def test_base_registration_does_not_require_cloud(self):
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config["mcpServers"]), {"CADapter"})

    def test_signature_builder_is_available_without_rag(self):
        self.assertTrue((ROOT / "mcp_server" / "build_api_signatures_typelib.py").is_file())


if __name__ == "__main__":
    unittest.main()
