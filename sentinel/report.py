"""Render a single self-contained HTML report from scan history and fix results.

The report is held to the same standard it measures: the test suite scans
the generated report with axe and fails if it has any WCAG 2.1 AA violations.
"""
from __future__ import annotations

from collections import Counter
from importlib import resources

from jinja2 import Environment, select_autoescape

from .diff import compare, gate
from .scanner import IMPACT_RANK
from .store import Store


def _env() -> Environment:
    return Environment(autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


def build(store: Store, fixes: list[dict] | None = None, min_impact: str = "serious") -> str:
    scans = store.scans()
    if not scans:
        raise SystemExit("No scans in the database yet. Run `sentinel scan <url>` first.")
    latest = scans[-1]
    current = store.violations(latest["scan_id"])
    previous = store.violations(scans[-2]["scan_id"]) if len(scans) > 1 else None
    diff = compare(previous, current) if previous is not None else None
    blocking = gate(diff, min_impact) if diff else []

    by_rule = Counter((v.rule_id, v.help, v.help_url) for v in current)
    worst = {}
    for v in current:
        if IMPACT_RANK[v.impact] > IMPACT_RANK.get(worst.get(v.rule_id, "minor"), 0) or v.rule_id not in worst:
            worst[v.rule_id] = v.impact
    rules = sorted(
        ({"rule_id": r, "help": h, "help_url": u, "count": c, "impact": worst[r]} for (r, h, u), c in by_rule.items()),
        key=lambda x: (-IMPACT_RANK[x["impact"]], -x["count"], x["rule_id"]),
    )
    pages = Counter(v.page for v in current)
    page_rows = sorted(({"page": p, "count": pages.get(p, 0)} for p in store.pages(latest["scan_id"])),
                       key=lambda x: (-x["count"], x["page"]))
    max_rule = max((r["count"] for r in rules), default=1)
    max_scan = max((s["violations"] for s in scans), default=1) or 1

    fixes = fixes or []
    tpl = _env().from_string(resources.files("sentinel.templates").joinpath("report.html").read_text())
    return tpl.render(
        latest=latest, scans=scans, max_scan=max_scan, diff=diff, blocking=blocking,
        min_impact=min_impact, rules=rules, max_rule=max_rule, pages=page_rows,
        fixes=fixes, accepted=sum(1 for f in fixes if f["accepted"]),
        current_count=len(current),
    )
