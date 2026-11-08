import unittest

from orchestra_dsl import SpecError, load

SPEC = {"steps": [
    {"id": "ingest", "cost": 0.1, "latency": 2},
    {"id": "classify", "depends_on": ["ingest"], "model": "small", "cost": 0.2, "latency": 1},
    {"id": "extract", "depends_on": ["ingest"], "model": "large", "cost": 1.5, "latency": 6},
    {"id": "publish", "depends_on": ["classify", "extract"], "cost": 0.0, "latency": 1, "approval": True},
]}


class PlanTests(unittest.TestCase):
    def test_order_respects_dependencies(self):
        order = load(SPEC).order()
        self.assertLess(order.index("ingest"), order.index("extract"))
        self.assertEqual(order[-1], "publish")

    def test_parallel_levels(self):
        self.assertEqual(load(SPEC).levels(), [["ingest"], ["classify", "extract"], ["publish"]])

    def test_cost_and_approvals(self):
        plan = load(SPEC)
        self.assertAlmostEqual(plan.total_cost(), 1.8)
        self.assertEqual(plan.approvals(), ["publish"])

    def test_critical_path_follows_slowest_branch(self):
        path, latency = load(SPEC).critical_path()
        self.assertEqual(path, ["ingest", "extract", "publish"])
        self.assertEqual(latency, 9)


class ValidationTests(unittest.TestCase):
    def test_empty_spec(self):
        with self.assertRaises(SpecError):
            load({})

    def test_duplicate_ids(self):
        with self.assertRaises(SpecError):
            load({"steps": [{"id": "a"}, {"id": "a"}]})

    def test_unknown_dependency(self):
        with self.assertRaises(SpecError):
            load({"steps": [{"id": "a", "depends_on": ["ghost"]}]})

    def test_cycle(self):
        with self.assertRaises(SpecError):
            load({"steps": [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}]})

    def test_negative_cost(self):
        with self.assertRaises(SpecError):
            load({"steps": [{"id": "a", "cost": -1}]})


if __name__ == "__main__":
    unittest.main()
