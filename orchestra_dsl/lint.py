"""Policy checks on a plan, before it runs. Each finding has a stable code so a pipeline can gate on it."""
from __future__ import annotations

from dataclasses import dataclass

from . import analysis
from .model import Plan


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # "error", "warning" or "info"
    step: str | None
    message: str

    def __str__(self) -> str:
        where = f" [{self.step}]" if self.step else ""
        return f"{self.severity.upper()} {self.code}{where}: {self.message}"


@dataclass(frozen=True)
class LintConfig:
    approval_risk: int = 3  # steps at or above this risk must be behind a human approval
    max_cost: float | None = None  # on expected cost
    max_latency: float | None = None  # on expected latency
    min_success: float | None = None
    allowed_models: tuple[str, ...] | None = None
    max_fan_in: int = 8
    max_depth: int = 12


def gated(plan: Plan, sid: str) -> bool:
    """A step is gated if it, or anything it depends on, requires approval."""
    return plan.steps[sid].approval or any(plan.steps[a].approval for a in plan.ancestors(sid))


def lint(plan: Plan, config: LintConfig | None = None) -> list[Finding]:
    cfg = config or LintConfig()
    out: list[Finding] = []
    for sid in plan.order():
        s = plan.steps[sid]
        if s.risk >= cfg.approval_risk and not gated(plan, sid):
            out.append(Finding("OD201", "error", sid, f"risk {s.risk} step runs with no human approval on it or before it"))
        if not s.idempotent and s.retries > 0:
            out.append(Finding("OD202", "warning", sid, f"not idempotent but retried up to {s.retries} times: a retry can apply the action twice"))
        if cfg.allowed_models is not None and s.model and s.model not in cfg.allowed_models:
            out.append(Finding("OD401", "warning", sid, f"model {s.model!r} is not in the allowed list"))
        if len(set(s.depends_on)) > cfg.max_fan_in:
            out.append(Finding("OD501", "warning", sid, f"depends on {len(set(s.depends_on))} steps (limit {cfg.max_fan_in})"))
    depth = len(plan.levels())
    if depth > cfg.max_depth:
        out.append(Finding("OD502", "warning", None, f"plan is {depth} levels deep (limit {cfg.max_depth})"))
    consumed = {i for s in plan.steps.values() for i in s.inputs}
    for sid, s in plan.steps.items():
        dead = [o for o in s.outputs if o not in consumed]
        if dead and not s.final and plan.dependents()[sid]:
            out.append(Finding("OD601", "warning", sid, "output never consumed: " + ", ".join(dead)))
    cost = analysis.expected_cost(plan)
    if cfg.max_cost is not None and cost > cfg.max_cost:
        out.append(Finding("OD301", "error", None, f"expected cost {cost:.4f} exceeds {cfg.max_cost}"))
    latency = analysis.makespan(plan, "expected")
    if cfg.max_latency is not None and latency > cfg.max_latency:
        out.append(Finding("OD302", "error", None, f"expected latency {latency:.3f} exceeds {cfg.max_latency}"))
    success = analysis.success_probability(plan)
    if cfg.min_success is not None and success < cfg.min_success:
        out.append(Finding("OD303", "error", None, f"success probability {success:.4f} is below {cfg.min_success}"))
    return out
