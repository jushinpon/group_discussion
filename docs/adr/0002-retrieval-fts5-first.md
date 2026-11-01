# Retrieval starts with SQLite FTS5; embeddings are deferred

The corpus is queried through a SQLite FTS5 index (Chinese-capable tokenisation: FTS5
`trigram`, or a jieba-segmented token column — verify which is available at implementation
time) rather than embeddings-as-first-move. Retrieval is keyword/BM25 over mail bodies,
extracted attachment text and wiki pages.

Reasons: the host has no Ollama server running and no `sentence-transformers`/`faiss`
installed (checked 2026-10-31); the corpus is largely Chinese research mail and PDFs whose
most valuable query terms are proper nouns — 人名、材料名、方法名、儀器型號 — where keyword
matching is strong; and embedding quality for Traditional-Chinese technical text is exactly
the thing that should be validated against real questions before paying its install and
maintenance cost.

## Consequences

- Synonym and paraphrase queries will under-recall; the agent must compensate with query
  expansion (synonyms, English/Chinese term pairs) listed in `GLOSSARY.md`.
- **Upgrade trigger:** if, after several weeks of real questions, recall is insufficient on
  paraphrased questions while exact-term questions succeed, move to local embeddings or the
  existing vLLM embedding endpoint. The index schema keeps page/record identity stable so the
  retrieval layer can be swapped without re-organising the corpus.
- This is an accepted trade-off, not a permanent verdict — see the upgrade trigger above.
