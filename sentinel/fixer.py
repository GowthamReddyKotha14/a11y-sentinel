"""Propose fixes for violations, then *prove* each fix before accepting it.

Architecture: proposer -> verifier -> (feedback -> proposer) -> accept/reject

The proposer can be a deterministic rule table or an LLM. Neither is trusted.
Every proposal is applied to the live DOM in a fresh browser page and axe is
re-run on the whole page. A fix is accepted only if ALL of these hold:

  1. the original violation's rule now fires on fewer elements,
  2. no violation of any *other* rule appeared (no fixing one thing by
     breaking another),
  3. the patched element still exists with the same tag and keeps its visible
     text (an LLM can "fix" a missing alt by deleting the <img>, which passes
     axe but destroys content; this check catches that).

If an LLM proposal fails, the verifier's reason is sent back for one retry,
so the model learns in-context what "safe" means for this page.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import PurePosixPath
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from .scanner import Violation, normalize, run_axe, relative_page

DEFAULT_MODEL = os.environ.get("SENTINEL_MODEL", "claude-haiku-5-5")


@dataclass
class Proposal:
    kind: str                          # "attrs" (set attributes) or "html" (replace element)
    attrs: dict = field(default_factory=dict)
    html: str = ""
    rationale: str = ""
    needs_review: bool = True          # a human must confirm the *meaning* (e.g. alt text)
    source: str = "rules"              # "rules" or "llm"


@dataclass
class FixResult:
    page: str
    rule_id: str
    impact: str
    target: str
    before_html: str
    proposal: dict | None
    accepted: bool
    reason: str
    attempts: int = 1


# ---------------------------------------------------------------- utilities

def _humanize(s: str) -> str:
    s = PurePosixPath(urlparse(s).path).stem if "/" in s or "." in s else s
    s = re.sub(r"[-_]+", " ", s)
    s = re.sub(r"\b(img|image|icon|fa|btn|photo|pic)\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:1].upper() + s[1:] if s else ""


ELEMENT_INFO_JS = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const attrs = {};
  for (const a of el.attributes) attrs[a.name] = a.value;
  const cs = getComputedStyle(el);
  const fig = el.closest('figure');
  return {
    tag: el.tagName.toLowerCase(), attrs,
    text: (el.textContent || '').trim(),
    outer: el.outerHTML.slice(0, 1500),
    parent: el.parentElement ? el.parentElement.outerHTML.slice(0, 1500) : '',
    figcaption: fig && fig.querySelector('figcaption') ? fig.querySelector('figcaption').textContent.trim() : '',
    style: {color: cs.color, background: cs.backgroundColor, fontSize: cs.fontSize, fontWeight: cs.fontWeight},
    classes: el.className && typeof el.className === 'string' ? el.className : ''
  };
}"""


# ---------------------------------------------------------------- proposers

class RuleProposer:
    """Deterministic fixes for the most common, mechanical failures.
    Fast, free, and explainable, but it can't judge meaning, so most
    proposals are flagged needs_review."""

    def propose(self, v: Violation, info: dict, feedback: str | None = None) -> Proposal | None:
        a = info["attrs"]
        if v.rule_id == "html-has-lang":
            return Proposal("attrs", {"lang": "en"},
                            rationale="Declare the page language so screen readers pick the right voice.",
                            needs_review=True)
        if v.rule_id == "image-alt":
            guess = info.get("figcaption") or a.get("title") or _humanize(a.get("src", ""))
            if not guess:
                return None
            return Proposal("attrs", {"alt": guess},
                            rationale="Placeholder alt text from caption or file name. A person must "
                                      "describe what the image actually shows.")
        if v.rule_id in ("label", "select-name"):
            guess = a.get("placeholder") or _humanize(a.get("name", "") or a.get("id", ""))
            if not guess:
                return None
            return Proposal("attrs", {"aria-label": guess},
                            rationale="Gives the field an accessible name. Better long-term fix: a visible <label>.")
        if v.rule_id in ("button-name", "link-name"):
            guess = a.get("title") or _humanize(
                next((c for c in info.get("classes", "").split() if "icon" in c or "fa-" in c), "")
            ) or (_humanize(a.get("href", "")) if v.rule_id == "link-name" else "")
            if not guess:
                return None
            return Proposal("attrs", {"aria-label": guess},
                            rationale="Icon-only control had no accessible name.")
        return None  # e.g. color-contrast needs design judgement -> LLM


