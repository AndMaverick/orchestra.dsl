import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from orchestra_dsl.cli import main

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V1 = os.path.join(HERE, "examples", "dispute_desk.odl")
V2 = os.path.join(HERE, "examples", "dispute_desk_v2.odl")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_check_passes_with_a_warning_and_fails_in_strict_mode(self):
        code, out, _ = run("check", V1)
        self.assertEqual(code, 0)
        self.assertIn("OD202", out)
        self.assertEqual(run("check", V1, "--strict")[0], 1)

    def test_check_fails_when_the_approval_is_gone(self):
        code, out, _ = run("check", V2)
        self.assertEqual(code, 1)
        self.assertIn("OD201", out)

    def test_check_budgets(self):
        self.assertEqual(run("check", V1, "--max-cost", "0.05")[0], 1)
        self.assertEqual(run("check", V1, "--max-cost", "1")[0], 0)
        self.assertEqual(run("check", V1, "--models", "small")[0], 0)  # a model outside the list is a warning, not an error

    def test_analyze_text_and_json(self):
        code, out, _ = run("analyze", V1, "--mode", "expected")
        self.assertEqual(code, 0)
        self.assertIn("critical: ingest -> evidence -> decide -> refund", out)
        data = json.loads(run("analyze", V1, "--json")[1])
        self.assertEqual(data["approvals"], ["refund"])
        self.assertEqual(data["critical_steps"], ["ingest", "evidence", "decide", "refund"])

    def test_fmt_is_idempotent_and_digest_is_stable(self):
        formatted = run("fmt", V1)[1]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.odl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(formatted)
            self.assertEqual(run("fmt", path)[1], formatted)
            self.assertEqual(run("digest", path)[1], run("digest", V1)[1])

    def test_graph_and_schedule(self):
        self.assertTrue(run("graph", V1)[1].startswith("flowchart TD"))
        self.assertTrue(run("graph", V1, "--format", "dot")[1].startswith("digraph"))
        out = run("schedule", V1, "--workers", "1")[1]
        self.assertIn("makespan 132 on 1 worker(s)", out)

    def test_diff_flags_exit_nonzero(self):
        code, out, _ = run("diff", V1, V2)
        self.assertEqual(code, 1)
        self.assertIn("approval removed from refund", out)
        self.assertEqual(run("diff", V1, V1)[0], 0)

    def test_json_plans_and_bad_input(self):
        with tempfile.TemporaryDirectory() as d:
            ok = os.path.join(d, "p.json")
            with open(ok, "w", encoding="utf-8") as fh:
                json.dump({"steps": [{"id": "a", "latency": 1}]}, fh)
            self.assertEqual(run("analyze", ok)[0], 0)
            bad = os.path.join(d, "bad.odl")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("plan { step a after nope { } }")
            code, _, err = run("check", bad)
            self.assertEqual(code, 2)
            self.assertIn("nope", err)
            self.assertEqual(run("check", os.path.join(d, "missing.odl"))[0], 2)


if __name__ == "__main__":
    unittest.main()
