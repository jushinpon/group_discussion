#!/usr/bin/env python3
"""Ingestion run for group_discussion.

IMAP (readonly) -> per-student folders -> extracted text -> SQLite FTS5 index.

This script is deliberately DETERMINISTIC ONLY. Deciding that a mail is a "progress mail"
and rewriting the student's wiki is semantic work: it is done by the agent, consuming
`index/pending_review.jsonl` (see docs/adr/ and README.md).

Never mutates the mailbox: readonly SELECT + BODY.PEEK only. No flags, no moves, no deletes.

Usage:
  python scripts/ingest.py --dry-run --limit 5      # plan only, writes nothing
  python scripts/ingest.py --limit 10               # small real run
  python scripts/ingest.py --commit                 # full window + git commit
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import imaplib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parent.parent
PASS_FILE = Path(os.path.expanduser("~")) / ".config" / "himalaya" / "melgroup.pass"
IMAP_HOST = "imap.gmail.com"
MAILBOX = "INBOX"
MAX_ATTACH_MB = 100          # above this: record only, do not download (settled 2026-10-31)
TEXT_EXTS = {".txt", ".csv", ".md", ".in", ".pl", ".py", ".json", ".log"}
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}

LOGISTICS_HINT = re.compile(
    r"(開會|meeting時間|幾點|教室|請假|meeting room|zoom|午餐|晚餐|集合|改期)", re.I)


# ---------------------------------------------------------------- identity
def load_identity():
    cfg = yaml.safe_load((ROOT / "students.yaml").read_text(encoding="utf-8"))
    alias = {}
    for s in cfg.get("students", []):
        for a in s.get("addresses", []):
            alias[a.strip().lower()] = s["slug"]
    pi = cfg.get("pi") or {}
    pi_addrs = {a.strip().lower() for a in pi.get("addresses", [])}
    return cfg, alias, pi_addrs


def hdr(value):
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except Exception:
        return str(value).strip()


def addrs(*values):
    out = []
    for v in values:
        for _name, addr in getaddresses([str(v or "")]):
            if addr:
                out.append(addr.strip().lower())
    return out


def safe_name(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "unnamed"
    stem, ext = os.path.splitext(name)
    return (stem[:110] + ext[:20]) or "unnamed"


# ---------------------------------------------------------------- text extraction
class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("p", "br", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _HTMLText()
    try:
        p.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\n{3,}", "\n\n", "".join(p.parts)).strip()


def _xml_text(data: bytes, xpath: str) -> str:
    # OOXML parts come from students' mail, i.e. untrusted input. stdlib ElementTree is used
    # (no defusedxml on this host), so DTD/entity injection is refused outright before parsing:
    # these parts never legitimately declare a DOCTYPE or ENTITY, which closes both XXE and
    # billion-laughs. Size is capped by the caller's MAX_ATTACH_MB limit.
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        return ""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ""
    # Paragraph-aware join: Word/PowerPoint split one sentence across many <w:t>/<a:t> runs,
    # so joining every text node with a space corrupts CJK phrases (未摻雜 B₄C became
    # 未摻雜  B₄C and became unsearchable). Runs are concatenated with no separator and only
    # paragraph boundaries produce a line break.
    out, cur = [], []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t" and node.text:
            cur.append(node.text)
        elif tag == "p" and cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return "\n".join(out).strip()


def extract_pdf(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, "-q", "-enc", "UTF-8", str(path), "-"],
                           capture_output=True, timeout=180)
        return r.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def extract_office(path: Path, ext: str) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if ext == ".docx":
                parts = [z.read(n) for n in names if re.match(r"word/(document|header\d*|footer\d*)\.xml$", n)]
            elif ext == ".pptx":
                parts = [z.read(n) for n in sorted(names) if re.match(r"ppt/slides/slide\d+\.xml$", n)]
                parts += [z.read(n) for n in sorted(names) if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)]
            elif ext == ".xlsx":
                parts = []
                if "xl/sharedStrings.xml" in names:
                    parts.append(z.read("xl/sharedStrings.xml"))
                parts += [z.read(n) for n in sorted(names) if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
            else:
                return ""
    except Exception:
        return ""
    texts = []
    for data in parts:
        t = _xml_text(data, "")
        if t:
            texts.append(t)
    return "\n\n".join(texts).strip()


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            txt = extract_pdf(path)
        elif ext in (".docx", ".pptx", ".xlsx"):
            txt = extract_office(path, ext)
        elif ext in TEXT_EXTS:
            txt = path.read_text(encoding="utf-8", errors="replace").strip()
        else:
            return ""
    except Exception:
        return ""
    # pdftotext on Windows emits CRLF; normalise so stored text and FTS content match exactly.
    return txt.replace("\r\n", "\n").replace("\r", "\n").strip()


# ---------------------------------------------------------------- writer
class Sink:
    """All filesystem/index writes funnel through here so --dry-run is honest."""

    def __init__(self, dry: bool):
        self.dry = dry
        self.written = 0

    def mkdir(self, p: Path):
        if not self.dry:
            p.mkdir(parents=True, exist_ok=True)

    def write_bytes(self, p: Path, data: bytes):
        self.written += 1
        if not self.dry:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def write_text(self, p: Path, text: str, append=False):
        self.written += 1
        if not self.dry:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a" if append else "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    def append_jsonl(self, p: Path, obj: dict):
        self.write_text(p, json.dumps(obj, ensure_ascii=False) + "\n", append=True)


# ---------------------------------------------------------------- index
DDL = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,          -- mail | attachment | wiki
  slug TEXT NOT NULL,
  date TEXT,
  message_id TEXT,
  path TEXT NOT NULL,
  title TEXT,
  UNIQUE(kind, path)
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, body, kind UNINDEXED, slug UNINDEXED, path UNINDEXED, doc_date UNINDEXED,
  tokenize='trigram'
);
"""


