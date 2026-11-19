"""The plan model: steps, validation at load time, and a stable order."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any


class SpecError(ValueError):
    """The plan specification is invalid."""


@dataclass(frozen=True)
class StepSpec:
    id: str
    depends_on: tuple[str, ...] = ()
    model: str = ""
    cost: float = 0.0
    latency: float = 0.0
    approval: bool = False
    risk: int = 0  # 0 (read-only) to 5 (irreversible, external)
    retries: int = 0
    p_fail: float = 0.0  # chance one attempt fails, for expected cost and success probability
    idempotent: bool = True
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    final: bool = False  # its outputs leave the plan, so they need no consumer

    @property
    def attempts(self) -> float:
        """Expected attempts with up to ``retries`` retries: 1 + p + p^2 + ... (truncated geometric series)."""
        return sum(self.p_fail ** k for k in range(self.retries + 1))

    @property
    def success(self) -> float:
        """Probability the step succeeds within its attempts."""
        return 1.0 - self.p_fail ** (self.retries + 1)


_FIELDS = {"id", "depends_on", "model", "cost", "latency", "approval", "risk", "retries", "p_fail", "idempotent", "inputs", "outputs", "tags", "final"}


@dataclass(eq=False)
class Plan:
    steps: dict[str, StepSpec]
    name: str = ""
    _order: list[str] | None = field(default=None, repr=False, compare=False)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Plan) and self.name == other.name and list(self.steps.values()) == list(other.steps.values())

    # ------------------------------------------------------------------------------------------ structure
    def order(self) -> list[str]:
        """A topological order that is stable: among ready steps, the one declared first goes first."""
        if self._order is None:
            position = {sid: i for i, sid in enumerate(self.steps)}
            remaining = {sid: len(set(s.depends_on)) for sid, s in self.steps.items()}
            dependents: dict[str, list[str]] = {sid: [] for sid in self.steps}
            for sid, s in self.steps.items():
                for d in set(s.depends_on):
                    dependents[d].append(sid)
            ready = sorted((sid for sid, n in remaining.items() if n == 0), key=position.__getitem__)
            out: list[str] = []
            while ready:
                sid = ready.pop(0)
                out.append(sid)
                for nxt in dependents[sid]:
                    remaining[nxt] -= 1
                    if remaining[nxt] == 0:
                        ready.append(nxt)
                ready.sort(key=position.__getitem__)
            if len(out) != len(self.steps):
                raise SpecError("plan contains a dependency cycle: " + ", ".join(sorted(set(self.steps) - set(out))))
            self._order = out
        return list(self._order)

    def levels(self) -> list[list[str]]:
        """Groups of steps that can run in parallel, in execution order."""
        depth: dict[str, int] = {}
        for sid in self.order():
            depth[sid] = 1 + max((depth[d] for d in self.steps[sid].depends_on), default=-1)
        grouped: dict[int, list[str]] = {}
        for sid, level in depth.items():
            grouped.setdefault(level, []).append(sid)
        return [sorted(grouped[k]) for k in sorted(grouped)]

    def ancestors(self, sid: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.steps[sid].depends_on)
        while stack:
            d = stack.pop()
            if d not in seen:
                seen.add(d)
                stack.extend(self.steps[d].depends_on)
        return seen

    def dependents(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {sid: [] for sid in self.steps}
        for sid in self.order():
            for d in self.steps[sid].depends_on:
                if sid not in out[d]:
                    out[d].append(sid)
        return out

    def sinks(self) -> list[str]:
        deps = self.dependents()
        return [sid for sid in self.order() if not deps[sid]]

    # ------------------------------------------------------------------------------------------ the original questions
    def total_cost(self) -> float:
        return round(sum(s.cost for s in self.steps.values()), 6)

    def critical_path(self) -> tuple[list[str], float]:
        """Longest chain by nominal latency. Returns (step ids, total latency)."""
        best: dict[str, tuple[list[str], float]] = {}
        for sid in self.order():
            step = self.steps[sid]
            prior = max((best[d] for d in step.depends_on), key=lambda r: r[1], default=([], 0.0))
            best[sid] = (prior[0] + [sid], prior[1] + step.latency)
        return max(best.values(), key=lambda r: r[1])

    def approvals(self) -> list[str]:
        return [i for i in self.order() if self.steps[i].approval]

    # ------------------------------------------------------------------------------------------ identity
    def to_dict(self) -> dict[str, Any]:
        defaults = StepSpec("x")
        steps = []
        for s in self.steps.values():
            d: dict[str, Any] = {"id": s.id}
            for f in sorted(_FIELDS - {"id"}):
                v = getattr(s, f)
                if v != getattr(defaults, f):
                    d[f] = list(v) if isinstance(v, tuple) else v
            steps.append(d)
        return {"name": self.name, "steps": steps}

    def digest(self) -> str:
        """SHA-256 of the plan's meaning (step order, dependency order and formatting do not change it), so an approved
        plan can be pinned and the plan that runs shown to be the plan that was approved."""
        canon = []
        for sid in sorted(self.steps):
            s = self.steps[sid]
            d = {f: getattr(s, f) for f in sorted(_FIELDS)}
            d["depends_on"] = sorted(s.depends_on)
            for key in ("inputs", "outputs", "tags"):
                d[key] = sorted(d[key])
            canon.append(d)
        payload = json.dumps({"name": self.name, "steps": canon}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _num(item: dict[str, Any], key: str, sid: str, lo: float = 0.0, hi: float | None = None, integer: bool = False) -> float:
    v = item.get(key, 0)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SpecError(f"{sid}: {key} must be a number")
    if math.isnan(v) or v < lo or (hi is not None and v > hi):  # NaN fails every comparison, so test it explicitly
        bound = f" between {lo:g} and {hi:g}" if hi is not None else " non-negative"
        raise SpecError(f"{sid}: {key} must be{bound}")
    if integer and int(v) != v:
        raise SpecError(f"{sid}: {key} must be a whole number")
    return float(v)


def _strs(item: dict[str, Any], key: str, sid: str) -> tuple[str, ...]:
    v = item.get(key, ())
    if isinstance(v, str) or not isinstance(v, (list, tuple)) or not all(isinstance(x, str) and x for x in v):
        raise SpecError(f"{sid}: {key} must be a list of non-empty strings")
    return tuple(v)


def load(spec: dict[str, Any]) -> Plan:
    """Build and validate a plan from a dict. Every problem is a ``SpecError`` naming the step."""
    raw = spec.get("steps") if isinstance(spec, dict) else None
    if not raw or not isinstance(raw, list):
        raise SpecError("spec needs a non-empty 'steps' list")
    steps: dict[str, StepSpec] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise SpecError("every step must be a mapping")
        sid = item.get("id")
        if not isinstance(sid, str) or not sid:
            raise SpecError("every step needs a string id")
        if sid in steps:
            raise SpecError(f"duplicate step id: {sid}")
        unknown = set(item) - _FIELDS
        if unknown:
            raise SpecError(f"{sid}: unknown field {min(unknown)!r}")
        model = item.get("model", "")
        if not isinstance(model, str):
            raise SpecError(f"{sid}: model must be a string")
        steps[sid] = StepSpec(
            sid, _strs(item, "depends_on", sid), model, _num(item, "cost", sid), _num(item, "latency", sid),
            bool(item.get("approval", False)), int(_num(item, "risk", sid, 0, 5, True)), int(_num(item, "retries", sid, 0, 20, True)),
            _num(item, "p_fail", sid, 0.0, 0.999999), bool(item.get("idempotent", True)), _strs(item, "inputs", sid),
            _strs(item, "outputs", sid), _strs(item, "tags", sid), bool(item.get("final", False)),
        )
    for step in steps.values():
        for dep in step.depends_on:
            if dep == step.id:
                raise SpecError(f"{step.id} depends on itself")
            if dep not in steps:
                raise SpecError(f"{step.id} depends on unknown step: {dep}")
    plan = Plan(steps, str(spec.get("name", "")))
    plan.order()  # raises SpecError on a cycle
    _check_dataflow(plan)
    return plan


def _check_dataflow(plan: Plan) -> None:
    producer: dict[str, str] = {}
    for sid, s in plan.steps.items():
        for out in s.outputs:
            if out in producer:
                raise SpecError(f"{sid} and {producer[out]} both produce {out!r}")
            producer[out] = sid
    for sid, s in plan.steps.items():
        anc = plan.ancestors(sid)
        for name in s.inputs:
            if name not in producer:
                raise SpecError(f"{sid} needs {name!r}, which no step produces")
            if producer[name] not in anc:
                raise SpecError(f"{sid} needs {name!r} from {producer[name]}, which it does not depend on")
