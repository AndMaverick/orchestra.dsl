# orchestra.dsl

[![CI](https://github.com/AndMaverick/orchestra.dsl/actions/workflows/ci.yml/badge.svg)](https://github.com/AndMaverick/orchestra.dsl/actions/workflows/ci.yml)

Write an AI workflow as a small language, then ask the questions that matter before anything runs. Is it valid. Where is the time going, and what would speeding up each step actually buy. What does it cost once failures and retries are counted. Where does a human have to approve, and is any risky step reachable without one. What changed between this version and the last. Standard library only, typed, Python 3.10 and up.

```
pip install git+https://github.com/AndMaverick/orchestra.dsl
```

## The language

```
plan "dispute-desk" {
  step ingest   { latency 2  outputs [claim] }
  step classify after ingest { model small  cost 0.002  latency 1  inputs [claim]  outputs [category]  retries 1  p_fail 0.02 }
  step evidence after ingest { model large  cost 0.04   latency 6  inputs [claim]  outputs [findings]  retries 2  p_fail 0.08 }
  step decide   after classify, evidence { model large  cost 0.03  latency 3  inputs [category, findings]  outputs [decision] }
  step refund   after decide { risk 4  approval  latency 120  retries 2  p_fail 0.02  idempotent false  inputs [decision]  final }
}
```

Attributes are whitespace or `;` separated, `#` and `//` start comments, and syntax errors name the line and column with a caret. A plan can also be a plain dict or JSON (`load`). Printing a parsed plan and parsing it again gives the same plan, which the tests check on random plans.

## Ask it things

```
$ python -m orchestra_dsl analyze examples/dispute_desk.odl --mode expected
dispute-desk  5 steps  max parallelism 2  digest 4c85bebc4adb2c62
latency  nominal 131   expected 134   worst 383
cost     nominal 0.072   expected 0.07547   worst 0.154
success probability 0.9991
approvals: refund
critical: ingest -> evidence -> decide -> refund
leverage (time saved if the step were instant):
  refund               122.4
  evidence             5.498
  decide               3
  ingest               2
  classify             0
```

- **Critical path and slack.** Full critical path method: earliest and latest start for every step, and slack. `classify` has slack, so making it faster changes nothing, and `leverage` says so in numbers.
- **Cost under failure.** Each step's cost is multiplied by its expected attempts (`1 + p + p^2 + ...` up to its retries) and weighted by the chance it is reached, since a step that fails after its retries stops everything downstream. Nominal, expected and worst case are reported side by side. A Monte Carlo test checks the expected cost against simulation.
- **Scheduling.** `schedule(plan, workers=n)` runs list scheduling on n workers. The tests check on random plans that no dependency is violated, no worker does two things at once, and the makespan stays inside Graham's bounds.
- **Dataflow.** `inputs` must come from an upstream step that produces them, and two steps cannot produce the same output. Both are load time errors.

## Check it before it runs

```
$ python -m orchestra_dsl check examples/dispute_desk.odl
WARNING OD202 [refund]: not idempotent but retried up to 2 times: a retry can apply the action twice
```

That is a real design flaw in the example: the refund can be sent twice. The checks have stable codes so a pipeline can gate on them.

| Code | Severity | Meaning |
|---|---|---|
| OD201 | error | A step at or above the risk threshold has no approval on it or before it |
| OD202 | warning | A nonidempotent step is retried |
| OD301 / 302 / 303 | error | Expected cost, expected latency or success probability outside your limit |
| OD401 | warning | Model not in the allowed list |
| OD501 / 502 | warning | Fan in or depth over the limit |
| OD601 | warning | An output that nothing consumes |

`check` exits 1 on any error (`--strict` includes warnings), so it works as a CI gate: `--max-cost 0.10 --max-latency 200 --approval-risk 3 --models small,large`.

## Review a change

```
$ python -m orchestra_dsl diff examples/dispute_desk.odl examples/dispute_desk_v2.odl
~ refund.approval: True -> False
~ refund.latency: 120.0 -> 2.0
expected cost +0.0000, expected latency -120.407
REVIEW: approval removed from refund
REVIEW: refund is no longer behind an approval
```

The diff shows what moved in cost and latency, and separately flags the changes a reviewer must read: an approval removed, a gate lost through the dependency graph, risk raised, a step made nonidempotent, a new high risk step. Exit code 1 when any are present.

## Pin what was approved

`plan.digest()` is a SHA-256 of the plan's meaning. Step order, dependency order and formatting do not change it, and any change to a cost, an approval or a dependency does. Record the digest when a plan is approved and check it before it runs, so the plan that executes is shown to be the plan that was approved. The orchestration layer of [mao-framework](https://github.com/AndMaverick/mao-framework) executes plans under policy and audit.

## Draw it

`python -m orchestra_dsl graph plan.odl` prints Mermaid, which GitHub renders in markdown. `--format dot` prints Graphviz. Approval gates are hexagons and critical steps are outlined.

## Honest limits

- Latencies, costs and failure rates are numbers you provide. The analysis is only as good as they are, so feed it measured values (see [agent.trace](https://github.com/AndMaverick/agent.trace) for where to get them).
- Failures are assumed independent. Real outages are correlated, so treat the success probability as an optimistic bound.
- The expected cost model is fail stop on the failing branch. It does not model compensating actions or partial rollbacks.
- The worker scheduler is a good heuristic, not an optimal schedule.
- This describes and checks a plan. It does not run one.

## Tests

```
python -m unittest discover -s tests -t . -v
```

**Related repositories:** [playbook.compiler](https://github.com/AndMaverick/playbook.compiler) turns a markdown runbook into a plan this tool can check. [mao-framework](https://github.com/AndMaverick/mao-framework) is the framework whose orchestration layer executes plans under policy and audit.

MIT licensed. Built by [AndMaverick LLC](https://www.andmaverick.com).

---

**If this is your problem.** If you are deciding what to build, buy, and fund, I help leadership teams make that call with evidence. [Begin a conversation](https://calendly.com/andmaverick?utm_source=github&utm_medium=repo&utm_campaign=orchestra.dsl).
