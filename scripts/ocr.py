#!/usr/bin/env python3
"""Local OCR for attachments that yielded no text (scanned PDFs, screenshots).

Tesseract + poppler. Writes <attachment>.txt alongside the attachment (same convention as
ingest.py) and indexes the result into the FTS5 index, so OCR'd scans become searchable.

Why local: the configured vision endpoint was unreachable when this was built (2026-10-31),
and OCR of a personal research corpus should not depend on a network model.

Usage:
  python scripts/ocr.py --list                 # what currently has no text
  python scripts/ocr.py                        # OCR everything missing text
  python scripts/ocr.py --force                # redo even if .txt exists
  python scripts/ocr.py --max-pages 40
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "index" / "retrieval.sqlite"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}
LANGS = "chi_tra+eng"
DDL = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, slug TEXT NOT NULL, date TEXT,
  message_id TEXT, path TEXT NOT NULL, title TEXT, UNIQUE(kind, path));
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, body, kind UNINDEXED, slug UNINDEXED, path UNINDEXED, doc_date UNINDEXED,
  tokenize='trigram');
"""


def find_tools():
    tess = shutil.which("tesseract")
    if not tess:
        for c in (r"C:\Users\jushinpon\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if Path(c).exists():
                tess = c
                break
    ppm = shutil.which("pdftoppm")
    if not tess:
        sys.exit("tesseract not found — install it or pass it on PATH")
    if not ppm:
        sys.exit("pdftoppm not found (poppler)")
    return tess, ppm


def missing_files(force: bool):
    out = []
    for p in sorted(ROOT.glob("students/*/attachments/*/*")):
        if p.suffix.lower() == ".txt" or not p.is_file():
            continue
        if p.suffix.lower() not in IMAGE_EXT | {".pdf"}:
            continue
        txt = Path(str(p) + ".txt")
        if force or not (txt.exists() and txt.stat().st_size > 40):
            out.append(p)
    return out


def run_tess(tess: str, img: Path) -> str:
    r = subprocess.run([tess, str(img), "stdout", "-l", LANGS, "--psm", "3"],
                       capture_output=True, timeout=600)
    return r.stdout.decode("utf-8", "replace")


def ocr_pdf(tess: str, ppm: str, pdf: Path, max_pages: int) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="ocr_", dir=os.environ.get("TMPDIR") or None))
    try:
        subprocess.run([ppm, "-r", "300", "-png", "-l", str(max_pages), str(pdf),
                        str(tmp / "page")], capture_output=True, timeout=900)
        pages = sorted(tmp.glob("page*.png"))
        chunks = []
        for pg in pages:
            t = run_tess(tess, pg).strip()
            if t:
                chunks.append(f"--- page {re.sub(r'[^0-9]', '', pg.stem)} ---\n{t}")
        return "\n\n".join(chunks)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def index(con, path: Path, title: str, body: str, slug: str, dt: str):
    rel = str(path.relative_to(ROOT))
    con.execute("INSERT INTO docs(kind,slug,date,message_id,path,title) VALUES('attachment',?,?,NULL,?,?) "
                "ON CONFLICT(kind,path) DO UPDATE SET title=excluded.title, date=excluded.date",
                (slug, dt, rel, title))
    con.execute("DELETE FROM docs_fts WHERE path=?", (rel,))
    con.execute("INSERT INTO docs_fts(title,body,kind,slug,path,doc_date) VALUES(?,?,?,?,?,?)",
                (title, body[:400_000], "attachment", slug, rel, dt))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max-pages", type=int, default=40)
    a = ap.parse_args()

    targets = missing_files(a.force)
    if a.list:
        print(f"{len(targets)} attachment(s) without usable text:")
        for p in targets:
            print(f"  {p.stat().st_size/1e6:6.2f} MB  {p.relative_to(ROOT)}")
        return
    if not targets:
        print("nothing to OCR — every attachment already has text")
        return

    tess, ppm = find_tools()
    con = sqlite3.connect(DB) if DB.exists() else None
    if con:
        con.executescript(DDL)

    for p in targets:
        slug = p.relative_to(ROOT).parts[1]
        month = p.parent.name
        dt = f"{month}-01" if re.match(r"\d{4}-\d{2}", month) else ""
        print(f"OCR {p.relative_to(ROOT)} …", end=" ", flush=True)
        try:
            text = ocr_pdf(tess, ppm, p, a.max_pages) if p.suffix.lower() == ".pdf" else run_tess(tess, p)
        except Exception as e:
            print(f"FAILED: {e}")
            continue
        text = text.strip()
        if len(text) < 20:
            print(f"no usable text ({len(text)} chars) — not written")
            continue
        Path(str(p) + ".txt").write_text(text, encoding="utf-8", newline="\n")
        if con:
            index(con, p, p.name, text, slug, dt)
        print(f"{len(text)} chars")
    if con:
        con.commit()
        con.close()


if __name__ == "__main__":
    main()
