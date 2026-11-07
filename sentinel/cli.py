"""sentinel: command-line entry point.

  sentinel scan   URL [--max-pages N] [--label L] [--json out.json]
  sentinel gate   current.json --baseline baseline.json [--min-impact serious]
  sentinel fix    URL [--rule RULE ...] [--llm] [--out fixes.json]
  sentinel report [--fixes fixes.json] [--out report.html]
  sentinel demo   [--llm]      # end-to-end run against the bundled demo site
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .crawler import crawl
from .diff import compare, gate
from .scanner import Violation, relative_page


def _load(path: str) -> list[Violation]:
    return [Violation(**d) for d in json.loads(Path(path).read_text())["violations"]]


def cmd_scan(a) -> int:
    from .store import Store

    def progress(url, found):
        print(f"  {len(found):>3}  {url}", file=sys.stderr)

    print(f"Scanning {a.url} (max {a.max_pages} pages)", file=sys.stderr)
    r = crawl(a.url, a.max_pages, a.delay, on_page=progress)
    for url, err in r.errors.items():
        print(f"  skip {url}: {err}", file=sys.stderr)
    store = Store(a.db)
    scan_id = store.save_scan(a.url, a.label, [(relative_page(u, a.url), u) for u in r.pages], r.violations)
    store.close()
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"root_url": a.url, "label": a.label, "pages": r.pages,
             "violations": [v.to_dict() for v in r.violations]}, indent=2))
    print(f"Scan #{scan_id}: {len(r.violations)} violations on {len(r.pages)} pages", file=sys.stderr)
    return 0


def cmd_gate(a) -> int:
    d = compare(_load(a.baseline), _load(a.current))
    failing = gate(d, a.min_impact)
    s = d.summary()
    print(f"new {s['new']}, fixed {s['fixed']}, still open {s['open']}")
    for v in failing:
        print(f"::error title=New accessibility violation ({v.impact})::{v.page}: {v.help} [{v.rule_id}] at {v.target}")
    if failing:
        print(f"Gate FAILED: {len(failing)} new {a.min_impact}-or-worse violation(s).")
        return 1
    print("Gate passed.")
    return 0


def cmd_fix(a) -> int:
    from .fixer import fix_page, make_proposer
    results = fix_page(a.url, a.root or a.url, make_proposer(a.llm),
                       set(a.rule) if a.rule else None, a.limit)
    for r in results:
        mark = "OK " if r.accepted else "-- "
        print(f"{mark}{r.page} {r.rule_id:<16} {r.reason}")
    if a.out:
        existing = json.loads(Path(a.out).read_text()) if Path(a.out).exists() and a.append else []
        Path(a.out).write_text(json.dumps(existing + [asdict(r) for r in results], indent=2))
    return 0


def cmd_report(a) -> int:
    from .report import build
    from .store import Store
    fixes = json.loads(Path(a.fixes).read_text()) if a.fixes else None
    store = Store(a.db)
    Path(a.out).write_text(build(store, fixes, a.min_impact), encoding="utf-8")
    store.close()
    print(f"Wrote {a.out}")
    return 0


def cmd_demo(a) -> int:
    """Scan v1 (baseline) and v2 (with regressions) of the demo site, gate,
    propose+verify fixes on v2, and render the report."""
    from .devserver import serve
    from .fixer import fix_page, make_proposer
    from .report import build
    from .store import Store

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db = out / "demo.duckdb"
    db.unlink(missing_ok=True)
    store = Store(str(db))
    with serve() as base:
        scans = {}
        for label in ("v1", "v2"):
            root = f"{base}/{label}/index.html"
            r = crawl(root, delay=0)
            store.save_scan(root, label, [(relative_page(u, root), u) for u in r.pages], r.violations)
            scans[label] = r.violations
            (out / f"{label}.json").write_text(json.dumps(
                {"root_url": root, "label": label, "pages": r.pages,
                 "violations": [v.to_dict() for v in r.violations]}, indent=2))
            print(f"{label}: {len(r.violations)} violations on {len(r.pages)} pages")
        d = compare(scans["v1"], scans["v2"])
        blocking = gate(d)
        print(f"diff v1 -> v2: {d.summary()}; gate would {'FAIL' if blocking else 'pass'} "
              f"({len(blocking)} new serious+)")
        proposer = make_proposer(a.llm)
        root = f"{base}/v2/index.html"
        fixes = []
        for page in sorted({v.page for v in scans["v2"]}):
            fixes += [asdict(f) for f in fix_page(f"{base}/v2/{page}", root, proposer)]
        (out / "fixes.json").write_text(json.dumps(fixes, indent=2))
        ok = sum(f["accepted"] for f in fixes)
        print(f"fixes: {ok}/{len(fixes)} verified")
    (out / "report.html").write_text(build(store, fixes), encoding="utf-8")
    store.close()
    print(f"Report: {out / 'report.html'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sentinel", description="WCAG 2.1 AA regression scanner with verified fixes")
    p.add_argument("--db", default="sentinel.duckdb", help="DuckDB file for scan history")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="crawl and scan a site")
    s.add_argument("url")
    s.add_argument("--max-pages", type=int, default=20)
    s.add_argument("--delay", type=float, default=1.0, help="seconds between page loads (be polite)")
    s.add_argument("--label", default="scan")
    s.add_argument("--json", help="also write results to this JSON file")
    s.set_defaults(func=cmd_scan)

    g = sub.add_parser("gate", help="fail if a scan introduced new violations")
    g.add_argument("current")
    g.add_argument("--baseline", required=True)
    g.add_argument("--min-impact", default="serious", choices=["minor", "moderate", "serious", "critical"])
    g.set_defaults(func=cmd_gate)

    f = sub.add_parser("fix", help="propose and verify fixes for one page")
    f.add_argument("url")
    f.add_argument("--root", help="scan root, for consistent page names")
    f.add_argument("--rule", action="append", help="only these axe rule ids (repeatable)")
    f.add_argument("--llm", action="store_true", help="use Claude (needs ANTHROPIC_API_KEY)")
    f.add_argument("--limit", type=int, default=25)
    f.add_argument("--out", help="write results JSON")
    f.add_argument("--append", action="store_true", help="append to --out instead of replacing")
    f.set_defaults(func=cmd_fix)

    r = sub.add_parser("report", help="render the HTML report")
    r.add_argument("--fixes")
    r.add_argument("--out", default="report.html")
    r.add_argument("--min-impact", default="serious", choices=["minor", "moderate", "serious", "critical"])
    r.set_defaults(func=cmd_report)

    d = sub.add_parser("demo", help="end-to-end demo on the bundled site")
    d.add_argument("--llm", action="store_true")
    d.add_argument("--out-dir", default="demo_output")
    d.set_defaults(func=cmd_demo)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
