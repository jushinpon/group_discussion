"""Paper library — every research paper PDF that arrives by mail lands here, once.

Why: papers are the one asset that is *not* specific to the student who happened to receive
them. The same PDF is re-sent across cohorts and across alumni, so keeping it inside
`students/<slug>/attachments/` means the next student never finds it. This module gives the
group a single content-addressed library that survives a student graduating.

Layout (default `papers/`, override with $PAPER_DB):

    papers/files/<sha8>_<safe-title>.pdf   the bytes, deduplicated by sha256
    papers/index.jsonl                     one JSON record per unique paper (tracked in git)
    papers/README.md                       provenance rules

The index is git-tracked because it is the searchable catalogue; the PDFs stay out of git
(licensing + size). A paper is identified by sha256, and secondarily by DOI: if the same DOI
arrives from two students, the second copy is linked, not stored.

Nothing here decides what a paper *means* — that stays in each wiki.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("PAPER_DB") or (ROOT / "papers"))
INDEX = DB / "index.jsonl"
FILES = DB / "files"
TEXT = DB / "text"          # 抽出的全文（一併進 git？不：與 PDF 同授權限制，保持不進版控）

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
# Things that are PDFs but are not papers. Their titles carry these words, not their content.
# Filename signals that are decisive on their own. 'meeting' is deliberately NOT here:
# students routinely save a paper as "20240924_賴冠宏_meeting_paper.pdf", so only words that
# describe lab paperwork disqualify a file by name.
NOT_A_PAPER = re.compile(
    r"投影片|簡報|slides?|deck|報告|海報|poster|議程|報名|簽到|簽退|作業|"
    r"certificate|收據|發票|同意書|申請表|授權書|進度",
    re.I,
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(text: str, limit: int = 90) -> str:
    text = re.sub(r"^\d{6,8}[_\-\s]*", "", text)          # leading yyyymmdd
    text = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ._-")
    return text[:limit].strip(" ._-") or "paper"


def guess_doi(*candidates: str) -> str | None:
    for c in candidates:
        if not c:
            continue
        m = DOI_RE.search(c.replace("%2F", "/"))
        if m:
            return m.group(1).rstrip(".,;)")
    return None


def looks_like_paper(path: Path, text: str | None = None) -> bool:
    """Decide by name first; when text is available, a near-empty PDF is not a paper either.

    Callers that pass text=None still go through add(), which extracts the text of a new PDF
    before committing to a copy - a 3-slide export and a real paper look identical by name.
    """
    if path.suffix.lower() != ".pdf":
        return False
    if NOT_A_PAPER.search(path.name):
        return False
    if text is not None and len(text) < 400:
        return False        # scanned slide export / one-page handout
    return True


def load_index() -> dict:
    """Two keys per record: by sha256, and by doi (both point at the same record)."""
    by_sha, by_doi = {}, {}
    if INDEX.exists():
        for line in INDEX.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            by_sha[rec["sha256"]] = rec
            if rec.get("doi"):
                by_doi[rec["doi"]] = rec
    return {"by_sha": by_sha, "by_doi": by_doi}


def _index_record(rec: dict, text: str | None) -> None:
    """Mirror the paper into the retrieval index as kind='paper' (best effort, never fatal).

    A long ingest holds a write lock on retrieval.sqlite, so a paper arriving mid-ingest can
    hit 'database is locked'. Retry with backoff instead of dropping the row.
    """
    db = ROOT / "index" / "retrieval.sqlite"
    if not db.exists():
        return
    import sqlite3
    import time

    body = "\n".join([rec.get("title") or "", rec.get("doi") or "", text or ""])
    srcs = ", ".join(sorted({s.get("slug", "") for s in rec.get("sources", [])}))
    for attempt in range(4):
        con = None
        try:
            con = sqlite3.connect(str(db), timeout=30)
            con.execute("PRAGMA busy_timeout=30000")
            rows = con.execute(
                "SELECT path FROM docs_fts WHERE kind='paper' AND path=?", (rec["file"],)).fetchall()
            if rows:
                con.execute("DELETE FROM docs_fts WHERE kind='paper' AND path=?", (rec["file"],))
            con.execute(
                "INSERT INTO docs_fts (title, body, kind, slug, path, doc_date) "
                "VALUES (?, ?, 'paper', ?, ?, ?)",
                (rec.get("title", ""), body, srcs, rec["file"], rec.get("added", "")[:10]))
            con.commit()
            return
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"  ! paper index failed: {exc}")
            return
        except Exception as exc:
            print(f"  ! paper index failed: {exc}")
            return
        finally:
            if con is not None:
                con.close()


def reindex_all() -> dict:
    """Build kind='paper' rows for every catalogued paper (run once after a backfill)."""
    idx = load_index()
    recs = {r["sha256"]: r for r in idx["by_sha"].values()}
    n = 0
    for rec in recs.values():
        _index_record(rec, None)
        n += 1
    return {"indexed": n}


def _extract_and_store_text(digest: str, pdf_path: Path) -> str | None:
    """Keep one plain-text copy per paper, so search works without re-parsing every PDF."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ingest as ing
    try:
        text = ing.extract_text(pdf_path)
    except Exception:
        return None
    if not text:
        return None
    TEXT.mkdir(parents=True, exist_ok=True)
    (TEXT / f"{digest[:8]}.txt").write_text(text, encoding="utf-8")
    return text


