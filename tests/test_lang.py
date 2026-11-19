import random
import unittest

from orchestra_dsl import ParseError, SpecError, dumps, load, loads

SOURCE = """\
# a plan with every kind of attribute
plan "dispute desk" {
  step ingest { cost 0.1  latency 2  outputs [raw, "doc one"] }
  step classify after ingest { model small  cost 0.2  latency 1  inputs [raw] ; tags [fast] }
  step extract after ingest { model large  cost 1.5e0  latency 6 }  // trailing comment
  step refund after classify, extract { risk 4  approval  retries 2  p_fail 0.05  idempotent false  final }
}
"""


def random_plan(seed, n=12):
    rng = random.Random(seed)
    steps = []
    for i in range(n):
        deps = sorted(rng.sample([f"s{j}" for j in range(i)], rng.randint(0, min(i, 3)))) if i else []
        steps.append({"id": f"s{i}", "depends_on": deps, "cost": round(rng.random() * 3, 2), "latency": rng.randint(1, 9),
                      "model": rng.choice(["", "small", "large"]), "approval": rng.random() < 0.2, "risk": rng.randint(0, 5),
                      "retries": rng.randint(0, 3), "p_fail": rng.choice([0, 0.1, 0.25]), "idempotent": rng.random() < 0.8,
                      "tags": rng.sample(["a", "b", "c"], rng.randint(0, 2))})
    return load({"name": f"p{seed}", "steps": steps})


class ParseTests(unittest.TestCase):
    def test_parses_every_attribute_kind(self):
        p = loads(SOURCE)
        self.assertEqual(p.name, "dispute desk")
        self.assertEqual(p.steps["ingest"].outputs, ("raw", "doc one"))
        self.assertEqual(p.steps["extract"].cost, 1.5)
        r = p.steps["refund"]
        self.assertEqual((r.depends_on, r.risk, r.approval, r.retries, r.p_fail, r.idempotent, r.final),
                         (("classify", "extract"), 4, True, 2, 0.05, False, True))
        self.assertEqual(p.steps["classify"].tags, ("fast",))

    def test_print_then_parse_gives_the_same_plan(self):
        p = loads(SOURCE)
        self.assertEqual(loads(dumps(p)), p)
        self.assertEqual(dumps(loads(dumps(p))), dumps(p))  # printing is a fixed point

    def test_round_trip_on_random_plans(self):
        for seed in range(60):
            p = random_plan(seed)
            self.assertEqual(loads(dumps(p)), p, seed)

    def test_odd_names_are_quoted_and_survive(self):
        p = load({"name": 'a "quoted" name', "steps": [{"id": "step one"}, {"id": "true", "depends_on": ["step one"]}]})
        self.assertEqual(loads(dumps(p)), p)

    def test_flag_may_be_explicitly_false(self):
        self.assertFalse(loads("plan { step a { approval false } }").steps["a"].approval)

    def test_errors_point_at_the_line_and_column(self):
        cases = {
            "plan {\n  step a { cost x }\n}": (2, 17, "expected a number"),
            "plan {\n  step a { }\n  step a { }\n}": (3, 8, "duplicate step id"),
            "plan {\n  step a { bogus 1 }\n}": (2, 12, "unknown attribute"),
            "plan {\n  step a { cost 1 cost 2 }\n}": (2, 19, "set twice"),
            "plan {\n  step a { }\n": (3, 1, "expected '}'"),
            "plan {\n  step a { idempotent maybe }\n}": (2, 23, "true or false"),
            "plan { step a { cost 1 } } extra": (1, 28, "unexpected text"),
            "plan { step a { cost 1 @ } }": (1, 24, "unexpected character"),
        }
        for src, (line, col, text) in cases.items():
            with self.assertRaises(ParseError, msg=src) as cm:
                loads(src)
            self.assertEqual((cm.exception.line, cm.exception.col), (line, col), src)
            self.assertIn(text, str(cm.exception), src)

    def test_semantic_errors_point_at_the_step(self):
        with self.assertRaises(ParseError) as cm:
            loads("plan {\n  step a { }\n  step b after zzz { }\n}")
        self.assertEqual((cm.exception.line, cm.exception.col), (3, 3))
        self.assertIn("zzz", str(cm.exception))
        self.assertIsInstance(cm.exception, SpecError)

    def test_numeric_ranges_are_checked(self):
        for bad in ("risk 6", "retries 21", "p_fail 1", "cost -1", "retries 1.5"):
            with self.assertRaises(SpecError, msg=bad):
                loads(f"plan {{ step a {{ {bad} }} }}")


if __name__ == "__main__":
    unittest.main()
