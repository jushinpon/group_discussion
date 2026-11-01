#!/usr/bin/env bash
# Monitor script for the 30-minute cron job.
# Runs one ingestion pass (deterministic, no LLM) and prints a short summary.
# The cron layer compares this output between ticks: identical output => no agent run.
# When new mail is ingested, the counts below change and the agent is woken with the diff.
set -u
cd /e/Hermes_projects/group_discussion || { echo "workspace missing"; exit 1; }
python3 scripts/ingest.py --commit 2>&1 | grep -E "INBOX since|new messages to ingest|^  [a-z_]+:|files written|git commit" | tail -20
echo "pending_unreviewed=$(python3 scripts/pending.py --stats 2>/dev/null | head -1)"
