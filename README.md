# orchestra.dsl

Write an AI workflow as plain data, then ask the questions that matter before anything runs: is it valid, what order does it execute in, what can run in parallel, what will it cost, how long will it take, and where does a human have to approve. Standard library only.

## Use

```python
from orchestra_dsl import load

plan = load({"steps": [
    {"id": "ingest", "cost": 0.1, "latency": 2},
    {"id": "classify", "depends_on": ["ingest"], "model": "small", "cost": 0.2, "latency": 1},
    {"id": "extract", "depends_on": ["ingest"], "model": "large", "cost": 1.5, "latency": 6},
    {"id": "publish", "depends_on": ["classify", "extract"], "latency": 1, "approval": True},
]})

plan.levels()         # [['ingest'], ['classify', 'extract'], ['publish']]
plan.total_cost()     # 1.8
plan.critical_path()  # (['ingest', 'extract', 'publish'], 9.0)
plan.approvals()      # ['publish']
```

## Design

- **Validation first.** Empty specs, duplicate ids, unknown dependencies, negative cost or latency, and cycles all raise `SpecError` at load time, with the offending step named.
- **Levels.** Steps grouped by how many dependencies deep they sit, so you can see exactly what is safe to run concurrently.
- **Critical path.** The longest chain by latency. It is the number to cut when the workflow is too slow.
- **Approvals as data.** Steps marked for human approval are listed up front, which makes the review surface of a workflow visible before it runs.
- **Pairs with** the orchestration layer of [mao-framework](https://github.com/AndMaverick/mao-framework), which executes plans under policy and audit.

## Tests

```
python -m unittest discover -s tests -t . -v
```

MIT licensed. Built by [AndMaverick LLC](https://www.andmaverick.com).

---

**If this is your problem.** If you are deciding what to build, buy, and fund, I help leadership teams make that call with evidence. [Begin a conversation](https://calendly.com/andmaverick?utm_source=github&utm_medium=repo&utm_campaign=orchestra.dsl).
