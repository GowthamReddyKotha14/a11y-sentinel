from sentinel.scanner import fingerprint, relative_page, scan_page


def test_finds_known_problems_on_homepage(page, base):
    root = f"{base}/v1/index.html"
    rules = {v.rule_id for v in scan_page(page, root, root)}
    assert {"html-has-lang", "image-alt", "color-contrast", "link-name"} <= rules


def test_clean_page_only_has_shared_nav_issue(page, base):
    root = f"{base}/v1/index.html"
    found = scan_page(page, f"{base}/v1/search.html", root)
    assert [v.rule_id for v in found] == ["link-name"]  # the icon-only search link in the nav


def test_fingerprint_is_stable_across_deployments():
    a = relative_page("http://staging.x.edu/site/apply.html", "http://staging.x.edu/site/index.html")
    b = relative_page("https://www.x.edu/site/apply.html", "https://www.x.edu/site/")
    assert a == b == "apply.html"
    assert fingerprint(a, "label", "#email") == fingerprint(b, "label", "#email")
    assert fingerprint(a, "label", "#email") != fingerprint(a, "label", "#phone")
