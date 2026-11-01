#!/usr/bin/env python3
"""Weekly digest: gather everything the agent needs to judge one ISO week, per student.

Deterministic only — it collects and formats; the judgement (on-track / at-risk / off-track
plus evidence) is the agent's. See GLOSSARY.md and the progress-report rules.

Usage:
  python scripts/week.py                    # current ISO week
  python scripts/week.py --week 2026-W40
  python scripts/week.py --week 2026-W40 --slug lai_junyu
  python scripts/week.py --week 2026-W40 --write   # write progress/<week>.md skeletons
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}


def iso_week_bounds(spec: str | None):
    if spec:
        y, w = spec.split("-W")
        start = date.fromisocalendar(int(y), int(w), 1)
    else:
        today = date.today()
        start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def load_students():
    cfg = yaml.safe_load((ROOT / "students.yaml").read_text(encoding="utf-8"))
    return cfg.get("students", [])


def processed():
    p = ROOT / "index" / "processed.jsonl"
    rows = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def attach_index():
    """message_id -> [(filename, text_chars)] from the per-student manifests."""
    out = defaultdict(list)
    for man in ROOT.glob("students/*/mail/manifest.jsonl"):
        for line in man.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if "attachment" not in r:
                continue
            path = ROOT / r.get("path", "")
            txt = Path(str(path) + ".txt")
            chars = txt.stat().st_size if txt.exists() else 0
            out[r.get("message_id")].append((r.get("attachment"), chars))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week")
    ap.add_argument("--slug")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    start, end = iso_week_bounds(a.week)
    week_id = f"{start.isocalendar().year}-W{start.isocalendar().week:02d}"
    rows = processed()
    atts = attach_index()
    students = load_students()

    print(f"# 週報取料 {week_id}  ({start} → {end})\n")
    for s in students:
        slug = s["slug"]
        if a.slug and slug != a.slug:
            continue
        mine = [r for r in rows if slug in (r.get("targets") or [])]
        inweek = [r for r in mine if start.isoformat() <= r.get("date", "") <= end.isoformat()]
        goals = ROOT / "students" / slug / "goals.md"
        print(f"## {s.get('display_name')}（{slug}）")
        print(f"- 本週進度信：{len(inweek)} 封；窗口內累計：{len(mine)} 封")
        if goals.exists():
            head = [l for l in goals.read_text(encoding="utf-8").splitlines() if l.strip()][:12]
            print("- goals.md 開頭：" + " / ".join(head[:4]))
        for r in inweek:
            files = atts.get(r.get("message_id"), [])
            print(f"  - {r['date']} | {r.get('from')} | 附件 {len(files)} 個")
            for name, chars in files:
                print(f"      · {name}  (text {chars} chars)")
        if not inweek:
            print("  - （本週無來信）")
        prev = sorted((ROOT / "students" / slug / "progress").glob("*.md"))
        if prev:
            print(f"- 上次報告：{prev[-1].name}")
        print()

    if a.write:
        for s in students:
            slug = s["slug"]
            if a.slug and slug != a.slug:
                continue
            dest = ROOT / "students" / slug / "progress" / f"{week_id}.md"
            if dest.exists():
                print("exists, skipped:", dest.relative_to(ROOT))
                continue
            mine = [r for r in rows if slug in (r.get("targets") or [])
                    and start.isoformat() <= r.get("date", "") <= end.isoformat()]
            lines = [f"# {s.get('display_name')} — {week_id} 進度分析", "",
                     f"期間：{start} → {end}",
                     f"本週進度信：{len(mine)} 封", "",
                     "## 判定", "", "狀態：（on-track / at-risk / off-track）", "",
                     "## 證據", ""]
            lines += [f"- {r['date']} {r.get('message_id')}" for r in mine] or ["（本週無來信）"]
            lines += ["", "## 與上週差異", "", "## 建議下一步", ""]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("\n".join(lines), encoding="utf-8", newline="\n")
            print("wrote", dest.relative_to(ROOT))


if __name__ == "__main__":
    main()
