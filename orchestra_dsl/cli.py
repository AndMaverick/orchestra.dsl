"""``python -m orchestra_dsl``: check, analyse, format, draw, diff and pin plans. Reads .odl text or .json. Local only."""
from __future__ import annotations

import argparse
import json
import sys

from . import analysis, lang, render
from .diff import diff as diff_plans
from .lint import LintConfig, lint
from .model import Plan, SpecError, load


def read_plan(path: str) -> Plan:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return load(json.loads(text)) if path.endswith(".json") else lang.loads(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orchestra_dsl", description="Check an AI workflow before it runs.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "analyze", "fmt", "graph", "digest", "schedule"):
        sub.add_parser(name).add_argument("file")
    c = sub.choices["check"]
    c.add_argument("--max-cost", type=float)
    c.add_argument("--max-latency", type=float)
    c.add_argument("--min-success", type=float)
    c.add_argument("--approval-risk", type=int, default=3)
    c.add_argument("--models", help="comma-separated allowed models")
    c.add_argument("--strict", action="store_true", help="warnings fail the check too")
    a = sub.choices["analyze"]
    a.add_argument("--mode", choices=["nominal", "expected", "worst"], default="nominal")
    a.add_argument("--json", action="store_true")
    sub.choices["graph"].add_argument("--format", choices=["mermaid", "dot"], default="mermaid")
    sch = sub.choices["schedule"]
    sch.add_argument("--workers", type=int, default=2)
    sch.add_argument("--mode", choices=["nominal", "expected", "worst"], default="nominal")
    d = sub.add_parser("diff")
    d.add_argument("old")
    d.add_argument("new")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "diff":
            result = diff_plans(read_plan(args.old), read_plan(args.new))
            print(result.render())
            return 1 if result.flags else 0
        plan = read_plan(args.file)
    except (OSError, SpecError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "check":
        cfg = LintConfig(args.approval_risk, args.max_cost, args.max_latency, args.min_success,
                              tuple(args.models.split(",")) if args.models else None)
        findings = lint(plan, cfg)
        for f in findings:
            print(f)
        bad = [f for f in findings if f.severity == "error" or (args.strict and f.severity == "warning")]
        print(f"{len(plan.steps)} steps, {len(findings)} finding(s), digest {plan.digest()[:16]}")
        return 1 if bad else 0
    if args.cmd == "analyze":
        data = analysis.summary(plan, args.mode)
        if args.json:
            print(json.dumps(data, indent=2))
            return 0
        print(f"{data['name'] or 'plan'}  {data['steps']} steps  max parallelism {data['max_parallelism']}  digest {data['digest'][:16]}")
        for k in ("latency", "cost"):
            v = data[k]
            print(f"{k:<8} nominal {v['nominal']:.4g}   expected {v['expected']:.4g}   worst {v['worst']:.4g}")
        print(f"success probability {data['success_probability']:.4f}")
        print("approvals:", ", ".join(data["approvals"]) or "none")
        print("critical:", " -> ".join(data["critical_steps"]))
        print("leverage (time saved if the step were instant):")
        for sid, v in list(data["leverage"].items())[:8]:
            print(f"  {sid:<20} {v:.4g}")
        return 0
    if args.cmd == "fmt":
        sys.stdout.write(lang.dumps(plan))
    elif args.cmd == "graph":
        sys.stdout.write(render.to_mermaid(plan) if args.format == "mermaid" else render.to_dot(plan))
    elif args.cmd == "digest":
        print(plan.digest())
    else:
        sched = analysis.schedule(plan, args.workers, args.mode)
        for s in sched.slots:
            print(f"worker {s.worker}  {s.start:>8.4g} -> {s.end:<8.4g} {s.step}")
        print(f"makespan {sched.makespan:.4g} on {sched.workers} worker(s), utilisation {sched.utilization:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
