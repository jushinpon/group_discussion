#!/usr/bin/env python3
"""Re-extract text for every attachment and refresh the FTS5 index.

Needed whenever the extractor improves (e.g. the OOXML run-joining fix that stopped
inserting spaces inside CJK phrases). Existing text files are preserved when the new
extraction yields substantially less text than what is already there — that protects
OCR results, which no pdftotext/OOXML pass can reproduce.

Usage:
  python scripts/reextract.py --dry-run
  python scripts/reextract.py
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ingest  # noqa: E402  (reuses extract_text + index DDL)

DB = ROOT / "index" / "retrieval.sqlite"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    con = None if a.dry_run else sqlite3.connect(DB)
    if con:
        con.executescript(ingest.DDL)

    files = [p for p in sorted(ROOT.glob("students/*/attachments/*/*"))
             if p.suffix.lower() in {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".csv", ".md"}]
    changed = kept = same = 0
    for p in files:
        txt_path = Path(str(p) + ".txt")
        if p.suffix == ".txt":
            continue
        old = txt_path.read_text(encoding="utf-8", errors="replace") if txt_path.exists() else ""
        new = ingest.extract_text(p)
        if old and len(new) < 0.5 * len(old):
            kept += 1
            print(f"keep  {p.relative_to(ROOT)}  (existing {len(old)} > new {len(new)})")
            continue
        if new == old:
            same += 1
            continue
        changed += 1
        print(f"write {p.relative_to(ROOT)}  {len(old)} -> {len(new)} chars")
        if a.dry_run:
            continue
        txt_path.write_text(new, encoding="utf-8", newline="\n")
        slug = p.relative_to(ROOT).parts[1]
        month = p.parent.name
        dt = f"{month}-01" if re.match(r"\d{4}-\d{2}", month) else ""
        rel = str(p.relative_to(ROOT))
        con.execute("INSERT INTO docs(kind,slug,date,message_id,path,title) VALUES('attachment',?,?,NULL,?,?) "
                    "ON CONFLICT(kind,path) DO UPDATE SET title=excluded.title, date=excluded.date",
                    (slug, dt, rel, p.name))
        con.execute("DELETE FROM docs_fts WHERE path=?", (rel,))
        con.execute("INSERT INTO docs_fts(title,body,kind,slug,path,doc_date) VALUES(?,?,?,?,?,?)",
                    (p.name, new[:400_000], "attachment", slug, rel, dt))

    if con:
        con.commit()
        con.close()
    print(f"\nrewritten={changed} unchanged={same} kept(ocr-protected)={kept}")


if __name__ == "__main__":
    main()
