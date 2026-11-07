"""A polite, same-origin breadth-first crawler.

Scanning someone else's website is a privilege, not a right. This crawler:
  * stays on the starting origin (never wanders onto third-party sites),
  * obeys robots.txt,
  * waits `delay` seconds between page loads,
  * stops at `max_pages`.
"""
from __future__ import annotations

import time
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urldefrag, urlparse

from playwright.sync_api import sync_playwright

from .scanner import Violation, scan_page

USER_AGENT = "A11ySentinel/0.1 (+https://github.com/GowthamReddyKotha14/a11y-sentinel)"
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp4", ".mp3",
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".css", ".js", ".ico")


@dataclass
class CrawlResult:
    root_url: str
    pages: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _in_scope(url: str, root_url: str) -> bool:
    """Same origin AND under the root's directory, so scanning
    https://x.edu/admissions/ doesn't crawl all of https://x.edu/."""
    if not _same_origin(url, root_url):
        return False
    root_path = urlparse(root_url).path
    root_dir = root_path if root_path.endswith("/") else root_path.rsplit("/", 1)[0] + "/"
    return urlparse(url).path.startswith(root_dir)


def _robots(root_url: str) -> urllib.robotparser.RobotFileParser:
    p = urlparse(root_url)
    rp = urllib.robotparser.RobotFileParser(f"{p.scheme}://{p.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:  # unreachable robots.txt -> treat as allow-all (RFC 9309 §2.3.1.3)
        rp.parse([])
    return rp


def crawl(root_url: str, max_pages: int = 20, delay: float = 0.5,
          on_page=None) -> CrawlResult:
    result = CrawlResult(root_url=root_url)
    robots = _robots(root_url)
    queue, seen = deque([root_url]), {root_url}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(user_agent=USER_AGENT).new_page()
        while queue and len(result.pages) < max_pages:
            url = queue.popleft()
            if not robots.can_fetch(USER_AGENT, url):
                result.errors[url] = "disallowed by robots.txt"
                continue
            try:
                found = scan_page(page, url, root_url)
            except Exception as e:  # keep crawling; record the failure
                result.errors[url] = str(e).splitlines()[0]
                continue
            result.pages.append(url)
            result.violations.extend(found)
            if on_page:
                on_page(url, found)

            for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"):
                nxt = urldefrag(urljoin(url, href))[0]
                if (nxt not in seen and nxt.startswith(("http://", "https://"))
                        and _in_scope(nxt, root_url)
                        and not nxt.lower().endswith(SKIP_EXT)):
                    seen.add(nxt)
                    queue.append(nxt)
            if queue and delay:
                time.sleep(delay)
        browser.close()
    return result
