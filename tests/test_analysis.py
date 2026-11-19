import unittest

from orchestra_dsl import (
    LintConfig,
    SpecError,
    cpm,
    critical_steps,
    diff,
    dumps,
    expected_cost,
    leverage,
    lint,
    load,
    loads,
    makespan,
    max_parallelism,
    run_probability,
    schedule,
    success_probability,
    summary,
    to_dot,
    to_mermaid,
    worst_cost,
)
from tests.test_lang import random_plan
from tests.test_orchestra_dsl import SPEC


class CpmTests(unittest.TestCase):
    def test_timings_by_hand(self):
        t = cpm(load(SPEC))
        self.assertEqual((t["ingest"].es, t["ingest"].ef, t["ingest"].slack), (0, 2, 0))
        self.assertEqual((t["classify"].es, t["classify"].ef, t["classify"].ls, t["classify"].slack), (2, 3, 7, 5))
        self.assertEqual((t["extract"].es, t["extract"].ef, t["extract"].slack), (2, 8, 0))
        self.assertEqual((t["publish"].es, t["publish"].ef), (8, 9))
        self.assertEqual(critical_steps(load(SPEC)), ["ingest", "extract", "publish"])

    def test_leverage_ranks_where_effort_pays(self):
        lev = leverage(load(SPEC))
        self.assertEqual(lev, {"extract": 5, "ingest": 2, "publish": 1, "classify": 0})  # classify has slack: speeding it up changes nothing

    def test_ties_make_both_branches_critical(self):
        p = load({"steps": [{"id": "a", "latency": 1}, {"id": "b", "depends_on": ["a"], "latency": 3}, {"id": "c", "depends_on": ["a"], "latency": 3},
                            {"id": "d", "depends_on": ["b", "c"], "latency": 1}]})
        self.assertEqual(critical_steps(p), ["a", "b", "c", "d"])
        self.assertEqual(leverage(p)["b"], 0)  # speeding up one of two tied branches saves nothing

    def test_slack_never_negative_and_critical_chain_spans_the_makespan(self):
        for seed in range(40):
            p = random_plan(seed)
            t = cpm(p)
            self.assertTrue(all(x.slack >= -1e-9 for x in t.values()), seed)
            crit = [x for x in t.values() if x.critical]
            self.assertAlmostEqual(min(x.es for x in crit), 0)
            self.assertAlmostEqual(max(x.ef for x in crit), makespan(p))

    def test_parallelism(self):
        self.assertEqual(max_parallelism(load(SPEC)), 2)
        self.assertEqual(max_parallelism(load({"steps": [{"id": "a", "latency": 1}, {"id": "b", "depends_on": ["a"], "latency": 1}]})), 1)


class FailureModelTests(unittest.TestCase):
    P = {"steps": [{"id": "a", "cost": 1.0, "latency": 10, "retries": 1, "p_fail": 0.5},
                   {"id": "b", "depends_on": ["a"], "cost": 4.0, "latency": 2}]}

    def test_attempts_success_and_durations(self):
        a = load(self.P).steps["a"]
        self.assertAlmostEqual(a.attempts, 1.5)
        self.assertAlmostEqual(a.success, 0.75)
        self.assertAlmostEqual(makespan(load(self.P), "expected"), 15 + 2)
        self.assertAlmostEqual(makespan(load(self.P), "worst"), 20 + 2)

    def test_expected_cost_weights_later_steps_by_the_chance_they_run(self):
        p = load(self.P)
        self.assertAlmostEqual(run_probability(p)["b"], 0.75)
        self.assertAlmostEqual(expected_cost(p), 1.0 * 1.5 + 0.75 * 4.0)
        self.assertAlmostEqual(worst_cost(p), 2.0 + 4.0)
        self.assertAlmostEqual(success_probability(p), 0.75)

    def test_no_failure_model_means_expected_equals_nominal(self):
        p = load(SPEC)
        self.assertAlmostEqual(expected_cost(p), p.total_cost())
        self.assertEqual(success_probability(p), 1.0)

    def test_expected_cost_matches_a_simulation(self):
        import random
        p = random_plan(3)
        rng = random.Random(1)
        total, trials = 0.0, 40000
        order = p.order()
        for _ in range(trials):
            ok: dict[str, bool] = {}
            for sid in order:
                s = p.steps[sid]
                if not all(ok[d] for d in s.depends_on):
                    ok[sid] = False
                    continue
                done = False
                for _try in range(s.retries + 1):
                    total += s.cost
                    if rng.random() >= s.p_fail:
                        done = True
                        break
                ok[sid] = done
        self.assertAlmostEqual(total / trials, expected_cost(p), delta=expected_cost(p) * 0.02)


