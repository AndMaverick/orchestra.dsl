"""orchestra.dsl: declare an AI plan as data. Validate it, order it, cost it, find its critical path."""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter

__version__ = "0.1.0"


class SpecError(ValueError):
    """The plan specification is invalid."""


@dataclass(frozen=True)
class StepSpec:
    id: str
    depends_on: tuple = ()
    model: str = ""
    cost: float = 0.0
    latency: float = 0.0
    approval: bool = False


class Plan:
    def __init__(self, steps: dict) -> None:
        self.steps = steps

    def order(self) -> list:
        graph = {s.id: set(s.depends_on) for s in self.steps.values()}
        return list(TopologicalSorter(graph).static_order())

    def levels(self) -> list:
        """Groups of steps that can run in parallel, in execution order."""
        depth: dict = {}
        for step_id in self.order():
            deps = self.steps[step_id].depends_on
            depth[step_id] = 1 + max((depth[d] for d in deps), default=-1)
        grouped: dict = {}
        for step_id, level in depth.items():
            grouped.setdefault(level, []).append(step_id)
        return [sorted(grouped[k]) for k in sorted(grouped)]

    def total_cost(self) -> float:
        return round(sum(s.cost for s in self.steps.values()), 6)

    def critical_path(self):
        """Longest chain by latency. Returns (step ids, total latency)."""
        best: dict = {}
        for step_id in self.order():
            step = self.steps[step_id]
            prior = max((best[d] for d in step.depends_on), key=lambda r: r[1], default=([], 0.0))
            best[step_id] = (prior[0] + [step_id], prior[1] + step.latency)
        return max(best.values(), key=lambda r: r[1])

    def approvals(self) -> list:
        return [i for i in self.order() if self.steps[i].approval]


def load(spec: dict) -> Plan:
    raw = spec.get("steps") if isinstance(spec, dict) else None
    if not raw:
        raise SpecError("spec needs a non-empty 'steps' list")
    steps: dict = {}
    for item in raw:
        step_id = item.get("id")
        if not isinstance(step_id, str) or not step_id:
            raise SpecError("every step needs a string id")
        if step_id in steps:
            raise SpecError(f"duplicate step id: {step_id}")
        if item.get("cost", 0) < 0 or item.get("latency", 0) < 0:
            raise SpecError(f"{step_id}: cost and latency must be non-negative")
        steps[step_id] = StepSpec(step_id, tuple(item.get("depends_on", ())), item.get("model", ""),
                                  float(item.get("cost", 0.0)), float(item.get("latency", 0.0)),
                                  bool(item.get("approval", False)))
    for step in steps.values():
        for dep in step.depends_on:
            if dep not in steps:
                raise SpecError(f"{step.id} depends on unknown step: {dep}")
    plan = Plan(steps)
    try:
        plan.order()
    except CycleError:
        raise SpecError("plan contains a dependency cycle") from None
    return plan
