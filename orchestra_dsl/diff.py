"""What changed between two versions of a plan, and which changes a reviewer must look at."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import analysis
from .lint import gated
from .model import _FIELDS, Plan


@dataclass
class PlanDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: dict[str, dict[str, tuple[object, object]]] = field(default_factory=dict)
    cost_delta: float = 0.0  # expected cost, new minus old
    latency_delta: float = 0.0  # expected latency
    flags: list[str] = field(default_factory=list)  # changes that touch the approval surface or add risk

    @property
    def identical(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def render(self) -> str:
        lines = []
        lines += [f"+ {s}" for s in self.added] + [f"- {s}" for s in self.removed]
        for sid, fields in self.changed.items():
            lines += [f"~ {sid}.{f}: {old!r} -> {new!r}" for f, (old, new) in fields.items()]
        lines.append(f"expected cost {self.cost_delta:+.4f}, expected latency {self.latency_delta:+.3f}")
        lines += [f"REVIEW: {f}" for f in self.flags]
        return "\n".join(lines)


def diff(old: Plan, new: Plan, approval_risk: int = 3) -> PlanDiff:
    d = PlanDiff()
    d.added = [s for s in new.steps if s not in old.steps]
    d.removed = [s for s in old.steps if s not in new.steps]
    for sid in new.steps:
        if sid in old.steps:
            a, b = old.steps[sid], new.steps[sid]
            fields = {f: (getattr(a, f), getattr(b, f)) for f in sorted(_FIELDS - {"id"}) if getattr(a, f) != getattr(b, f)}
            if fields:
                d.changed[sid] = fields
    d.cost_delta = analysis.expected_cost(new) - analysis.expected_cost(old)
    d.latency_delta = analysis.makespan(new, "expected") - analysis.makespan(old, "expected")
    for sid in d.added:
        if new.steps[sid].risk >= approval_risk:
            d.flags.append(f"new step {sid} has risk {new.steps[sid].risk}")
        if new.steps[sid].approval:
            d.flags.append(f"new approval gate {sid}")
    for sid in d.removed:
        if old.steps[sid].approval:
            d.flags.append(f"approval gate {sid} was removed")
    for sid, fields in d.changed.items():
        if "approval" in fields and fields["approval"] == (True, False):
            d.flags.append(f"approval removed from {sid}")
        if "risk" in fields and fields["risk"][1] > fields["risk"][0]:  # type: ignore[operator]
            d.flags.append(f"risk of {sid} rose from {fields['risk'][0]} to {fields['risk'][1]}")
        if "idempotent" in fields and fields["idempotent"] == (True, False):
            d.flags.append(f"{sid} is no longer idempotent")
        if "retries" in fields and not new.steps[sid].idempotent and fields["retries"][1] > fields["retries"][0]:  # type: ignore[operator]
            d.flags.append(f"{sid} is retried more, and it is not idempotent")
    for sid in new.steps:  # a gate can be lost through the dependency graph without any approval flag changing
        if sid in old.steps and old.steps[sid].risk >= approval_risk and gated(old, sid) and not gated(new, sid):
            d.flags.append(f"{sid} is no longer behind an approval")
    d.flags = list(dict.fromkeys(d.flags))  # drop repeats, keep order
    return d