class ScheduleTests(unittest.TestCase):
    def test_by_hand(self):
        p = load(SPEC)
        one, two = schedule(p, 1), schedule(p, 2)
        self.assertEqual(one.makespan, 10)
        self.assertEqual(two.makespan, 9)
        self.assertAlmostEqual(two.utilization, 10 / 18)
        self.assertEqual(schedule(p, 8).makespan, 9)  # more workers than parallelism does not help
        with self.assertRaises(ValueError):
            schedule(p, 0)

    def test_valid_and_within_the_grahams_bounds_on_random_plans(self):
        for seed in range(50):
            p = random_plan(seed, 14)
            work = sum(s.latency for s in p.steps.values())
            cp = makespan(p)
            for m in (1, 2, 3, 5):
                sch = schedule(p, m)
                by = {s.step: s for s in sch.slots}
                self.assertEqual(len(by), len(p.steps))
                for sid, step in p.steps.items():
                    for d in step.depends_on:
                        self.assertGreaterEqual(by[sid].start + 1e-9, by[d].end)  # dependencies respected
                for w in range(m):
                    mine = sorted((s for s in sch.slots if s.worker == w), key=lambda s: s.start)
                    for x, y in zip(mine, mine[1:], strict=False):
                        self.assertGreaterEqual(y.start + 1e-9, x.end)  # one thing at a time per worker
                self.assertGreaterEqual(sch.makespan + 1e-9, max(cp, work / m))
                self.assertLessEqual(sch.makespan, work / m + (1 - 1 / m) * cp + 1e-9)  # Graham's list-scheduling bound
            self.assertAlmostEqual(schedule(p, 1).makespan, work)


class LintTests(unittest.TestCase):
    def codes(self, src, cfg=None):
        return [(f.code, f.step) for f in lint(loads(src), cfg)]

    def test_risky_step_needs_a_gate_on_it_or_before_it(self):
        self.assertIn(("OD201", "pay"), self.codes("plan { step pay { risk 4 } }"))
        self.assertEqual(self.codes("plan { step pay { risk 4 approval } }"), [])
        self.assertEqual(self.codes("plan { step ok { approval } step pay after ok { risk 5 } }"), [])
        self.assertIn(("OD201", "pay"), self.codes("plan { step ok { approval } step pay { risk 5 } }"))  # a gate that is not upstream does not count
        self.assertEqual(self.codes("plan { step pay { risk 2 } }"), [])
        self.assertIn(("OD201", "pay"), self.codes("plan { step pay { risk 2 } }", LintConfig(approval_risk=2)))

    def test_retrying_something_not_idempotent(self):
        self.assertIn(("OD202", "send"), self.codes("plan { step send { retries 2 idempotent false } }"))
        self.assertEqual(self.codes("plan { step send { retries 2 } }"), [])

    def test_model_policy(self):
        cfg = LintConfig(allowed_models=("small",))
        self.assertEqual(self.codes("plan { step a { model huge } step b { model small } }", cfg), [("OD401", "a")])

    def test_structure_limits(self):
        wide = "plan { " + " ".join(f"step s{i} {{ }}" for i in range(4)) + " step j after s0, s1, s2, s3 { } }"
        self.assertIn(("OD501", "j"), self.codes(wide, LintConfig(max_fan_in=3)))
        chain = "plan { step a { } step b after a { } step c after b { } }"
        self.assertIn(("OD502", None), self.codes(chain, LintConfig(max_depth=2)))

    def test_dead_outputs(self):
        src = "plan { step a { outputs [x, y] } step b after a { inputs [x] } }"
        self.assertEqual(self.codes(src), [("OD601", "a")])
        self.assertEqual(self.codes(src.replace("outputs [x, y]", "outputs [x, y] final")), [])
        self.assertEqual(self.codes("plan { step only { outputs [x] } }"), [])  # a last step's outputs leave the plan

    def test_budgets_use_expected_values(self):
        src = "plan { step a { cost 1 latency 10 retries 1 p_fail 0.5 } }"
        self.assertEqual(self.codes(src, LintConfig(max_cost=1.5)), [])
        self.assertEqual(self.codes(src, LintConfig(max_cost=1.4)), [("OD301", None)])
        self.assertEqual(self.codes(src, LintConfig(max_latency=14)), [("OD302", None)])
        self.assertEqual(self.codes(src, LintConfig(min_success=0.8)), [("OD303", None)])

    def test_clean_plan_has_no_findings(self):
        self.assertEqual(lint(load(SPEC)), [])
        self.assertIn("OD201", str(lint(loads("plan { step p { risk 5 } }"))[0]))


