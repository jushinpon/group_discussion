# Student identity is keyed by slug, with an alias table — never by email address

`students.yaml` is the single identity authority: slug ↔ display name ↔ one or more Sender
addresses ↔ first-seen date. Folders, wiki paths, the retrieval index and git history all
hang off the slug.

A mail-address key was rejected because the same person demonstrably writes from more than
one address (校內 `@mem.nsysu.edu.tw`, 實驗室 `@nsysumel.com`, personal Gmail) and changes
display names; keying on the From address splits one Student into several and orphans their
earlier history. 學號 and 中文姓名 were rejected as keys too — the former is not in any mail
header, the latter is not filename-safe and is ambiguous across cohorts.

## Consequences

- Every unrecognised Sender forces an explicit decision: map it to an existing slug (add an
  alias) or create a new Student. Ingestion must refuse to file mail silently under a guess.
- Renaming a person or adding an address is a `students.yaml` edit, not a filesystem migration.