def open_index(dry: bool):
    if dry:
        return None
    p = ROOT / "index" / "retrieval.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript(DDL)
    return con


def index_doc(con, kind, slug, dt, message_id, path, title, body, sink: Sink):
    if con is None:
        return
    body = (body or "")[:400_000]
    rel = str(Path(path).relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path)
    cur = con.execute(
        "INSERT INTO docs(kind,slug,date,message_id,path,title) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(kind,path) DO UPDATE SET date=excluded.date, message_id=excluded.message_id, "
        "title=excluded.title, slug=excluded.slug",
        (kind, slug, dt, message_id, rel, title))
    row = con.execute("SELECT id FROM docs WHERE kind=? AND path=?", (kind, rel)).fetchone()
    doc_id = row[0]
    con.execute("DELETE FROM docs_fts WHERE path=?", (rel,))
    con.execute(
        "INSERT INTO docs_fts(title,body,kind,slug,path,doc_date) VALUES(?,?,?,?,?,?)",
        (title or "", body, kind, slug, rel, dt))


# ---------------------------------------------------------------- imap
def imap_connect():
    pwd = PASS_FILE.read_text(encoding="utf-8").strip()
    pwd = re.sub(r"\s+", "", pwd)
    if not pwd or pwd == "PASTE_16_CHAR_APP_PASSWORD_HERE":
        sys.exit("password file not filled in")
    m = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    m.login("melgroup@nsysumel.com", pwd)
    typ, _ = m.select(MAILBOX, readonly=True)   # readonly: flags untouched
    if typ != "OK":
        sys.exit("cannot select mailbox")
    return m


def search_since(m, since: str):
    typ, data = m.uid("SEARCH", None, f'(SINCE {since})')
    if typ != "OK":
        sys.exit(f"SEARCH failed: {data}")
    return [u.decode() for u in data[0].split()]


def search_expr(m, expr: str):
    typ, data = m.uid("SEARCH", None, expr)
    if typ != "OK":
        sys.exit(f"SEARCH failed: {expr} -> {data}")
    return [u.decode() for u in data[0].split()]


def load_processed():
    p = ROOT / "index" / "processed.jsonl"
    done = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["message_id"])
            except Exception:
                pass
    return done


def load_hashmap():
    p = ROOT / "index" / "attachment_hashes.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ---------------------------------------------------------------- body
