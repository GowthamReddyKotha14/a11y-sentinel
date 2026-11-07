from sentinel.fixer import ChainProposer, Proposal, RuleProposer, fix_page, verify
from sentinel.scanner import scan_page


def _one(page, base, path, rule):
    root = f"{base}/v1/index.html"
    return next(v for v in scan_page(page, f"{base}/v1/{path}", root) if v.rule_id == rule)


def test_rule_fixes_are_verified(base):
    root = f"{base}/v1/index.html"
    results = {r.rule_id: r for r in fix_page(f"{base}/v1/apply.html", root, ChainProposer(RuleProposer()))}
    assert results["label"].accepted and results["label"].proposal["attrs"] == {"aria-label": "Email address"}
    assert results["select-name"].accepted


def test_verifier_rejects_a_non_fix(page, base):
    v = _one(page, base, "index.html", "image-alt")
    ok, why = verify(page, f"{base}/v1/index.html", f"{base}/v1/index.html", v,
                     Proposal("attrs", {"class": "hero decorative"}))
    assert not ok and "still fails" in why


def test_verifier_rejects_deleting_the_element(page, base):
    """An LLM can 'fix' a missing alt by swapping the <img> for a <span>.
    axe would be happy; the content is gone. The verifier must refuse."""
    v = _one(page, base, "index.html", "image-alt")
    ok, why = verify(page, f"{base}/v1/index.html", f"{base}/v1/index.html", v,
                     Proposal("html", html="<span></span>"))
    assert not ok and "tag changed" in why


def test_verifier_rejects_removing_visible_text(page, base):
    v = _one(page, base, "index.html", "color-contrast")
    ok, why = verify(page, f"{base}/v1/index.html", f"{base}/v1/index.html", v,
                     Proposal("html", html='<p class="tagline" style="color:#222">A university.</p>'))
    assert not ok and "removed visible text" in why


def test_verifier_rejects_fix_that_breaks_something_else(page, base):
    v = _one(page, base, "index.html", "color-contrast")
    bad = ('<p class="tagline" style="color:#222">A public university in northwest Ohio since 1910.'
           ' <a href="x.html"></a></p>')  # fixes contrast, adds an empty link
    ok, why = verify(page, f"{base}/v1/index.html", f"{base}/v1/index.html", v, Proposal("html", html=bad))
    assert not ok and "link-name" in why


class ScriptedLLM:
    """Stands in for Claude: first proposes a contrast fix that's still too
    light, then (after verifier feedback) one that passes."""
    has_llm = True

    def __init__(self):
        self.calls = []

    def propose(self, v, info, feedback=None):
        self.calls.append(feedback)
        colour = "#999" if feedback is None else "#333"
        return Proposal("attrs", {"style": f"color:{colour}"}, source="llm", needs_review=False)


def test_feedback_loop_retries_with_verifier_reason(base):
    root = f"{base}/v1/index.html"
    llm = ScriptedLLM()
    [r] = fix_page(f"{base}/v1/index.html", root, llm, rules={"color-contrast"})
    assert r.accepted and r.attempts == 2
    assert llm.calls[0] is None and "still fails" in llm.calls[1]
