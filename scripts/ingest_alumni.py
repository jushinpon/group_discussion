"""Alumni ingestion — same deterministic pipeline as ingest.py, separate namespace.

Why a separate script: alumni are not students. They are never "on track" or "behind", they
are the group's institutional memory and a **benchmark** for what a finished MSc/PhD looks
like in a given subtopic. Keeping them out of students/ means the weekly progress job can
never confuse a graduated person's output with a current student's progress (ADR-0006).

Reuses ingest.py for extraction, IMAP, slimming and indexing so there is exactly one
implementation of each. Writes to:

    alumni/<slug>/mail/raw/*.eml            headers + body text (attachments stored once)
    alumni/<slug>/attachments/<YYYY-MM>/*   original file + .txt
    index/retrieval.sqlite                  kind='alumni_mail' / 'alumni_attachment'

Usage:
    python scripts/ingest_alumni.py --plan                 # sizes only, no download
    python scripts/ingest_alumni.py --tier A --limit 3     # small run
    python scripts/ingest_alumni.py --only zheng_junwen --commit
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import imaplib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest as ing  # noqa: E402  (single source of truth for extraction/imap/index)
import paper_db  # noqa: E402  (shared paper library, ADR-0008)

ROOT = ing.ROOT
ALUMNI_DIR = ROOT / "alumni"
PROCESSED = ROOT / "index" / "alumni_processed.jsonl"
HASHMAP = ROOT / "index" / "attachment_hashes.json"
MAX_ATTACHMENT = 100 * 1024 * 1024


def load_roster(only=None, tiers=("A",), include_candidates=True):
    cfg = yaml.safe_load((ROOT / "alumni.yaml").read_text(encoding="utf-8"))
    people = []
    for p in cfg["alumni"]:
        if tiers and p.get("tier") not in tiers:
            continue
        if only and p["slug"] not in only:
            continue
        if not include_candidates and p.get("status") != "confirmed":
            people.append(p) if p.get("status") == "confirmed" else None
            continue
        people.append(p)
    return cfg, people


def alias_map(people):
    m = {}
    for p in people:
        for a in p["addresses"]:
            m[a.lower()] = p["slug"]
    return m


def fetch_sizes(m, uids):
    """Exact total bytes without downloading: RFC822.SIZE of the full message."""
    total = 0
    for i in range(0, len(uids), 200):
        chunk = ",".join(uids[i:i + 200])
        typ, data = m.uid("FETCH", chunk, "(RFC822.SIZE)")
        if typ != "OK":
            continue
        for item in data:
            if isinstance(item, bytes):
                mt = re.search(rb"RFC822\.SIZE (\d+)", item)
                if mt:
                    total += int(mt.group(1))
    return total


def plan(people, m):
    rows = []
    for p in people:
        uids = set()
        for a in p["addresses"]:
            since = datetime.fromisoformat(str(p["window_start"])).strftime("%d-%b-%Y")
            try:
                uids.update(ing.search_expr(m, f'(SINCE {since} FROM "{a}")'))
            except SystemExit:
                print(f"  ! SEARCH failed for {a}")
        uids = sorted(uids, key=int)
        if not uids:
            print(f"  {p['slug']:18s} no mail found")
            continue
        size = fetch_sizes(m, uids)
        rows.append((p["slug"], p["display_name"], p["tier"], len(uids), size))
        print(f"  {p['slug']:18s} {p['display_name'][:14]:16s} tier={p['tier']:1s} "
              f"mails={len(uids):4d}  {size / 1e9:6.2f} GB")
    tot_n = sum(r[3] for r in rows)
    tot_b = sum(r[4] for r in rows)
    print(f"\n合計 {len(rows)} 人、{tot_n} 封、{tot_b / 1e9:.1f} GB"
          f"（以 25 秒／封估，下載約 {tot_n * 25 / 3600:.1f} 小時）")
    return rows


def load_processed():
    done = set()
    if PROCESSED.exists():
        for line in PROCESSED.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["message_id"])
            except Exception:
                pass
    return done


def load_hashes():
    if HASHMAP.exists():
        try:
            return json.loads(HASHMAP.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _send_to_paper_db(name, payload, slug, dt, stats):
    """Hand a PDF to the shared library via a temp file. Returns its status string."""
    import tempfile
    tmp = None
    status = "error"
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(payload)
            tmp = Path(fh.name)
        res = paper_db.add(tmp, source_slug=slug, mail_date=dt)
        status = res["status"]
        key = "papers_" + status
        stats[key] = stats.get(key, 0) + 1
    except Exception as exc:
        stats["paper_db_errors"] = stats.get("paper_db_errors", 0) + 1
        print(f"      ! paper db failed for {name}: {exc}")
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    return status


def store_attachment(sink: ing.Sink, slug: str, dt: str, name: str, payload: bytes,
                     hashes: dict, stats: dict, pdf_mode="index", max_mb=100):
    digest = hashlib.sha256(payload).hexdigest()
    is_pdf = name.lower().endswith(".pdf")

    # Papers live once, in papers/ — never in a person's folder (ADR-0008 + Pon 2026-11-01).
    # paper_db decides what counts as a paper; only when it refuses (a slide export, a
    # scanned handout) do we fall through and store the file here.
    if is_pdf:
        status = _send_to_paper_db(name, payload, slug, dt, stats)
        if status in ("new", "linked", "dup"):
            stats["pdf_moved_to_papers"] = stats.get("pdf_moved_to_papers", 0) + 1
            return None
        if pdf_mode == "skip":
            stats["pdf_not_stored"] += 1
            stats["pdf_not_stored_bytes"] += len(payload)
            return None

    if len(payload) > max_mb * 1024 * 1024:
        stats["skipped_large"] += 1
        return None
    if digest in hashes:
        stats["dedup"] += 1
        return None
    hashes[digest] = f"alumni/{slug}/{name}"
    dest_dir = ALUMNI_DIR / slug / "attachments" / dt[:7]
    dest = dest_dir / name
    n = 1
    while dest.exists():                       # same filename, different content
        dest = dest_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    sink.write_bytes(dest, payload)
    stats["attachments"] += 1
    stats["attachment_bytes"] += len(payload)
    text = ing.extract_text(dest)
    if text:
        sink.write_text(Path(str(dest) + ".txt"), text)
        stats["text_extracted"] += 1
    return dest, text


def run(people, args):
    cfg_alias = alias_map(people)
    processed = load_processed()
    hashes = load_hashes()
    sink = ing.Sink(args.dry_run)
    con = ing.open_index(args.dry_run)

    lock = ROOT / ".ingest.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: lock.unlink(missing_ok=True))

    m = ing.imap_connect()
    stats = {"attachments": 0, "attachment_bytes": 0, "text_extracted": 0,
             "dedup": 0, "mails": 0, "skipped_large": 0, "unmatched": 0,
             "pdf_not_stored": 0, "pdf_not_stored_bytes": 0}

    for p in people:
        slug = p["slug"]
        uids = set()
        for a in p["addresses"]:
            since = datetime.fromisoformat(str(p["window_start"])).strftime("%d-%b-%Y")
            uids.update(ing.search_expr(m, f'(SINCE {since} FROM "{a}")'))
        todo = [u for u in sorted(uids, key=int) if u not in processed]
        if args.limit:
            todo = todo[:args.limit]
        print(f"\n=== {slug}（{p['display_name']}）tier={p['tier']} "
              f"搜到 {len(uids)} 封、待處理 {len(todo)}")
        for uid in todo:
            try:
                typ, data = m.uid("FETCH", uid, "(BODY.PEEK[])")
            except (imaplib.IMAP4.abort, OSError) as exc:
                # Gmail drops long-lived connections (seen after ~2.5h of continuous fetches).
                # Reconnect and retry this one message instead of losing the whole run.
                print(f"  ! connection lost ({exc}); reconnecting, retrying uid {uid}")
                m = ing.imap_connect()
                typ, data = m.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                print(f"  ! uid {uid} fetch failed")
                continue
            msg = ing.BytesParser(policy=ing.policy.default).parsebytes(data[0][1])
            mid = ing.hdr(msg.get("Message-ID")) or f"uid-{uid}"
            d = msg.get("Date")
            try:
                dt = ing.parsedate_to_datetime(d).strftime("%Y-%m-%d")
            except Exception:
                dt = datetime.now().strftime("%Y-%m-%d")
            subject = ing.hdr(msg.get("Subject"))
            sender = (ing.addrs(msg.get("From")) or [""])[0]
            if sender not in cfg_alias:
                stats["unmatched"] += 1
                print(f"  ~ uid {uid} sender {sender} 不在名冊，跳過")
                continue

            body = ing.body_text(msg)
            att_names, stored = [], []
            for part, name in ing.iter_attachments(msg):
                payload = part.get_payload(decode=True) or b""
                if len(payload) > MAX_ATTACHMENT:
                    stats["skipped_large"] += 1
                    att_names.append(f"{name} (skipped >100MB)")
                    continue
                att_names.append(name)
                out = store_attachment(sink, slug, dt, name, payload, hashes, stats,
                                       pdf_mode=args.pdf_mode, max_mb=args.max_mb)
                if out:
                    dest, text = out
                    stored.append((dest, name, text))

            mail_path = ALUMNI_DIR / slug / "mail" / "raw" / f"{dt}_{re.sub(r'[^A-Za-z0-9]', '', mid)[:40]}.eml"
            slim = ing.slim_mail(msg, body, att_names)
            sink.write_text(mail_path, slim)
            ing.index_doc(con, "alumni_mail", slug, dt, mid, mail_path, subject,
                          (subject or "") + "\n" + body, sink)
            for dest, name, text in stored:
                ing.index_doc(con, "alumni_attachment", slug, dt, mid, dest, name,
                              (name or "") + "\n" + (text or ""), sink)
            sink.append_jsonl(PROCESSED, {"message_id": mid, "slug": slug, "date": dt, "uid": uid})
            processed.add(mid)
            stats["mails"] += 1
            print(f"  {dt} {subject[:56]}  [{len(att_names)} att]")

    if con is not None:
        ing.prune_index(con, sink)
        con.commit()
        con.close()
    if not args.dry_run:
        HASHMAP.write_text(json.dumps(hashes, ensure_ascii=False, indent=0), encoding="utf-8")
    print("\n=== summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  files written: {sink.written}")
    if args.commit and not args.dry_run:
        subprocess.run(["git", "add", "-A"], cwd=ROOT, check=False)
        subprocess.run(["git", "-c", "core.autocrlf=false", "commit", "-q", "-m",
                        f"alumni ingest: {stats['mails']} mails, {stats['attachments']} attachments"],
                       cwd=ROOT, check=False)
        print("  git commit: ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="只算封數與容量，不下載")
    ap.add_argument("--tier", default="A", help="A / B / AB（空字串=全部）")
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定 slug")
    ap.add_argument("--limit", type=int, default=0, help="每人最多幾封")
    ap.add_argument("--pdf-mode", default="index", choices=["full", "index", "skip"],
                    help="full=全部存檔  index=大於 3MB 的 PDF 只留檔名  skip=PDF 一律不存")
    ap.add_argument("--max-mb", type=int, default=100, help="單一附件上限 MB")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    tiers = tuple(t for t in args.tier.upper()) if args.tier else ()
    _, people = load_roster(only=args.only, tiers=tiers)
    print(f"名冊：{len(people)} 人（tier={args.tier or '全部'}）")
    m = ing.imap_connect()
    if args.plan:
        plan(people, m)
        m.logout()
        return
    try:
        run(people, args)
    finally:
        try:
            m.logout()
        except Exception:
            pass        # the socket is already gone; nothing to clean up


if __name__ == "__main__":
    main()