def slim_mail(msg, body: str, att_names: list[str]) -> str:
    """Headers + body text only. Attachments are stored once under attachments/, so the raw
    message is NOT kept byte-for-byte (it would duplicate every attachment)."""
    keep = ["From", "To", "Cc", "Date", "Subject", "Message-ID", "In-Reply-To", "References"]
    lines = [f"{k}: {hdr(msg.get(k))}" for k in keep if msg.get(k)]
    if att_names:
        lines.append(f"Attachments: {', '.join(att_names)}")
    return "\n".join(lines) + "\n\n" + (body or "") + "\n"


def body_text(msg) -> str:
    plains, htmls = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_content()
        except Exception:
            payload = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        (plains if ctype == "text/plain" else htmls).append(str(payload))
    text = "\n\n".join(t for t in plains if t.strip())
    if not text.strip() and htmls:
        text = html_to_text("\n".join(htmls))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def iter_attachments(msg):
    for part in msg.walk():
        if part.is_multipart():
            continue
        fname = part.get_filename()
        if not fname:
            continue
        yield part, safe_name(hdr(fname))


# ---------------------------------------------------------------- main
def prune_index(con, sink: Sink):
    """Drop index rows whose file no longer exists (e.g. after a re-home or a failed run)."""
    if con is None:
        return
    dead = [(i, p) for i, p in con.execute("SELECT id, path FROM docs")]
    gone = [(i, p) for i, p in dead if not (ROOT / p).exists()]
    for i, p in gone:
        con.execute("DELETE FROM docs_fts WHERE path=?", (p,))
        con.execute("DELETE FROM docs WHERE id=?", (i,))
    if gone:
        print(f"  pruned {len(gone)} stale index rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="IMAP SINCE date, e.g. 01-Jul-2026")
    ap.add_argument("--limit", type=int, default=0, help="max messages this run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--skip-attachments", action="store_true")
    args = ap.parse_args()

    cfg, alias, pi_addrs = load_identity()
    window = cfg.get("window_start")

    # Lock: a bulk backfill or a wiki rebuild must not overlap the 30-minute cron tick, or
    # two agents write the same wiki pages. gd_ingest.sh skips the tick while this exists.
    lock = ROOT / ".ingest.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: lock.unlink(missing_ok=True))
    # Per-student window override: a 博三 student's record starts at enrolment, not at the
    # global four-month cutoff. The IMAP search must start at the earliest such window.
    windows = {s["slug"]: str(s.get("window_start") or window) for s in cfg.get("students", [])}
    earliest = min([str(window)] + list(windows.values())) if windows else str(window)
    since = args.since or datetime.fromisoformat(earliest).strftime("%d-%b-%Y")

    sink = Sink(args.dry_run)
    con = open_index(args.dry_run)
    processed = load_processed()
    hashes = load_hashmap()

    m = imap_connect()
    # Candidate set = the global window, plus a wider per-student FROM search for every
    # student whose own window starts earlier. A plain SINCE over the whole mailbox would
    # mean ~1,500 header round-trips to discover mail we already skip; the FROM search keeps
    # this proportional to the students who actually need backfill.
    uid_set = set(search_since(m, datetime.fromisoformat(str(window)).strftime("%d-%b-%Y")))
    for s in cfg.get("students", []):
        ws = str(s.get("window_start") or window)
        if ws < str(window):
            for addr in s.get("addresses", []):
                found = search_expr(m, f'(SINCE {datetime.fromisoformat(ws).strftime("%d-%b-%Y")} FROM "{addr}")')
                uid_set.update(found)
                print(f"backfill search {s['slug']} from {ws}: {len(found)} messages")
    uids = sorted(uid_set, key=lambda x: int(x))
    print(f"INBOX since {since}: {len(uids)} candidate messages; already processed: {len(processed)}")

    todo = []
    for uid in uids:
        # header-only peek: cheap, and lets us skip processed mail without downloading bodies
        typ, d = m.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC SUBJECT DATE)] RFC822.SIZE)")
        if typ != "OK" or not d or not isinstance(d[0], tuple):
            print(f"  ! uid {uid}: header fetch failed")
            continue
        raw_hdr = d[0][1]
        meta = d[0][0].decode("utf-8", "replace")
        msg = BytesParser(policy=policy.default).parsebytes(raw_hdr)
        mid = hdr(msg.get("Message-ID")) or f"uid-{uid}"
        if mid in processed:
            continue
        size = 0
        mm = re.search(r"RFC822\.SIZE (\d+)", meta)
        if mm:
            size = int(mm.group(1))
        todo.append({"uid": uid, "mid": mid, "header": msg, "size": size})
        if args.limit and len(todo) >= args.limit:
            break

    print(f"new messages to ingest: {len(todo)}")
    stats = {"progress": 0, "pi": 0, "unassigned": 0, "before_window": 0,
             "attachments": 0, "attachment_bytes": 0, "text_extracted": 0,
             "skipped_large": 0}

    for item in todo:
        uid, mid, msg, size = item["uid"], item["mid"], item["header"], item["size"]
        sender = addrs(msg.get("From"))
        sender = sender[0] if sender else ""
        to_cc = addrs(msg.get("To"), msg.get("Cc"))
        subject = hdr(msg.get("Subject"))
        try:
            dt = parsedate_to_datetime(str(msg.get("Date")))
            dt_s = dt.strftime("%Y-%m-%d")
            yyyymm = dt.strftime("%Y-%m")
        except Exception:
            dt_s, yyyymm = "1970-01-01", "1970-01"

        is_pi = sender in pi_addrs

        def in_window(slug: str) -> bool:
            return dt_s >= windows.get(slug, str(window))

        if is_pi:
            targets = sorted({alias[a] for a in to_cc if a in alias and in_window(alias[a])})
            stats["pi"] += 1
        elif sender in alias:
            slug = alias[sender]
            if not in_window(slug):
                stats["before_window"] += 1
                print(f"  skip  uid {uid:>6} {dt_s} {sender:38s} "
                      f"(before {slug} window {windows[slug]})")
                continue
            targets = [slug]
            stats["progress"] += 1
        else:
            targets = []
            stats["unassigned"] += 1

        kind_hint = "pi-instruction" if is_pi else ("progress?" if targets else "unassigned")
        print(f"  uid {uid:>6} {dt_s} {sender:38s} -> {targets or ['_unassigned']}  [{size/1e6:.1f} MB] {subject[:48]}")

        # full raw fetch (single round trip); skip attachment extraction for huge messages
        typ, d = m.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK" or not d or not isinstance(d[0], tuple):
            print(f"    ! full fetch failed, header-only recorded")
            full = None
        else:
            full = d[0][1]
        body = ""
        atts = []
        if full:
            fmsg = BytesParser(policy=policy.default).parsebytes(full)
            body = body_text(fmsg)
            if not args.skip_attachments:
                atts = list(iter_attachments(fmsg))

        if is_pi:
            store_slugs = ["_pi"]
        elif not targets:
            store_slugs = ["_unassigned"]
        else:
            store_slugs = targets

        raw_rel = None
        att_names = [f for _p, f in atts]
        for slug in store_slugs:
            base = ROOT / "students" / slug
            sink.mkdir(base / "mail" / "raw")
            fname = f"{dt_s}_{mid.strip('<>')[:40].replace('@','_at_')}.eml"
            fname = safe_name(fname)
            raw_path = base / "mail" / "raw" / fname
            if full:
                sink.write_text(raw_path, slim_mail(fmsg, body, att_names))
            raw_rel = str(raw_path.relative_to(ROOT))
            sink.append_jsonl(base / "mail" / "manifest.jsonl", {
                "message_id": mid, "date": dt_s, "from": sender,
                "to": [a for a in to_cc if a in alias], "subject": subject,
                "kind_hint": kind_hint, "raw": raw_rel, "uid": uid,
                "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            index_doc(con, "mail", slug, dt_s, mid, raw_path, subject, body, sink)

        # attachments: stored once under the owner (or _pi / _unassigned)
        owner = store_slugs[0]
        for part, fname in atts:
            payload = part.get_payload(decode=True) or b""
            if not payload:
                continue
            if len(payload) > MAX_ATTACH_MB * 1024 * 1024:
                stats["skipped_large"] += 1
                sink.append_jsonl(ROOT / "index" / "skipped_large.jsonl",
                                  {"message_id": mid, "slug": owner, "filename": fname,
                                   "bytes": len(payload), "date": dt_s})
                continue
            sha = hashlib.sha256(payload).hexdigest()
            if sha in hashes:
                rel = hashes[sha]
                print(f"    = dup attachment {fname} ({len(payload)/1e6:.2f} MB)")
            else:
                dest = ROOT / "students" / owner / "attachments" / yyyymm / fname
                n = 1
                while dest.exists() and not args.dry_run:
                    dest = dest.with_name(f"{Path(fname).stem}_{n}{Path(fname).suffix}")
                    n += 1
                sink.write_bytes(dest, payload)
                rel = str(dest.relative_to(ROOT))
                hashes[sha] = rel
                stats["attachments"] += 1
                stats["attachment_bytes"] += len(payload)
                text = "" if args.dry_run else extract_text(dest)
                if text:
                    sink.write_text(dest.with_suffix(dest.suffix + ".txt"), text)
                    stats["text_extracted"] += 1
                    index_doc(con, "attachment", owner, dt_s, mid, dest, fname, text, sink)
                # Papers also go to the shared library (ADR-0008): the same PDF is re-read by
                # later cohorts, so it must not stay buried in one student's folder.
                if not args.dry_run:
                    try:
                        import paper_db
                        r = paper_db.add(dest, source_slug=owner, mail_date=dt_s, text=text)
                        if r["status"] in ("new", "linked"):
                            stats["papers_" + r["status"]] = stats.get("papers_" + r["status"], 0) + 1
                            print(f"      → paper db: {r['status']} {str(r.get('file', ''))[:60]}")
                    except Exception as exc:                     # never fail ingestion for this
                        print(f"      ! paper db failed: {exc}")
                print(f"    + attachment {fname} ({len(payload)/1e6:.2f} MB) text={len(text)} chars")
            sink.append_jsonl(ROOT / "students" / owner / "mail" / "manifest.jsonl",
                              {"message_id": mid, "attachment": fname, "sha256": sha,
                               "path": rel, "bytes": len(payload)})

        sink.append_jsonl(ROOT / "index" / "processed.jsonl",
                          {"message_id": mid, "uid": uid, "date": dt_s, "from": sender,
                           "targets": targets or ["_unassigned"], "kind_hint": kind_hint,
                           "attachments": len(atts),
                           "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

        if targets and not is_pi:
            sink.append_jsonl(ROOT / "index" / "pending_review.jsonl", {
                "message_id": mid, "uid": uid, "slug": targets[0], "date": dt_s,
                "from": sender, "subject": subject,
                "attachments": [f for _p, f in atts],
                "kind_hint": kind_hint,
                "logistics_hint": bool(LOGISTICS_HINT.search((subject or "") + " " + body[:600])),
                "excerpt": re.sub(r"\s+", " ", body)[:600],
            })
        elif is_pi:
            for slug in targets:
                sink.append_jsonl(ROOT / "index" / "pending_review.jsonl", {
                    "message_id": mid, "uid": uid, "slug": slug, "date": dt_s,
                    "from": sender, "subject": subject, "kind_hint": "pi-instruction",
                    "attachments": [f for _p, f in atts],
                    "excerpt": re.sub(r"\s+", " ", body)[:600],
                })

    if con is not None:
        prune_index(con, sink)
        con.commit()
        con.close()
    if not args.dry_run:
        (ROOT / "index" / "attachment_hashes.json").write_text(
            json.dumps(hashes, indent=0, sort_keys=True), encoding="utf-8")

    print("\n=== summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  files written: {sink.written}")

    if args.commit and not args.dry_run and sink.written:
        subprocess.run(["git", "-C", str(ROOT), "add", "-A"], check=False)
        r = subprocess.run(["git", "-C", str(ROOT), "commit", "-q", "-m",
                            f"ingest: {len(todo)} messages, {stats['attachments']} attachments"],
                           check=False)
        print("  git commit:", "ok" if r.returncode == 0 else "nothing to commit / failed")


if __name__ == "__main__":
    main()
