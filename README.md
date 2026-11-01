# group_discussion

Per-student research tracking for the MEL lab. Mail sent to `<redacted-email>`
is ingested, filed per student, and used to maintain one LLM-written wiki per student
plus a retrieval index over the whole corpus.

**Scope (settled 2026-10-31):** the default ingestion window starts **2026-07-01**, but each
student may declare an earlier `window_start` in `students.yaml`. A 博三 student's record must
start at enrolment, not at a four-month cutoff — student-01 (2023-08-01) and student-02 (2024-09-01)
are backfilled that way. Mail older than a student's own window is not ingested, and never
silently: the run prints `skip … (before <slug> window …)` for it.

## Layout

```
students.yaml                  identity table: slug <-> display name <-> sender addresses
students/<slug>/
  wiki/index.md                entry point for that student's wiki
  wiki/*.md                    topic pages (Obsidian-compatible, [[wikilinks]])
  wiki/log.md                  append-only record of wiki changes, one line per run
  attachments/YYYY-MM/         original attachments, deduped by SHA-256
  mail/                        one file per ingested message (headers + body text)
  goals.md                     agreed research goals for that student
index/                         SQLite FTS5 retrieval index
scripts/                       ingestion, wiki-update and report tooling
docs/adr/                      decisions and why
GLOSSARY.md                    domain vocabulary — read before naming anything
```

## Retrieval

`index/` holds a SQLite FTS5 index over mail bodies, extracted attachment text and
wiki pages. Embeddings are deliberately deferred; see `docs/adr/0002-retrieval-fts5-first.md`.

## Reading order for an agent

1. `GLOSSARY.md`
2. `docs/adr/`
3. `students.yaml`
