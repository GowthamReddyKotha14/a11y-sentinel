import json
import pathlib

from playwright.sync_api import sync_playwright

from sentinel.cli import main
from sentinel.scanner import run_axe


def test_demo_end_to_end_and_report_passes_its_own_scan(tmp_path):
    assert main(["demo", "--out-dir", str(tmp_path)]) == 0
    report = tmp_path / "report.html"
    html = report.read_text()
    assert "Gate failed" in html and "2</b> new problems" in html
    fixes = json.loads((tmp_path / "fixes.json").read_text())
    assert sum(f["accepted"] for f in fixes) >= 10
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for scheme in ("light", "dark"):
            page = browser.new_page(color_scheme=scheme)
            page.goto(pathlib.Path(report).as_uri())
            assert run_axe(page) == [], f"report has accessibility violations in {scheme} mode"
        browser.close()


def test_gate_cli_exit_codes(tmp_path):
    main(["demo", "--out-dir", str(tmp_path)])
    v1, v2 = str(tmp_path / "v1.json"), str(tmp_path / "v2.json")
    assert main(["gate", v2, "--baseline", v1]) == 1   # regressions -> fail
    assert main(["gate", v1, "--baseline", v1]) == 0   # no change -> pass
    data = json.loads((tmp_path / "v1.json").read_text())
    data["violations"] = data["violations"][3:]             # same site, three problems fixed
    improved = tmp_path / "improved.json"
    improved.write_text(json.dumps(data))
    assert main(["gate", str(improved), "--baseline", v1]) == 0   # only improvements -> pass
    assert main(["gate", v1, "--baseline", v2]) == 1   # reverting v2's fixes is a regression too
