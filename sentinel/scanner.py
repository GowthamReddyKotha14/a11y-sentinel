"""Run axe-core inside a real Chromium page and normalize the results.

Why a real browser instead of parsing HTML? Many WCAG failures (colour
contrast, focus order, hidden content, ARIA computed names) only exist after
CSS and JavaScript have run. axe-core needs a live DOM to judge them.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from importlib import resources
from urllib.parse import urlparse

# WCAG 2.1 Level A + AA is the technical standard named in the DOJ ADA Title II
# rule, so that is exactly what we test against. axe's "best-practice" rules
# are deliberately excluded: they're useful, but they aren't legal
# requirements, and mixing them in inflates the numbers.
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]

IMPACT_RANK = {"minor": 1, "moderate": 2, "serious": 3, "critical": 4}


def axe_source() -> str:
    return resources.files("sentinel.vendor").joinpath("axe.min.js").read_text(encoding="utf-8")


@dataclass
class Violation:
    page: str          # path relative to the scan root, e.g. "admissions.html"
    rule_id: str       # axe rule, e.g. "image-alt"
    impact: str        # minor | moderate | serious | critical
    help: str          # one-line description from axe
    help_url: str
    wcag: str          # comma-separated WCAG tags, e.g. "wcag2a,wcag111"
    target: str        # CSS selector axe used to locate the element
    html: str          # offending element's outer HTML (truncated)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = fingerprint(self.page, self.rule_id, self.target)

    def to_dict(self) -> dict:
        return asdict(self)


def fingerprint(page: str, rule_id: str, target: str) -> str:
    """Stable identity for a violation across scans.

    Design decision: identity = (page path, rule, selector). We deliberately do
    NOT include the element's HTML, because copy edits (changing a heading's
    text) would make every violation look "new". The trade-off: if a
    developer restructures the DOM so the selector changes, an old violation
    shows up as one fixed + one new. That's noisy but safe — a regression
    gate should over-report, never silently swallow a problem.
    """
    raw = f"{page}|{rule_id}|{target}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def relative_page(url: str, root_url: str) -> str:
    """Express a URL relative to the scan root so two deployments
    (e.g. staging vs prod, or /v1/ vs /v2/) produce comparable fingerprints."""
    u, r = urlparse(url), urlparse(root_url)
    root_path = r.path if r.path.endswith("/") else r.path.rsplit("/", 1)[0] + "/"
    path = u.path
    if path.startswith(root_path):
        path = path[len(root_path):]
    return path or "index.html"


def run_axe(page, context: str | None = None) -> list[dict]:
    """Inject axe (if needed) and run it on the current page. Returns raw axe
    violation objects. `context` optionally limits the run to a selector."""
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.add_script_tag(content=axe_source())
    return page.evaluate(
        """async ([tags, ctx]) => {
            const r = await axe.run(ctx ? ctx : document,
                {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']});
            return r.violations;
        }""",
        [WCAG_TAGS, context],
    )


def normalize(raw: list[dict], page_rel: str) -> list[Violation]:
    out: list[Violation] = []
    for v in raw:
        wcag = ",".join(t for t in v.get("tags", []) if t.startswith("wcag"))
        for node in v.get("nodes", []):
            target = " ".join(
                t if isinstance(t, str) else " >>> ".join(t) for t in node.get("target", [])
            )
            out.append(
                Violation(
                    page=page_rel,
                    rule_id=v["id"],
                    impact=node.get("impact") or v.get("impact") or "minor",
                    help=v.get("help", ""),
                    help_url=v.get("helpUrl", ""),
                    wcag=wcag,
                    target=target,
                    html=(node.get("html") or "")[:500],
                )
            )
    return out


def scan_page(page, url: str, root_url: str) -> list[Violation]:
    page.goto(url, wait_until="networkidle")
    return normalize(run_axe(page), relative_page(url, root_url))
