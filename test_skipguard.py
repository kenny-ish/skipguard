import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import skipguard as sg


def go_events(*events):
    return "\n".join(json.dumps(e) for e in events)


GO_STREAM = go_events(
    {"Action": "start", "Package": "example.com/store"},
    {"Action": "run", "Package": "example.com/store", "Test": "TestOpen"},
    {"Action": "output", "Package": "example.com/store", "Test": "TestOpen", "Output": "=== RUN   TestOpen\n"},
    {"Action": "pass", "Package": "example.com/store", "Test": "TestOpen", "Elapsed": 0.01},
    {"Action": "run", "Package": "example.com/store", "Test": "TestMigrate"},
    {"Action": "output", "Package": "example.com/store", "Test": "TestMigrate", "Output": "=== RUN   TestMigrate\n"},
    {"Action": "output", "Package": "example.com/store", "Test": "TestMigrate",
     "Output": "    store_test.go:40: Test Dolt server not running, skipping test\n"},
    {"Action": "output", "Package": "example.com/store", "Test": "TestMigrate", "Output": "--- SKIP: TestMigrate (0.00s)\n"},
    {"Action": "skip", "Package": "example.com/store", "Test": "TestMigrate", "Elapsed": 0},
    {"Action": "pass", "Package": "example.com/store", "Elapsed": 0.02},
)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3" skipped="1" failures="0" errors="0">
<testcase classname="tests.test_render" name="test_publish" time="0.01"/>
<testcase classname="tests.test_render" name="test_gitignore" time="0.01">
  <skipped type="pytest.skip" message="git not installed">tests/test_render.py:12: git not installed</skipped>
</testcase>
<testcase classname="tests.test_render" name="test_halt" time="0.01"/>
</testsuite></testsuites>
"""


class SkipguardTests(unittest.TestCase):
    def run_main(self, text, *args):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "report"
            path.write_text(text, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = sg.main([str(path), *args])
        return code, out.getvalue()

    def test_parse_go_json(self):
        r = sg.parse(GO_STREAM)
        self.assertEqual(r.tests, {"example.com/store.TestOpen": "passed", "example.com/store.TestMigrate": "skipped"})
        self.assertEqual(r.reasons["example.com/store.TestMigrate"],
                         "store_test.go:40: Test Dolt server not running, skipping test")

    def test_parse_junit(self):
        r = sg.parse(JUNIT)
        self.assertEqual(r.names("passed"), ["tests.test_render.test_halt", "tests.test_render.test_publish"])
        self.assertEqual(r.names("skipped"), ["tests.test_render.test_gitignore"])
        self.assertEqual(r.reasons["tests.test_render.test_gitignore"], "git not installed")

    def test_junit_failure_and_error(self):
        r = sg.parse('<testsuite><testcase name="a"><failure message="x"/></testcase>'
                     '<testcase name="b"><error message="y"/></testcase></testsuite>')
        self.assertEqual(r.names("failed"), ["a", "b"])

    def test_required_test_that_was_skipped_fails(self):
        code, out = self.run_main(GO_STREAM, "--require", "TestMigrate")
        self.assertEqual(code, 1)
        self.assertIn("required test 'TestMigrate' did not pass (example.com/store.TestMigrate: skipped)", out)

    def test_required_test_that_never_ran_fails(self):
        code, out = self.run_main(GO_STREAM, "--require", "TestSchemaSkew")
        self.assertEqual(code, 1)
        self.assertIn("did not run at all", out)

    def test_required_test_that_passed_is_fine(self):
        code, out = self.run_main(GO_STREAM, "--require", "TestOpen")
        self.assertEqual(code, 0)
        self.assertIn("1 passed, 0 failed, 1 skipped", out)

    def test_no_skips_reports_reason_and_allow_list(self):
        code, out = self.run_main(JUNIT, "--no-skips")
        self.assertEqual(code, 1)
        self.assertIn("skipped: tests.test_render.test_gitignore (git not installed)", out)
        code, _ = self.run_main(JUNIT, "--no-skips", "--allow-skip", "test_git*")
        self.assertEqual(code, 0)

    def test_nothing_ran(self):
        # a -run filter that matched nothing: the package passes with no tests
        stream = go_events({"Action": "output", "Package": "p", "Output": "testing: warning: no tests to run\n"},
                           {"Action": "pass", "Package": "p", "Elapsed": 0})
        code, out = self.run_main(stream)
        self.assertEqual(code, 1)
        self.assertIn("only 0 test(s) passed", out)

    def test_subtests_match_by_last_component(self):
        stream = go_events({"Action": "pass", "Package": "github.com/a/b", "Test": "TestGate/verb_consent"})
        code, _ = self.run_main(stream, "--require", "TestGate/*")
        self.assertEqual(code, 0)

    def test_noise_lines_are_ignored(self):
        stream = "go: downloading example.com/x v1.0.0\n" + GO_STREAM + "\nFAIL\n"
        self.assertEqual(len(sg.parse(stream).tests), 2)

    def test_bad_xml(self):
        code, out = self.run_main("<testsuite><testcase></testsuite>")
        self.assertEqual(code, 2)
        self.assertIn("cannot parse", out)


if __name__ == "__main__":
    unittest.main()
