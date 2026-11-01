# The PI's mail feeds students' goals; the PI does not get a wiki

Mail from Pon (`<redacted-email>`, alias `<redacted-email>`) is not treated as a
Student. It is extracted as **PI instruction** and written into the affected students'
`goals.md` revision history and wiki pages, attributed by `Message-ID`.

Rejected: giving the PI a ninth wiki. Pon is the authority that goals are measured against
(ADR-0004), so a PI wiki would create two authors of the same goal set and invite the agent to
"average" the PI's corrections with the student's claims.

## Consequences

- One PI mail can touch several students at once (group-meeting replies); the ingestion run
  must attribute per-student rather than file the mail once.
- PI instructions are the highest-authority goal input, so they must stay quotable: the exact
  sentence and its `Message-ID` go into `goals.md`, not a paraphrase alone.
- If Pon ever wants his own view of the group (cross-student), it is generated as a report,
  not maintained as a wiki.
