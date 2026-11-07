from sentinel.crawler import crawl


def test_crawl_stays_in_scope_and_obeys_robots(base):
    r = crawl(f"{base}/v1/index.html", delay=0)
    pages = sorted(u.rsplit("/", 1)[1] for u in r.pages)
    assert pages == ["admissions.html", "apply.html", "events.html", "index.html", "search.html"]
    assert all(u.startswith(base) for u in r.pages)          # never left the site
    assert any("private/staff.html" in u and "robots" in e for u, e in r.errors.items())


def test_max_pages_is_respected(base):
    assert len(crawl(f"{base}/v1/index.html", max_pages=2, delay=0).pages) == 2
