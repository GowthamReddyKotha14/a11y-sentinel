"""Compare two scans: what's new, what's fixed, what's still open.

The CI gate fails only on *new* violations at or above a chosen impact.
Design decision: a gate that fails on *every* existing violation is ignored
or switched off within a week on any real site with a backlog. Failing only
on regressions ("don't make it worse") is enforceable from day one, while the
backlog is burned down separately and tracked on the report.
"""
from __future__ import annotations

from dataclasses import dataclass

from .scanner import IMPACT_RANK, Violation


@dataclass
class Diff:
    new: list[Violation]
    fixed: list[Violation]
    open: list[Violation]

    def summary(self) -> dict:
        return {"new": len(self.new), "fixed": len(self.fixed), "open": len(self.open)}


def compare(baseline: list[Violation], current: list[Violation]) -> Diff:
    base = {v.fingerprint: v for v in baseline}
    cur = {v.fingerprint: v for v in current}
    return Diff(
        new=[v for f, v in cur.items() if f not in base],
        fixed=[v for f, v in base.items() if f not in cur],
        open=[v for f, v in cur.items() if f in base],
    )


def gate(diff: Diff, min_impact: str = "serious") -> list[Violation]:
    """Return the new violations that should fail the build."""
    floor = IMPACT_RANK[min_impact]
    return [v for v in diff.new if IMPACT_RANK.get(v.impact, 0) >= floor]
