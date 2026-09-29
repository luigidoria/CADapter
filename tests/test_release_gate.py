"""The release gate's verdicts, without SOLIDWORKS: fake reports, fake family output,
fake exit codes. Each case here is a way the V1 battery once reported, or could report,
a PASS it had not earned.

    python -m unittest tests.test_release_gate

The suite_version cases import that module (it imports pywin32 without connecting), so
they run on Windows only; everything else is plain Python.
"""
from pathlib import Path
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import release_gate as G  # noqa: E402

BATTERY = (ROOT / "tests" / "run_v1_battery.ps1").read_text(encoding="utf-8")


def _quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()) as buf:
        return fn(*a, **kw), buf.getvalue()


# ── compatibility suite ───────────────────────────────────────────────────────
def _step(i, verb, **kw):
    return dict({"i": i, "verb": verb, "ok": True}, **kw)


class SuiteVerdictTests(unittest.TestCase):
    def verdict(self, steps, n_steps=None, known=None, stopped=None):
        report = {"scenarios": [{"name": "s", "steps": steps, "parou_em": stopped}]}
        return G.suite_verdict(report, {"s": (n_steps or len(steps), known or {})})

    def test_all_steps_pass_is_exit_0(self):
        v = self.verdict([_step(0, "part.new_part"), _step(1, "part.block")])
        self.assertEqual(v["exit_code"], 0)
        self.assertEqual(v["counts"][G.PASS], 2)

    def test_step_error_is_nonzero(self):
        v = self.verdict([_step(0, "part.new_part"), _step(1, "part.fillet", ok=False,
                                                           error="refused")])
        self.assertEqual(v["exit_code"], 1)
        self.assertEqual(v["counts"][G.ERROR], 1)
        self.assertEqual(v["counts"][G.PASS], 1)

    def test_failed_expectation_is_nonzero(self):
        v = self.verdict([_step(0, "part.pattern_linear", expect_failed=["1 hole, not 3"])])
        self.assertEqual(v["exit_code"], 1)
        self.assertEqual(v["counts"][G.FAIL], 1)

    def test_steps_after_a_stop_are_not_run(self):
        v = self.verdict([_step(0, "part.new_part"),
                          _step(1, "part.hole", ok=False, error="wrong side")],
                         n_steps=3, stopped=1)
        self.assertEqual(v["counts"][G.NOT_RUN], 1)
        self.assertEqual(v["exit_code"], 1)

    def test_declared_known_is_known_not_pass(self):
        v = self.verdict([_step(0, "part.new_part"),
                          _step(1, "part.fillet", ok=False, error="refused")],
                         known={1: "radius exceeds the wall"})
        self.assertEqual(v["counts"][G.KNOWN], 1)
        self.assertEqual(v["counts"][G.PASS], 1)
        self.assertEqual(v["exit_code"], 0)

    def test_known_declared_elsewhere_does_not_excuse_this_step(self):
        v = self.verdict([_step(0, "part.new_part", ok=False, error="x")], known={3: "other"})
        self.assertEqual(v["exit_code"], 1)

    def test_same_error_on_both_versions_is_still_an_error(self):
        # nothing in the verdict reads another report: agreement is not acceptance
        step = _step(0, "part.fillet", ok=False, error="identical on SW17 and SW26")
        self.assertEqual(self.verdict([step])["exit_code"], 1)


