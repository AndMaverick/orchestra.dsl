"""Static analysis of a plan: critical path method, expected cost under failure, worker scheduling, leverage."""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any, Literal

from .model import Plan, StepSpec

Mode = Literal["nominal", "expected", "worst"]
EPS = 1e-9


def duration(step: StepSpec, mode: Mode = "nominal") -> float:
    """A step's time: one attempt (nominal), the average over retries (expected), or every retry used (worst)."""
    if mode == "nominal":
        return step.latency
    return step.latency * (step.attempts if mode == "expected" else step.retries + 1)


@dataclass(frozen=True)
class Timing:
    es: float  # earliest start
    ef: float
    ls: float  # latest start that does not delay the plan
    lf: float
    slack: float

    @property
    def critical(self) -> bool:
        return self.slack <= EPS


def cpm(plan: Plan, mode: Mode = "nominal", override: dict[str, float] | None = None) -> dict[str, Timing]:
    """Critical path method. Slack is how long a step can slip before the whole plan is later."""
    dur = {sid: (override or {}).get(sid, duration(s, mode)) for sid, s in plan.steps.items()}
    order = plan.order()
    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    for sid in order:
        es[sid] = max((ef[d] for d in plan.steps[sid].depends_on), default=0.0)
        ef[sid] = es[sid] + dur[sid]
    end = max(ef.values())
    deps = plan.dependents()
    lf: dict[str, float] = {}
    ls: dict[str, float] = {}
    for sid in reversed(order):
        lf[sid] = min((ls[n] for n in deps[sid]), default=end)
        ls[sid] = lf[sid] - dur[sid]
    return {sid: Timing(es[sid], ef[sid], ls[sid], lf[sid], round(ls[sid] - es[sid], 9)) for sid in order}


def makespan(plan: Plan, mode: Mode = "nominal", override: dict[str, float] | None = None) -> float:
    return max(t.ef for t in cpm(plan, mode, override).values())


def critical_steps(plan: Plan, mode: Mode = "nominal") -> list[str]:
    """Every step with zero slack, in order. More than one chain can be critical when branches tie."""
    return [sid for sid, t in cpm(plan, mode).items() if t.critical]


def run_probability(plan: Plan) -> dict[str, float]:
    """Chance each step is reached, if a step that fails after its retries stops everything that depends on it."""
    out: dict[str, float] = {}
    for sid in plan.order():
        p = 1.0
        for a in plan.ancestors(sid):
            p *= plan.steps[a].success
        out[sid] = p
    return out


def expected_cost(plan: Plan) -> float:
    """Expected spend: each step's cost times its expected attempts, weighted by the chance it is reached.
    Assumes attempts fail independently."""
    reach = run_probability(plan)
    return sum(reach[sid] * s.cost * s.attempts for sid, s in plan.steps.items())


def worst_cost(plan: Plan) -> float:
    """Spend if every step uses all its retries."""
    return sum(s.cost * (s.retries + 1) for s in plan.steps.values())


def success_probability(plan: Plan) -> float:
    """Chance every step succeeds within its retries."""
    p = 1.0
    for s in plan.steps.values():
        p *= s.success
    return p


@dataclass(frozen=True)
class Slot:
    step: str
    worker: int
    start: float
    end: float


@dataclass(frozen=True)
class Schedule:
    workers: int
    makespan: float
    slots: list[Slot]
    utilization: float  # busy time divided by workers times makespan


def schedule(plan: Plan, workers: int, mode: Mode = "nominal") -> Schedule:
    """List scheduling on ``workers`` identical workers, longest remaining path first. A step starts when a worker is
    free and its dependencies are done. Not optimal (that is NP-hard), but within a factor of two of optimal."""
    if workers < 1:
        raise ValueError("workers must be at least 1")
    dur = {sid: duration(s, mode) for sid, s in plan.steps.items()}
    deps = plan.dependents()
    bottom: dict[str, float] = {}
    for sid in reversed(plan.order()):
        bottom[sid] = dur[sid] + max((bottom[n] for n in deps[sid]), default=0.0)
    position = {sid: i for i, sid in enumerate(plan.order())}
    waiting = {sid: len(set(s.depends_on)) for sid, s in plan.steps.items()}
    ready: list[tuple[float, int, str]] = [(-bottom[sid], position[sid], sid) for sid, n in waiting.items() if n == 0]
    heapq.heapify(ready)
    running: list[tuple[float, int, str, int]] = []  # (end, tiebreak, step, worker)
    free = list(range(workers))
    now, slots = 0.0, []
    while ready or running:
        while ready and free:
            _, _, sid = heapq.heappop(ready)
            w = free.pop(0)
            slots.append(Slot(sid, w, now, now + dur[sid]))
            heapq.heappush(running, (now + dur[sid], position[sid], sid, w))
        end, _, sid, w = heapq.heappop(running)
        now = end
        free.append(w)
        free.sort()
        finished = [(end, sid, w)]
        while running and running[0][0] <= now + EPS:
            e2, _, s2, w2 = heapq.heappop(running)
            free.append(w2)
            free.sort()
            finished.append((e2, s2, w2))
        for _, done, _w in finished:
            for nxt in deps[done]:
                waiting[nxt] -= 1
                if waiting[nxt] == 0:
                    heapq.heappush(ready, (-bottom[nxt], position[nxt], nxt))
    span = max((s.end for s in slots), default=0.0)
    busy = sum(s.end - s.start for s in slots)
    return Schedule(workers, span, sorted(slots, key=lambda s: (s.start, s.worker)), busy / (workers * span) if span > 0 else 1.0)


def max_parallelism(plan: Plan, mode: Mode = "nominal") -> int:
    """The most steps that can be running at once with unlimited workers."""
    events: list[tuple[float, int]] = []
    for t in cpm(plan, mode).values():
        if t.ef - t.es > EPS:
            events += [(t.es, 1), (t.ef, -1)]
    events.sort(key=lambda e: (e[0], e[1]))  # ends before starts at the same instant
    cur = best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


def leverage(plan: Plan, mode: Mode = "nominal") -> dict[str, float]:
    """For each step, how much sooner the plan would finish if that step took no time. Zero for steps off the critical
    path: speeding them up changes nothing. The ranking is where optimisation effort pays."""
    base = makespan(plan, mode)
    out = {sid: round(base - makespan(plan, mode, {sid: 0.0}), 9) for sid in plan.steps}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def summary(plan: Plan, mode: Mode = "nominal") -> dict[str, Any]:
    timing = cpm(plan, mode)
    return {
        "name": plan.name, "digest": plan.digest(), "steps": len(plan.steps), "levels": plan.levels(),
        "approvals": plan.approvals(), "max_parallelism": max_parallelism(plan, mode),
        "latency": {"nominal": makespan(plan), "expected": makespan(plan, "expected"), "worst": makespan(plan, "worst")},
        "cost": {"nominal": plan.total_cost(), "expected": round(expected_cost(plan), 9), "worst": round(worst_cost(plan), 9)},
        "success_probability": success_probability(plan),
        "critical_steps": [s for s, t in timing.items() if t.critical],
        "slack": {s: t.slack for s, t in timing.items()},
        "leverage": leverage(plan, mode),
    }
