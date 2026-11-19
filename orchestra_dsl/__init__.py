"""orchestra.dsl: write an AI workflow as a small language, then check it before it runs.

Parse and print plans, validate dataflow, find the critical path and slack, cost the plan under failure and retries,
schedule it on a fixed number of workers, lint it against approval and risk policy, diff two versions for review, and
pin a plan by digest.
"""
from __future__ import annotations

from .analysis import (
                       Schedule,
                       Slot,
                       Timing,
                       cpm,
                       critical_steps,
                       expected_cost,
                       leverage,
                       makespan,
                       max_parallelism,
                       run_probability,
                       schedule,
                       success_probability,
                       summary,
                       worst_cost,
)
from .diff import PlanDiff, diff
from .lang import ParseError, dumps, loads
from .lint import Finding, LintConfig, gated, lint
from .model import Plan, SpecError, StepSpec, load
from .render import to_dot, to_mermaid

__version__ = "0.2.0"

__all__ = [
                       "Finding",
                       "LintConfig",
                       "ParseError",
                       "Plan",
                       "PlanDiff",
                       "Schedule",
                       "Slot",
                       "SpecError",
                       "StepSpec",
                       "Timing",
                       "__version__",
                       "cpm",
                       "critical_steps",
                       "diff",
                       "dumps",
                       "expected_cost",
                       "gated",
                       "leverage",
                       "lint",
                       "load",
                       "loads",
                       "makespan",
                       "max_parallelism",
                       "run_probability",
                       "schedule",
                       "success_probability",
                       "summary",
                       "to_dot",
                       "to_mermaid",
                       "worst_cost",
]
