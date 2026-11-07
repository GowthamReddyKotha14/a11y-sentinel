import pytest
from playwright.sync_api import sync_playwright

from sentinel.devserver import serve


@pytest.fixture(scope="session")
def base():
    with serve() as url:
        yield url


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    p = browser.new_page()
    yield p
    p.close()