class LLMProposer:
    """Uses Claude to propose a patch. Called only when an API key is set."""

    SYSTEM = (
        "You fix WCAG 2.1 AA accessibility violations in HTML. Reply with ONLY a JSON object, "
        "no prose and no code fences, with keys: kind ('attrs' or 'html'), attrs (object of "
        "attribute->value to set, when kind='attrs'), html (full replacement outerHTML for the "
        "element, when kind='html'), rationale (one sentence), needs_review (true if a human must "
        "confirm meaning, e.g. image descriptions). Rules: keep the same element tag; never "
        "delete or hide content; keep all existing visible text; prefer the smallest change; "
        "for colour contrast, change only colours via an inline style attribute and target a "
        "contrast ratio of at least 4.5:1 for normal text."
    )

    def __init__(self, model: str = DEFAULT_MODEL):
        import anthropic  # optional dependency
        self.client = anthropic.Anthropic()
        self.model = model

    def propose(self, v: Violation, info: dict, feedback: str | None = None) -> Proposal | None:
        prompt = {
            "rule": v.rule_id, "help": v.help, "wcag": v.wcag, "impact": v.impact,
            "element": info["outer"], "parent_context": info["parent"],
            "computed_style": info["style"],
        }
        if feedback:
            prompt["previous_attempt_rejected_because"] = feedback
        msg = self.client.messages.create(
            model=self.model, max_tokens=800, system=self.SYSTEM,
            messages=[{"role": "user", "content": json.dumps(prompt)}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            return None
        if d.get("kind") not in ("attrs", "html"):
            return None
        return Proposal(d["kind"], d.get("attrs") or {}, d.get("html") or "",
                        d.get("rationale", ""), bool(d.get("needs_review", True)), "llm")


class ChainProposer:
    """Try cheap deterministic rules first, fall back to the LLM."""

    def __init__(self, *proposers):
        self.proposers = [p for p in proposers if p]
        self.has_llm = any(isinstance(p, LLMProposer) for p in self.proposers)

    def propose(self, v, info, feedback=None):
        for p in self.proposers:
            if feedback and isinstance(p, RuleProposer):
                continue  # rules are deterministic; retrying them is pointless
            prop = p.propose(v, info, feedback)
            if prop:
                return prop
        return None


# ---------------------------------------------------------------- verifier

APPLY_JS = """([sel, kind, attrs, html]) => {
  const el = document.querySelector(sel);
  if (!el) return 'element not found';
  if (kind === 'attrs') {
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return 'ok';
  }
  if (el === document.documentElement || el === document.body)
    return 'refusing to replace <html> or <body>';
  const tpl = document.createElement('template');
  tpl.innerHTML = html.trim();
  const repl = tpl.content.firstElementChild;
  if (!repl || tpl.content.childElementCount !== 1) return 'replacement must be exactly one element';
  if (repl.tagName !== el.tagName) return `tag changed from <${el.tagName.toLowerCase()}> to <${repl.tagName.toLowerCase()}>`;
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  if (!norm(repl.textContent).includes(norm(el.textContent))) return 'replacement removed visible text';
  el.replaceWith(repl);
  return 'ok';
}"""


def verify(page, url: str, root_url: str, v: Violation, prop: Proposal) -> tuple[bool, str]:
    page.goto(url, wait_until="networkidle")
    before = normalize(run_axe(page), relative_page(url, root_url))
    status = page.evaluate(APPLY_JS, [v.target, prop.kind, prop.attrs, prop.html])
    if status != "ok":
        return False, f"patch not applied: {status}"
    after = normalize(run_axe(page), relative_page(url, root_url))

    rule_before = sum(1 for x in before if x.rule_id == v.rule_id)
    rule_after = sum(1 for x in after if x.rule_id == v.rule_id)
    if rule_after >= rule_before:
        return False, f"'{v.rule_id}' still fails ({rule_before} -> {rule_after} elements)"

    other_before = {(x.rule_id, x.target) for x in before if x.rule_id != v.rule_id}
    introduced = {(x.rule_id, x.target) for x in after if x.rule_id != v.rule_id} - other_before
    if introduced:
        rules = ", ".join(sorted({r for r, _ in introduced}))
        return False, f"fix introduced new violations: {rules}"
    return True, f"'{v.rule_id}' {rule_before} -> {rule_after} elements, nothing else broke"


# ---------------------------------------------------------------- driver

def fix_page(url: str, root_url: str, proposer, rules: set[str] | None = None,
             limit: int = 25, max_attempts: int = 2) -> list[FixResult]:
    results: list[FixResult] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        found = normalize(run_axe(page), relative_page(url, root_url))
        for v in found[:limit]:
            if rules and v.rule_id not in rules:
                continue
            if ">>>" in v.target:  # inside an iframe / shadow root: out of scope for v0.1
                continue
            page.goto(url, wait_until="networkidle")
            info = page.evaluate(ELEMENT_INFO_JS, v.target)
            if not info:
                continue
            no_prop = ("no proposal from the LLM" if getattr(proposer, "has_llm", True)
                       else "needs judgement beyond the rule table; run with --llm or fix by hand")
            feedback, prop, ok, reason, attempt = None, None, False, no_prop, 0
            for attempt in range(1, max_attempts + 1):
                prop = proposer.propose(v, info, feedback)
                if not prop:
                    reason = no_prop if attempt == 1 else reason
                    break
                ok, reason = verify(page, url, root_url, v, prop)
                if ok:
                    break
                feedback = reason
            results.append(FixResult(v.page, v.rule_id, v.impact, v.target, info["outer"][:500],
                                     asdict(prop) if prop else None, ok, reason, max(attempt, 1)))
        browser.close()
    return results


def make_proposer(use_llm: bool):
    llm = None
    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        llm = LLMProposer()
    return ChainProposer(RuleProposer(), llm)
