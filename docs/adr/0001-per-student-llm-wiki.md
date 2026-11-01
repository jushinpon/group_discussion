# Per-student wiki is Obsidian-compatible markdown, kept outside the Obsidian vault

Each Student gets `students/<slug>/wiki/` — an Obsidian-compatible markdown sub-vault
(`index.md`, topic pages, `[[wikilinks]]`, plus an append-only `log.md`). The agent patches
existing pages incrementally instead of regenerating them, and every change is committed.

It is deliberately **not** placed inside the Pon's Obsidian vault
(`Obsidian_Ai_related`). The vault is a curated knowledge hub written only after preview and
explicit confirmation; this wiki is machine-written at every ingestion run, will reach
thousands of edits, and would pollute the hub's structure and its write rules.

## Considered options

- **One long `wiki.md` per student** — rejected: grows unmaintainable, no addressing of individual topics.
- **JSON / database only** — rejected: Pon cannot read or hand-edit it, which is a stated requirement.
- **Write straight into the vault** — rejected: contradicts the vault's confirmation rule, and conflates draft research notes with settled knowledge.

## Consequences

- Wiki pages must remain valid standalone markdown that Obsidian can open, but there is no
  sync back into the vault; promoting a conclusion into the vault stays a manual, confirmed step.
- Git history on the wiki becomes the audit trail of what the agent changed and when.