@unittest.skipUnless(sys.platform == "win32", "suite_version imports pywin32")
class SuiteVersionExitTests(unittest.TestCase):
    """suite_version.main end to end, with the COM parts replaced."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["CADAPTER_VERSION_WORK"] = os.path.join(cls.tmp.name, "work")
        sys.path.insert(0, str(ROOT / "tests" / "version"))
        import suite_version
        cls.S = suite_version

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("CADAPTER_VERSION_WORK", None)
        cls.tmp.cleanup()

    def run_main(self, steps, stopped=None, hygiene=lambda needs: "25.3.0"):
        S = self.S
        fake = lambda cen, v: {"name": cen["name"], "domain": cen["domain"],  # noqa: E731
                               "steps": steps, "parou_em": stopped, "arquivos": {}}
        out = os.path.join(self.tmp.name, "report.json")
        with unittest.mock.patch.object(S, "_hygiene", hygiene), \
                unittest.mock.patch.object(S, "run_scenario", fake), \
                unittest.mock.patch.object(S.surface, "open_", lambda name: object()):
            code, text = _quiet(S.main, ["--scenario", "bloco_simples", "--out", out])
        return code, text

    def test_all_pass_exits_0(self):
        code, text = self.run_main([_step(0, "part.new_part"), _step(1, "part.block")])
        self.assertEqual(code, 0, text)
        self.assertIn("VERDICT: PASS", text)

    def test_unexpected_step_error_exits_1(self):
        code, text = self.run_main([_step(0, "part.new_part"),
                                    _step(1, "part.block", ok=False, error="boom")], stopped=1)
        self.assertEqual(code, 1, text)
        self.assertIn("VERDICT: FAIL", text)

    def test_failed_expectation_exits_1(self):
        code, text = self.run_main([_step(0, "part.new_part"),
                                    _step(1, "part.block", expect_failed=["volume 0"])])
        self.assertEqual(code, 1, text)

    def test_early_stop_leaves_declared_steps_not_run(self):
        code, text = self.run_main([_step(0, "part.new_part")], stopped=0)
        self.assertEqual(code, 1, text)
        self.assertIn("1 NOT RUN", text)

    def test_solidworks_unavailable_exits_2(self):
        def dead(needs):
            raise OSError("no COM server")
        code, text = self.run_main([], hygiene=dead)
        self.assertEqual(code, 2, text)

    def run_args(self, args, hygiene=lambda needs: "34.3.2"):
        S = self.S
        fake = lambda cen, v: {"name": cen["name"], "domain": cen["domain"],  # noqa: E731
                               "steps": [_step(i, verb) for i, (verb, _) in
                                         enumerate(cen["steps"])],
                               "parou_em": None, "arquivos": {}}
        with unittest.mock.patch.object(S, "_hygiene", hygiene), \
                unittest.mock.patch.object(S, "run_scenario", fake), \
                unittest.mock.patch.object(S.surface, "open_", lambda name: object()):
            return _quiet(S.main, args)

    def test_partial_run_refuses_baseline_before_touching_solidworks(self):
        def untouchable(needs):
            raise AssertionError("SOLIDWORKS must not be touched")
        code, text = self.run_args(["--scenario", "bloco_simples", "--baseline"],
                                   hygiene=untouchable)
        self.assertEqual(code, 2, text)
        self.assertIn("never be written as a baseline", text)

    def test_unknown_scenario_is_refused_not_dropped(self):
        code, text = self.run_args(["--scenario", "bloco_simples",
                                    "--scenario", "no_such_scenario"])
        self.assertEqual(code, 2, text)
        self.assertIn("no_such_scenario", text)

    def test_filtered_run_is_labelled_partial(self):
        out = os.path.join(self.tmp.name, "partial.json")
        code, text = self.run_args(["--scenario", "bloco_simples", "--out", out])
        self.assertEqual(code, 0, text)
        self.assertIn("FILTERED RUN: 1 of", text)
        meta = json.loads(Path(out).read_text(encoding="utf-8"))["meta"]
        self.assertEqual(meta["filter"]["scenario"], ["bloco_simples"])
        self.assertEqual(meta["filter"]["scenarios_run"], 1)

    def test_expected_revision_mismatch_exits_2(self):
        code, text = self.run_args(["--scenario", "bloco_simples",
                                    "--expected-revision", "25"])
        self.assertEqual(code, 2, text)
        self.assertIn("wrong SolidWorks version", text)

    def test_expected_revision_match_runs(self):
        out = os.path.join(self.tmp.name, "rev.json")
        code, text = self.run_args(["--scenario", "bloco_simples", "--out", out,
                                    "--expected-revision", "34"])
        self.assertEqual(code, 0, text)


# ── regression families ───────────────────────────────────────────────────────
class FamilyResultTests(unittest.TestCase):
    def test_all_required_checks_pass(self):
        r = G.family_result("part", 0, ["  [PASS] a", "  [PASS] b", "SUMMARY: 2/2 PASS"])
        self.assertEqual(r["result"], G.PASS)
        self.assertEqual(r["passed"], 2)

    def test_required_skip_is_not_a_complete_pass(self):
        # the drawing family run without the clevis: its summary counts only what ran
        r = G.family_result("drawing", 0, [
            "  [PASS] a", "  [SKIP] assembly BOM -- no clevis.sldasm in X", "1/1 PASS"])
        self.assertEqual(r["result"], "INCOMPLETE")
        self.assertEqual(len(r["skip_required"]), 1)

    def test_skipped_case_hidden_by_the_denominator_is_incomplete(self):
        # the audited false PASS: "4/4 PASS (1 SKIP)" from a benchmark that ran 4 of 5
        r = G.family_result("benchmark", 0, [
            "  [PASS] x", "  [SKIP] B -- not installed", "4/4 PASS  (1 SKIP)"])
        self.assertNotEqual(r["result"], G.PASS)

    def test_optional_skip_is_allowed_visible_and_not_counted(self):
        lines = ["  [PASS] a",
                 "  [SKIP] gauge table -- none installed with this SOLIDWORKS (lang/...)",
                 "SUMMARY: 1/1 PASS"]
        r = G.family_result("sheetmetal", 0, lines)
        self.assertEqual(r["result"], G.PASS)
        self.assertEqual(r["passed"], 1)
        self.assertEqual(len(r["skip_optional"]), 1)
        self.assertIn("1 SKIP optional", G.family_notes(r))

    def test_the_same_skip_is_required_in_a_family_that_does_not_declare_it(self):
        r = G.family_result("part", 0, [
            "  [PASS] a", "  [SKIP] gauge table -- none installed", "1/1 PASS"])
        self.assertEqual(r["result"], "INCOMPLETE")

    def test_accepted_known_stays_known(self):
        lines = ["  [KNOWN SW2017] sample.btl refused (intermittent in SW 2017)",
                 "  [PASS] SW2017: the refused sample.btl left the DEFAULT allowance",
                 "SUMMARY: 1/1 PASS"]
        r = G.family_result("sheetmetal", 0, lines)
        self.assertEqual(r["result"], G.PASS)
        self.assertEqual(r["passed"], 1)          # the check, not the KNOWN line
        self.assertEqual(len(r["known_accepted"]), 1)
        self.assertIn("1 KNOWN accepted", G.family_notes(r))

    def test_undeclared_known_is_not_accepted(self):
        r = G.family_result("part", 0, ["  [KNOWN SW2017] something new", "  [PASS] a",
                                        "SUMMARY: 1/1 PASS"])
        self.assertEqual(r["result"], "INCOMPLETE")
        self.assertEqual(len(r["known_unaccepted"]), 1)

    def test_failed_check(self):
        r = G.family_result("part", 0, ["  [PASS] a", "  [FAIL ] b", "SUMMARY: 1/2 PASS"])
        self.assertEqual(r["result"], G.FAIL)

    def test_nonzero_exit_or_missing_summary_is_error(self):
        self.assertEqual(G.family_result("part", 1, ["  [PASS] a", "1/1 PASS"])["result"],
                         G.ERROR)
        self.assertEqual(G.family_result("part", 0, ["  [PASS] a", "Traceback"])["result"],
                         G.ERROR)
        self.assertEqual(G.family_result("part", 0, ["0/0 PASS"])["result"], G.ERROR)

    def test_hung(self):
        self.assertEqual(G.family_result("part", 1, ["  [PASS] a"], hung=True)["result"],
                         "HUNG")

    def test_not_run_and_tally(self):
        table = {"part": G.family_result("part", 0, ["  [PASS] a", "1/1 PASS"]),
                 "drawing": G.family_not_run("SOLIDWORKS did not answer")}
        self.assertEqual(table["drawing"]["result"], G.NOT_RUN)
        tally = G.regression_tally(table)
        self.assertIn("1 NOT RUN", tally)
        self.assertIn("1 PASS", tally)

    def test_runner_refuses_a_non_empty_out_before_touching_solidworks(self):
        import tests.run_regression as R
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "stale.sldprt").write_text("x")
            with unittest.mock.patch.object(R, "ensure_sw",
                                            side_effect=AssertionError("attached")):
                code, text = _quiet(R.main, ["part", "--out", tmp])
        self.assertEqual(code, 2)
        self.assertIn("not empty", text)


# ── comparison phase ──────────────────────────────────────────────────────────
class ComparePhaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.report = self.dir / "suite_version_sw34_3_2.json"
        self.report.write_text("{}")
        self.reports = self.dir / "reports"
        self.reports.mkdir()
        (self.reports / "BASELINE_sw34_3_2.json").write_text("{}")   # this version's own

    def tearDown(self):
        self.tmp.cleanup()

    def phase(self, report=None, rev="34.3.2", run=lambda argv: 0):
        return G.compare_phase(str(report or self.report), rev, str(self.dir),
                               str(self.reports), run=run)

    def test_missing_baseline_is_nonzero(self):
        code, msgs = self.phase()
        self.assertEqual(code, 1, msgs)

    def test_missing_report_is_nonzero(self):
        (self.reports / "BASELINE_sw25_3_0.json").write_text("{}")
        code, msgs = self.phase(report=self.dir / "absent.json")
        self.assertEqual(code, 1, msgs)

    def test_clean_comparison_passes_and_keeps_the_ref_new_order(self):
        (self.reports / "BASELINE_sw25_3_0.json").write_text("{}")
        seen = []
        code, _ = self.phase(run=lambda argv: seen.append(argv) or 0)
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)              # never against its own baseline
        a, b = seen[0][3], seen[0][4]
        self.assertTrue(a.endswith("BASELINE_sw25_3_0.json"))   # REF = SW 2017
        self.assertEqual(b, str(self.report))

    def test_incompatibility_is_nonzero(self):
        (self.reports / "BASELINE_sw25_3_0.json").write_text("{}")
        self.assertEqual(self.phase(run=lambda argv: 1)[0], 1)

    def test_comparator_crash_is_nonzero(self):
        # the real comparator on a baseline that is not a report
        (self.reports / "BASELINE_sw25_3_0.json").write_text("not json")
        real = lambda argv: subprocess.run(argv, cwd=ROOT, capture_output=True).returncode  # noqa: E731
        code, msgs = self.phase(run=real)
        self.assertNotEqual(code, 0, msgs)

    def test_explicit_skip_is_reported_as_skipped_by_request(self):
        self.assertIn("compare: SKIPPED BY REQUEST", BATTERY)
        self.assertIn('"PHASE ${phase}: SKIPPED BY REQUEST"', BATTERY)
        self.assertIn('Run-Py "compare" @("tests\\release_gate.py", "compare"', BATTERY)
        self.assertNotIn("nothing to compare", BATTERY)


# ── output freshness ──────────────────────────────────────────────────────────
class FreshOutputTests(unittest.TestCase):
    def test_new_and_empty_roots_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(G.fresh_output_problem(os.path.join(tmp, "new")))
            self.assertIsNone(G.fresh_output_problem(tmp))

    def test_non_empty_root_is_refused_and_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "work").mkdir()
            Path(tmp, "work", "clevis.sldasm").write_text("stale")
            msg = G.fresh_output_problem(tmp)
            self.assertIn("Output directory is not empty", msg)
            self.assertIn("Use a new directory for release validation", msg)
            self.assertTrue(Path(tmp, "work", "clevis.sldasm").exists())
            self.assertEqual(_quiet(G.main, ["fresh-out", tmp])[0], 1)

    def test_a_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "f").write_text("x")
            self.assertIsNotNone(G.fresh_output_problem(os.path.join(tmp, "f")))

    def test_battery_checks_before_it_creates_anything(self):
        check = BATTERY.index("release_gate.py fresh-out $Out")
        self.assertLess(check, BATTERY.index("New-Item -ItemType Directory -Force $Out"))
        self.assertIn('Test-Path "$Out.zip"', BATTERY)
        self.assertNotIn("Remove-Item $zip -ErrorAction SilentlyContinue\n# the evidence",
                         BATTERY)


# ── expected revision ─────────────────────────────────────────────────────────
class RevisionTests(unittest.TestCase):
    def test_matching(self):
        self.assertTrue(G.revision_matches("25.3.0", "25.3.0"))
        self.assertTrue(G.revision_matches("25", "25.3.0"))
        self.assertTrue(G.revision_matches(" 34.3 ", "34.3.2\n"))

    def test_mismatch(self):
        self.assertFalse(G.revision_matches("25.3.0", "34.3.2"))
        self.assertFalse(G.revision_matches("25.3", "25.30.0"))
        self.assertFalse(G.revision_matches("", "25.3.0"))

    def test_cli_reports_both_and_fails(self):
        code, text = _quiet(G.main, ["revision", "25.3.0", "34.3.2"])
        self.assertEqual(code, 1)
        self.assertIn("Expected SolidWorks revision: 25.3.0", text)
        self.assertIn("Observed SolidWorks revision: 34.3.2", text)
        self.assertIn("ERROR: wrong SolidWorks version is active.", text)
        self.assertEqual(_quiet(G.main, ["revision", "34", "34.3.2"])[0], 0)

    def test_battery_guard_runs_before_the_output_is_created(self):
        guard = BATTERY.index("release_gate.py revision $ExpectedRevision $rev")
        self.assertLess(guard, BATTERY.index("release_gate.py fresh-out $Out"))
        self.assertIn("[string]$ExpectedRevision", BATTERY)


# ── evidence bundle ───────────────────────────────────────────────────────────
class EvidenceZipTests(unittest.TestCase):
    def test_zip_failure_fails_the_run(self):
        block = BATTERY[BATTERY.index("try {\n    Compress-Archive"):]
        self.assertIn("-ErrorAction Stop", block.splitlines()[1])
        catch = block[block.index("} catch {"):block.index("Get-Content \"$Out\\SUMMARY.txt\"")]
        self.assertIn("[void]$failed.Add(\"evidence zip", catch)
        self.assertIn("Write-Summary", catch)
        # the verdict/exit come after the zip, so the zip can still turn them to FAIL
        self.assertLess(BATTERY.index("Compress-Archive"), BATTERY.rindex("if ($failed.Count) { exit 1 }"))
        self.assertIn('-Exclude "work"', block)


# ── the removed vendor sample part is gone from the public ladder ─────────────
class NoVendorPartTests(unittest.TestCase):
    PUBLIC = ["mcp_server/tests/benchmark_v1.py", "mcp_server/tests/README.md",
              "solidworks/tests/benchmark/benchmark_drawings.py",
              "solidworks/tests/README.md", "tests/README.md", "tests/run_regression.py",
              "tests/run_v1_battery.ps1", "docs/TESTING.md"]

    def test_no_public_test_names_it(self):
        for rel in self.PUBLIC:
            text = (ROOT / rel).read_text(encoding="utf-8").lower()
            # spelled in pieces, so a grep of the public tree does not find this guard
            for needle in ("punch" + "_holder", "punch" + " holder"):
                self.assertNotIn(needle, text, rel)


if __name__ == "__main__":
    unittest.main()