class DataflowTests(unittest.TestCase):
    def test_inputs_must_come_from_an_ancestor(self):
        with self.assertRaisesRegex(SpecError, "no step produces"):
            loads("plan { step a { inputs [ghost] } }")
        with self.assertRaisesRegex(SpecError, "does not depend on"):
            loads("plan { step a { outputs [x] } step b { inputs [x] } }")
        with self.assertRaisesRegex(SpecError, "both produce"):
            loads("plan { step a { outputs [x] } step b { outputs [x] } }")
        loads("plan { step a { outputs [x] } step b { } step c after a, b { inputs [x] } }")
        loads("plan { step a { outputs [x] } step b after a { } step c after b { inputs [x] } }")  # transitive is fine


class DiffTests(unittest.TestCase):
    BASE = "plan { step read { } step pay after read { risk 4 approval cost 1 } step notify after pay { cost 0.1 } }"

    def flags(self, new):
        return diff(loads(self.BASE), loads(new)).flags

    def test_identical(self):
        d = diff(loads(self.BASE), loads(self.BASE))
        self.assertTrue(d.identical)
        self.assertEqual(d.flags, [])

    def test_removing_the_approval_is_flagged(self):
        self.assertIn("approval removed from pay", self.flags(self.BASE.replace(" approval", "")))

    def test_losing_a_gate_through_the_graph_is_flagged(self):
        old = "plan { step gate { approval } step act after gate { risk 4 } }"
        new = "plan { step gate { approval } step act { risk 4 } }"
        self.assertIn("act is no longer behind an approval", diff(loads(old), loads(new)).flags)

    def test_new_risky_step_and_risk_increase_and_idempotency(self):
        self.assertIn("new step wipe has risk 5", self.flags(self.BASE[:-2] + " step wipe { risk 5 } }"))
        self.assertIn("risk of notify rose from 0 to 3", self.flags(self.BASE.replace("notify after pay { cost 0.1", "notify after pay { cost 0.1 risk 3")))
        self.assertIn("pay is no longer idempotent", self.flags(self.BASE.replace("approval cost 1", "approval cost 1 idempotent false")))

    def test_cost_and_latency_deltas_and_render(self):
        d = diff(loads(self.BASE), loads(self.BASE.replace("cost 1 ", "cost 3 ")))
        self.assertAlmostEqual(d.cost_delta, 2.0)
        self.assertIn("~ pay.cost: 1.0 -> 3.0", d.render())
        self.assertEqual(d.flags, [])

    def test_added_and_removed(self):
        d = diff(loads(self.BASE), loads("plan { step read { } step pay after read { risk 4 approval cost 1 } step audit after pay { } }"))
        self.assertEqual((d.added, d.removed), (["audit"], ["notify"]))


class DigestAndRenderTests(unittest.TestCase):
    def test_digest_ignores_form_but_not_meaning(self):
        a = loads("plan { step a { cost 1 } step b after a { latency 2 } }")
        b = loads("plan {\n  step b after a { latency 2.0 }\n  step a { cost 1.0 }\n}")  # reordered and reformatted
        self.assertEqual(a.digest(), b.digest())
        self.assertEqual(loads(dumps(a)).digest(), a.digest())
        self.assertNotEqual(a.digest(), loads("plan { step a { cost 1.01 } step b after a { latency 2 } }").digest())
        self.assertNotEqual(a.digest(), loads("plan { step a { cost 1 } step b after a { latency 2 approval } }").digest())
        self.assertEqual(len(a.digest()), 64)

    def test_mermaid_and_dot(self):
        p = load(SPEC)
        m = to_mermaid(p)
        self.assertTrue(m.startswith("flowchart TD"))
        self.assertIn('{{"publish | $0 | 1s"}}'.replace(" | $0", ""), m)  # approval gates are hexagons
        self.assertEqual(m.count("-->"), 4)
        self.assertEqual(m.count("class n"), 3 + 1)  # three critical steps and one gate
        d = to_dot(p)
        self.assertIn('"ingest" -> "classify";', d)
        self.assertIn("shape=hexagon", d)
        self.assertIn("color=red", d)

    def test_summary_is_json_friendly(self):
        import json
        s = summary(load(SPEC))
        json.dumps(s)
        self.assertEqual((s["steps"], s["approvals"], s["max_parallelism"]), (4, ["publish"], 2))
        self.assertEqual(s["levels"], [["ingest"], ["classify", "extract"], ["publish"]])


if __name__ == "__main__":
    unittest.main()
