# Assistant preparation audit — 2026-11-04

## Verified repository state

- GitHub main: `63f5e7c1b7724648dd01c59abd5ebe8202d3fc9d`.
- PR #2 was open and mergeable at inspection; its reasoning/catalog work was not in main.
- Polling, human approval gateway, manual-answer observations, retry leases/backoff,
  explicit quota handling, versioned catalog and SQLite backups already exist.
- No quotation engine exists in the inspected remote branches (main,
  fix/assistant-production-recovery, fix/assistant-catalog-reasoning). A previously
  mentioned local feat/custom-quotation-engine branch must be recovered separately.
- The portal required sign-in. Production commit, current database counts,
  actual OpenAI balance/connectivity and current worker errors remain UNVERIFIED.

## Changes included with PR #2

- All 68 confirmed catalog alternatives available to generation, without inferring
  an unlinked listing's identity, availability or price.
- Derived bin geometry with explicit assumptions, tight-fit versus roomier options.
- Additive buyer/answer timestamp columns; no removal of questions or decisions.
- Up to six verified prior turns for the same buyer AND listing, excluding answers
  published after the current question. Unknown identities are not joined.
  Existing records acquire identity metadata when refreshed from the API.
- Historical evaluator masks target/future answers in disposable memory snapshots.
  Current catalog is deliberately retained: this measures current behavior, not
  a reconstruction of the historical model. Unverifiable dates are skipped.
- Authenticated diagnostic status and explicit OpenAI connection probe in the
  existing Assistants page. Probe performs no buyer reply, Telegram alert or stock
  change; actual API usage can incur the normal provider cost. Successful probe
  clears the AI pause and releases relevant blocked drafts for normal retry.
- CI includes learning, manual replies, reasoning, conversations and diagnostics.

## Publication constraint

Automatic publication remains unimplemented and disabled. Existing per-answer
human approval still uses its existing environment gate and live status check.
No approval to enable automatic publication has been given.

## Required production acceptance work

1. Sign into portal/Railway; verify deployed commit and make a consistent backup.
2. Integrate the reviewed changes and verify the exact deployed commit, database
   integrity, record counts, pending drafts and worker heartbeat.
3. Run the explicit OpenAI probe; inspect a genuine generated draft and usage.
4. Refresh questions to populate buyer identity and answer timestamps where API
   supplies them. Do not join unknown buyers based on names or similar text.
5. Recover quotation-engine source; validate fiscal, collection, shipping and
   packaging inputs before enabling quotes.
6. Evaluate representative questions with target/future answers excluded; human
   review must measure factual correctness, useful recommendations and sales tone.
   No model self-confidence score is proof of correctness.
7. Add sourced external research and answer verification, then evaluate failures,
   duplicates, timeouts and uncertain delivery. Web research is NOT implemented here.
8. Collect per-category human-reviewed evidence before requesting explicit
   approval for any automatic publishing pilot. Ten days is a target, not an
   automatic activation date. No scheduled activation task has been created.

## Validation

150 selected unit/integration tests passed locally, including management/cloud,
assistant generation, human publication, learning, reasoning and diagnostics.
Mocked provider tests do not verify the production OpenAI account or model quality.
