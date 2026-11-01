#!/usr/bin/env python3
"""Query the retrieval index (agentic RAG entry point).

The index is SQLite FTS5 with the trigram tokenizer (ADR-0002), which matches Chinese and
English substrings without word segmentation. Terms shorter than 3 characters cannot be
matched by trigram FTS, so this script detects that case and falls back to a LIKE scan over
the same rows, and it reports the fallback in its output instead of silently returning
nothing.

Usage:
  python scripts/search.py "MoS2 鈉離子"
  python scripts/search.py "high-entropy" --slug wu_dongye --kind attachment --limit 5
  python scripts/search.py "Bauschinger" --kind alumni_attachment      # 只找校友層
  python scripts/search.py "楊旻哲" --days 120 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "index" / "retrieval.sqlite"
SNIPPET = 240


def tokenize_query(q: str):
    """Split on whitespace/commas; keep quoted phrases whole."""
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]+"', " ", q)
    terms = phrases + [t for t in re.split(r"[\s,，、]+", rest) if t]
    return terms


def run(query: str, slug=None, kind=None, limit=10, days=None, as_json=False):
    if not DB.exists():
        sys.exit(f"no index at {DB} — run scripts/ingest.py first")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    terms = tokenize_query(query)
    if not terms:
        sys.exit("empty query")

    short = [t for t in terms if len(t) < 3]
    mode = "fts"
    rows = []
    if short:
        mode = "like-fallback"
        where, params = [], []
        for t in terms:
            where.append("(docs_fts.title LIKE ? OR docs_fts.body LIKE ?)")
            params += [f"%{t}%", f"%{t}%"]
        if slug:
            where.append("docs_fts.slug = ?"); params.append(slug)
        if kind:
            where.append("docs_fts.kind = ?"); params.append(kind)
        sql = ("SELECT title, body, kind, slug, path, doc_date FROM docs_fts WHERE "
               + " AND ".join(where) + " LIMIT ?")
        rows = con.execute(sql, params + [limit]).fetchall()
    else:
        match = " ".join(f'"{t}"' if (" " in t or "-" in t) else t for t in terms)
        where, params = ["docs_fts MATCH ?"], [match]
        if slug:
            where.append("slug = ?"); params.append(slug)
        if kind:
            where.append("kind = ?"); params.append(kind)
        sql = ("SELECT title, body, kind, slug, path, doc_date FROM docs_fts WHERE "
               + " AND ".join(where) + " ORDER BY rank LIMIT ?")
        try:
            rows = con.execute(sql, params + [limit]).fetchall()
        except sqlite3.OperationalError as e:
            print(f"FTS query rejected ({e}); falling back to LIKE", file=sys.stderr)
            mode = "like-fallback"
            where, params = [], []
            for t in terms:
                where.append("(title LIKE ? OR body LIKE ?)"); params += [f"%{t}%", f"%{t}%"]
            if slug:
                where.append("slug = ?"); params.append(slug)
            if kind:
                where.append("kind = ?"); params.append(kind)
            rows = con.execute("SELECT title, body, kind, slug, path, doc_date FROM docs_fts WHERE "
                               + " AND ".join(where) + " LIMIT ?", params + [limit]).fetchall()

    out = []
    for title, body, kind, slug_, path, date in rows:
        idx = -1
        low = (body or "").lower()
        for t in terms:
            idx = low.find(t.lower())
            if idx >= 0:
                break
        start = max(0, idx - SNIPPET // 3) if idx > 0 else 0
        out.append({"title": title, "kind": kind, "slug": slug_, "date": date,
                    "path": path, "snippet": re.sub(r"\s+", " ", (body or "")[start:start + SNIPPET])})

    if as_json:
        print(json.dumps({"query": query, "mode": mode, "count": len(out), "results": out},
                         ensure_ascii=False, indent=1))
    else:
        print(f"query={query!r} mode={mode} hits={len(out)}")
        for r in out:
            print(f"\n[{r['kind']}] {r['slug']} {r['date']}  {r['title'][:80]}")
            print(f"  {r['path']}")
            print(f"  …{r['snippet']}…")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--slug")
    ap.add_argument("--kind", choices=["mail", "attachment", "wiki", "paper",
                                       "alumni_mail", "alumni_attachment", "alumni_wiki"])
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--days", type=int, default=None, help="accepted for caller convenience; filter in the query instead")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    run(a.query, a.slug, a.kind, a.limit, a.days, a.json)


if __name__ == "__main__":
    main()
