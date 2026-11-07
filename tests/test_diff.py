from sentinel.crawler import crawl
from sentinel.diff import compare, gate
from sentinel.scanner import Violation


def V(page, rule, target, impact="serious"):
    return Violation(page, rule, impact, "", "", "", target, "")


def test_compare_classifies_new_fixed_open():
    base = [V("a.html", "label", "#x"), V("a.html", "image-alt", "img")]
    cur = [V("a.html", "label", "#x"), V("b.html", "button-name", ".chat", "critical")]
    d = compare(base, cur)
    assert d.summary() == {"new": 1, "fixed": 1, "open": 1}


def test_gate_only_fails_on_new_violations_at_or_above_threshold():
    d = compare([], [V("a", "r1", "t1", "minor"), V("a", "r2", "t2", "critical")])
    assert [v.rule_id for v in gate(d, "serious")] == ["r2"]
    assert len(gate(d, "minor")) == 2


def test_demo_regressions_are_caught(base):
    v1 = crawl(f"{base}/v1/index.html", delay=0).violations
    v2 = crawl(f"{base}/v2/index.html", delay=0).violations
    d = compare(v1, v2)
    assert sorted((v.page, v.rule_id) for v in d.new) == [("apply.html", "button-name"), ("events.html", "image-alt")]
    assert sorted((v.page, v.rule_id) for v in d.fixed) == [("index.html", "html-has-lang"), ("index.html", "image-alt")]
    assert len(gate(d, "serious")) == 2
