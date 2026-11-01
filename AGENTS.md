# group_discussion

Per-student research tracking for the MEL lab: mail sent to `<redacted-email>` is
ingested into per-student folders, each student gets an LLM-maintained wiki plus agreed
research goals, and progress is analysed weekly against those goals.

Read `GLOSSARY.md` and `docs/adr/` before naming anything or changing the pipeline.

## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<feature-slug>/` in this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Pipeline

Deterministic ingestion (no LLM): `python scripts/ingest.py [--dry-run] [--limit N] [--commit]`

- reads INBOX **readonly** (`BODY.PEEK`, never sets \Seen — 3,800+ unread mails are the PI's todo list)
- files each mail under `students/<slug>/`, extracts attachment text, updates the FTS5 index
- appends un-reviewed items to `index/pending_review.jsonl`
- never decides what a mail *means* — that is the agent step below

Agent step (semantic, one mail at a time): classify each pending item as
`progress` / `logistics` / `noise`, update `students/<slug>/wiki/`, append to `wiki/log.md`,
and record the verdict in `index/reviewed.jsonl` so it is auditable and re-runnable.

Retrieval: `python scripts/search.py "查詢字串" [--slug SLUG] [--kind mail|attachment|wiki]`
