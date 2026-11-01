#!/usr/bin/env python3
"""Agent-side worklist for the wiki update step.

`ingest.py` appends every new item to index/pending_review.jsonl. This script shows what the
agent has NOT yet ruled on, and records verdicts. Verdicts are auditable and re-runnable:
nothing is deleted, and `--undo` is just a later verdict for the same message_id.

Usage:
  python scripts/pending.py                 # list unreviewed items grouped by student
  python scripts/pending.py --slug you_junyi
  python scripts/pending.py --show <message_id>
  python scripts/pending.py --mark <message_id> --verdict progress --note "PPT 有新材料"
  python scripts/pending.py --stats
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "index" / "pending_review.jsonl"
REVIEWED = ROOT / "index" / "reviewed.jsonl"
VERDICTS = ("progress", "logistics", "noise", "pi-instruction")


def read_jsonl(p: Path):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def reviewed_map():
    """message_id -> latest verdict (later lines win)."""
    latest = {}
    for row in read_jsonl(REVIEWED):
        mid = row.get("message_id")
        if mid:
            latest[mid] = row
    return latest


def unreviewed():
    done = reviewed_map()
    seen = set()
    items = []
    for row in read_jsonl(PENDING):
        mid = row.get("message_id")
        if not mid or mid in done or mid in seen:
            continue
        seen.add(mid)
        items.append(row)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    ap.add_argument("--show")
    ap.add_argument("--mark")
    ap.add_argument("--verdict", choices=VERDICTS)
    ap.add_argument("--note", default="")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    if a.mark:
        if not a.verdict:
            raise SystemExit("--mark requires --verdict")
        row = {"message_id": a.mark, "verdict": a.verdict, "note": a.note}
        REVIEWED.parent.mkdir(parents=True, exist_ok=True)
        with REVIEWED.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("recorded:", row)
        return

    if a.show:
        for row in read_jsonl(PENDING):
            if row.get("message_id") == a.show or str(row.get("uid")) == str(a.show):
                print(json.dumps(row, ensure_ascii=False, indent=1))
                return
        raise SystemExit(f"not found: {a.show}")

    items = unreviewed()
    if a.stats:
        by_slug = defaultdict(int)
        for r in items:
            by_slug[r.get("slug", "?")] += 1
        print(f"unreviewed: {len(items)}")
        for slug, n in sorted(by_slug.items()):
            print(f"  {slug}: {n}")
        print(f"reviewed total: {len(reviewed_map())}")
        return

    if a.slug:
        items = [r for r in items if r.get("slug") == a.slug]
    print(f"unreviewed items: {len(items)}")
    for r in items:
        flag = " [logistics?]" if r.get("logistics_hint") else ""
        atts = len(r.get("attachments") or [])
        print(f"\n{r.get('date')}  {r.get('slug')}  attachments={atts}{flag}")
        print(f"  {r.get('subject','')[:100]}")
        print(f"  id={r.get('message_id')}")
        print(f"  …{(r.get('excerpt') or '')[:220]}…")


if __name__ == "__main__":
    main()
