"""Scan history in DuckDB.

Why DuckDB? It's an embedded analytical database: one file, no server, and
real SQL with window functions for trend queries. For a tool that runs on a
laptop or in a CI job, standing up Postgres would be pure overhead. If this
grew into a multi-team service, the same schema moves to Postgres/Snowflake.
"""
from __future__ import annotations

import datetime as dt

import duckdb

from .scanner import Violation

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS scan_seq START 1;
CREATE TABLE IF NOT EXISTS scans (
    scan_id     INTEGER PRIMARY KEY DEFAULT nextval('scan_seq'),
    started_at  TIMESTAMP,
    root_url    VARCHAR,
    label       VARCHAR,
    pages       INTEGER
);
CREATE TABLE IF NOT EXISTS pages (
    scan_id INTEGER, page VARCHAR, url VARCHAR
);
CREATE TABLE IF NOT EXISTS violations (
    scan_id INTEGER, page VARCHAR, rule_id VARCHAR, impact VARCHAR, help VARCHAR,
    help_url VARCHAR, wcag VARCHAR, target VARCHAR, html VARCHAR, fingerprint VARCHAR
);
"""


class Store:
    def __init__(self, path: str = "sentinel.duckdb"):
        self.con = duckdb.connect(path)
        self.con.execute(SCHEMA)

    def save_scan(self, root_url: str, label: str, pages: list[tuple[str, str]],
                  violations: list[Violation]) -> int:
        scan_id = self.con.execute(
            "INSERT INTO scans (started_at, root_url, label, pages) VALUES (?, ?, ?, ?) RETURNING scan_id",
            [dt.datetime.now(), root_url, label, len(pages)],
        ).fetchone()[0]
        if pages:
            self.con.executemany("INSERT INTO pages VALUES (?, ?, ?)",
                                 [(scan_id, rel, url) for rel, url in pages])
        if violations:
            self.con.executemany(
                "INSERT INTO violations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(scan_id, v.page, v.rule_id, v.impact, v.help, v.help_url, v.wcag,
                  v.target, v.html, v.fingerprint) for v in violations],
            )
        return scan_id

    def scans(self) -> list[dict]:
        cur = self.con.execute(
            """SELECT s.scan_id, s.started_at, s.root_url, s.label, s.pages,
                      count(v.fingerprint) AS violations
               FROM scans s LEFT JOIN violations v USING (scan_id)
               GROUP BY ALL ORDER BY s.scan_id""")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def violations(self, scan_id: int) -> list[Violation]:
        rows = self.con.execute(
            """SELECT page, rule_id, impact, help, help_url, wcag, target, html, fingerprint
               FROM violations WHERE scan_id = ? ORDER BY page, rule_id""", [scan_id]).fetchall()
        return [Violation(*r) for r in rows]

    def pages(self, scan_id: int) -> list[str]:
        return [r[0] for r in self.con.execute(
            "SELECT page FROM pages WHERE scan_id = ? ORDER BY page", [scan_id]).fetchall()]

    def page_url(self, scan_id: int, page: str) -> str | None:
        r = self.con.execute("SELECT url FROM pages WHERE scan_id = ? AND page = ?",
                             [scan_id, page]).fetchone()
        return r[0] if r else None

    def close(self) -> None:
        self.con.close()