def _append_index(rec: dict) -> None:
    DB.mkdir(parents=True, exist_ok=True)
    with open(INDEX, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def add(path: Path, source_slug: str, mail_date: str | None = None,
        text: str | None = None, dry_run: bool = False) -> dict:
    """Copy one PDF into the library. Returns {'status': new|dup|linked|skipped, ...}."""
    path = Path(path)
    if not looks_like_paper(path, text):
        return {"status": "skipped", "reason": "not a paper", "path": str(path)}
    if text is None and path.suffix.lower() == ".pdf":
        text = _extract_and_store_text(sha256_file(path), path)
        if text is not None and len(text) < 400:
            return {"status": "skipped", "reason": "no extractable text (<400 chars)", "path": str(path)}

    idx = load_index()
    digest = sha256_file(path)
    if digest in idx["by_sha"]:
        rec = idx["by_sha"][digest]
        # Same bytes, new reader: the whole point of `sources` is who has read this paper, so
        # record the second sighting instead of silently dropping it (ADR-0008).
        srcs = rec.setdefault("sources", [])
        new_src = {"slug": source_slug, "date": mail_date, "name": path.name}
        if not any(s.get("slug") == source_slug and s.get("date") == mail_date for s in srcs):
            srcs.append(new_src)
            if not dry_run:
                _rewrite_index(idx)
            return {"status": "linked", "sha256": digest, "file": rec["file"], "sources": srcs}
        return {"status": "dup", "sha256": digest, "file": rec["file"], "sources": srcs}

    doi = guess_doi(path.name, text or "")
    if doi and doi in idx["by_doi"]:
        rec = idx["by_doi"][doi]
        rec.setdefault("sources", []).append({"slug": source_slug, "date": mail_date,
                                              "name": path.name})
        if not dry_run:
            _rewrite_index(idx)
        return {"status": "linked", "sha256": digest, "doi": doi, "file": rec["file"]}

    title = safe_name(path.stem)
    dest_name = f"{digest[:8]}_{title}.pdf"
    dest = FILES / dest_name
    rec = {
        "sha256": digest,
        "file": f"files/{dest_name}",
        "size": path.stat().st_size,
        "title": title,
        "doi": doi,
        "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": [{"slug": source_slug, "date": mail_date, "name": path.name}],
    }
    if dry_run:
        return {"status": "new", "dry_run": True, **rec}
    FILES.mkdir(parents=True, exist_ok=True)
    n = 1
    while dest.exists():                      # same name, different bytes
        dest = FILES / f"{digest[:8]}_{title}_{n}.pdf"
        n += 1
    rec["file"] = f"files/{dest.name}"
    shutil.copy2(path, dest)
    if text is None:
        text = _extract_and_store_text(digest, dest)
    rec["text_file"] = f"text/{digest[:8]}.txt" if (TEXT / f"{digest[:8]}.txt").exists() else None
    _append_index(rec)
    _index_record(rec, text)
    return {"status": "new", **rec}


def _rewrite_index(idx: dict) -> None:
    """Rewrite the jsonl after mutating a record in place (sources list grew)."""
    if not INDEX.exists():
        return
    seen = {}
    for rec in idx["by_sha"].values():
        seen[rec["sha256"]] = rec
    DB.mkdir(parents=True, exist_ok=True)
    with open(INDEX, "w", encoding="utf-8") as f:
        for rec in seen.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def stats() -> dict:
    idx = load_index()
    recs = {r["sha256"]: r for r in idx["by_sha"].values()}
    total_bytes = sum(r.get("size", 0) for r in recs.values())
    with_doi = sum(1 for r in recs.values() if r.get("doi"))
    return {"papers": len(recs), "bytes": total_bytes,
            "gb": round(total_bytes / 1e9, 2), "with_doi": with_doi}


def enrich(with_text: bool = False) -> dict:
    """Backfill DOI / better titles for records that were catalogued from the filename alone."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ingest as ing

    idx = load_index()
    recs = {r["sha256"]: r for r in idx["by_sha"].values()}
    changed = 0
    for rec in recs.values():
        if rec.get("doi"):
            continue
        path = DB / rec["file"]
        if not path.exists() or path.suffix.lower() != ".pdf":
            continue
        try:
            text = ing.extract_text(path)
        except Exception:
            continue
        doi = guess_doi(rec.get("title", ""), text[:8000])
        if doi:
            rec["doi"] = doi
            changed += 1
    if changed:
        DB.mkdir(parents=True, exist_ok=True)
        with open(INDEX, "w", encoding="utf-8") as f:
            for rec in recs.values():
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"enriched": changed, "total": len(recs)}


def extract_missing() -> dict:
    """Extract plain text for every catalogued paper that has none yet, then reindex."""
    idx = load_index()
    recs = {r["sha256"]: r for r in idx["by_sha"].values()}
    made = 0
    for rec in recs.values():
        target = TEXT / f"{rec['sha256'][:8]}.txt"
        if target.exists():
            continue
        pdf = DB / rec["file"]
        if not pdf.exists():
            continue
        text = _extract_and_store_text(rec["sha256"], pdf)
        if text:
            rec["text_file"] = f"text/{target.name}"
            made += 1
            _index_record(rec, text)
    if made:
        with open(INDEX, "w", encoding="utf-8") as f:
            for rec in recs.values():
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"text_extracted": made, "total": len(recs)}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="paper library")
    ap.add_argument("--scan", nargs="*", default=None,
                    help="掃描這些目錄下的 PDF 並收進資料庫（例：students）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--enrich", action="store_true", help="從 PDF 內文補 DOI")
    ap.add_argument("--reindex", action="store_true", help="把目錄全部寫進檢索索引")
    ap.add_argument("--extract", action="store_true", help="為尚未抽全文的論文補 text/")
    args = ap.parse_args()

    if args.extract:
        print(extract_missing())
        return

    if args.reindex:
        print(reindex_all())
        return

    if args.enrich:
        print(enrich())
        return

    if args.stats:
        s = stats()
        print(f"資料庫：{DB}")
        print(f"  論文 {s['papers']} 篇、{s['gb']} GB、有 DOI {s['with_doi']} 篇")
        return

    if args.scan is None:
        ap.print_help()
        return

    counts = {"new": 0, "dup": 0, "linked": 0, "skipped": 0}
    for target in args.scan:
        base = Path(target)
        if not base.is_absolute():
            base = ROOT / base
        for pdf in sorted(base.rglob("*.pdf")):
            slug = pdf.relative_to(base).parts[0] if len(pdf.relative_to(base).parts) > 1 else "-"
            res = add(pdf, source_slug=slug, dry_run=args.dry_run)
            counts[res["status"]] = counts.get(res["status"], 0) + 1
            if res["status"] in ("new", "linked"):
                print(f"  {res['status']:6s} {pdf.name[:70]}")
    print("\n=== paper db ===")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print(f"  {stats()}")


if __name__ == "__main__":
    main()
