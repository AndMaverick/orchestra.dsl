"""Draw a plan: Mermaid (renders in GitHub markdown) and Graphviz DOT. Critical steps and approval gates stand out."""
from __future__ import annotations

from . import analysis
from .model import Plan


def _label(plan: Plan, sid: str) -> str:
    s = plan.steps[sid]
    parts = [sid]
    if s.model:
        parts.append(s.model)
    if s.cost:
        parts.append(f"${s.cost:g}")
    if s.latency:
        parts.append(f"{s.latency:g}s")
    return " | ".join(parts).replace('"', "'")


def to_mermaid(plan: Plan) -> str:
    crit = set(analysis.critical_steps(plan))
    ids = {sid: f"n{i}" for i, sid in enumerate(plan.steps)}
    lines = ["flowchart TD"]
    for sid, s in plan.steps.items():
        node = ids[sid]
        lines.append(f'  {node}{{{{"{_label(plan, sid)}"}}}}' if s.approval else f'  {node}["{_label(plan, sid)}"]')
    for sid, s in plan.steps.items():
        for d in s.depends_on:
            lines.append(f"  {ids[d]} --> {ids[sid]}")
    lines.append("  classDef critical stroke:#d62728,stroke-width:3px")
    lines.append("  classDef gate fill:#fff3cd,stroke:#b8860b")
    for sid in plan.steps:
        if sid in crit:
            lines.append(f"  class {ids[sid]} critical")
        if plan.steps[sid].approval:
            lines.append(f"  class {ids[sid]} gate")
    return "\n".join(lines) + "\n"


def to_dot(plan: Plan) -> str:
    crit = set(analysis.critical_steps(plan))
    lines = ["digraph plan {", "  rankdir=TB;", "  node [shape=box, fontname=Helvetica];"]
    for sid, s in plan.steps.items():
        attrs = [f'label="{_label(plan, sid)}"']
        if s.approval:
            attrs += ["shape=hexagon", "style=filled", "fillcolor=lightgoldenrod1"]
        if sid in crit:
            attrs += ["color=red", "penwidth=2"]
        lines.append(f'  "{sid}" [{", ".join(attrs)}];')
    for sid, s in plan.steps.items():
        for d in s.depends_on:
            lines.append(f'  "{d}" -> "{sid}";')
    lines.append("}")
    return "\n".join(lines) + "\n"
