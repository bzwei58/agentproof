# Changelog

All notable changes to AgentProof are documented here.

## 0.6.0 — 2026-11-10

- Add explicit `confirm` and `reject` commands for human acceptance decisions.
- Bind decisions to the captured workspace, acceptance configuration and linked check receipts.
- Mark decisions as `STALE_CONFIRMATION` after file edits, log tampering, configuration changes or check reruns.
- Preserve human notes and rejection reasons in JSON and Markdown reports.

## 0.5.1 — 2026-11-10

- Add explicit evidence levels: `NO_EVIDENCE`, `PARTIAL_EVIDENCE` and `EVIDENCE_NEEDS_REVIEW`.
- Add actionable next steps to claim reviews and the review-first report.
- Add optional `acceptance` mappings in `agentproof.json` to connect requirements, checks and human questions.
- Report acceptance items as `MISSING_EVIDENCE` or `NEEDS_HUMAN_REVIEW` without certifying semantic correctness.

## 0.5.0 — 2026-11-09

- Read bounded UTF-8 JUnit XML via a unique per-execution `{junit}` command placeholder.
- Show reported test counts, failures, errors, skips and bounded failure locations in review priorities.
- Reject malformed reports and expose inconsistent suite/case totals; zero-test, skipped-only and failing reports return 1 even with command exit 0.
- Recheck result-file integrity and revoke usable evidence for modified or missing XML.
- Support `verify --junit` and configured `result_format: junit`; preserve source XML in archives.
- Keep natural-language claims UNVERIFIED and clearly label parsed results as reporter-provided.

## 0.4.1 — 2026-11-09

- Persist pending and completed execution receipts before fallible workspace capture.
- Keep reports available with UNKNOWN file matching when capture exceeds limits or fails.
- Save full configured run plans and list interrupted, errored and unexecuted checks.
- Request Windows process-tree termination and preserve timeout/termination diagnostics.

## 0.4.0 — 2026-11-09

- Put actionable review priorities first, with separate execution, freshness and log integrity states.
- Add `status --brief` and versioned `status --json` for summaries and integrations.
- Add captured-baseline `diff` and a local comparison artifact linked from reports.
- Add explicit reusable `agentproof.json` checks, preview with `check --list`, and run with `check`.
- Add `finish` and `history` so evidence is archived before starting another task in the same checkout.
- Display only the latest claim set in current reports while retaining prior sets in task metadata.
- Validate positive finite timeouts before executing checks.

## 0.3.1 — 2026-11-09

- Keep completion claims UNVERIFIED; show command receipts separately from claim status.
- Never treat unrelated captured file changes as evidence of a fix.
- Show historical execution, workspace freshness and log integrity independently.
- Reuse the same integrity rules in reports and claim matching, with bounded-memory log hashing.

## 0.3.0 — 2026-11-09

- Remove natural-language VERIFIED claims; show only scoped observed command receipts.
- Require explicit check categories; never infer verification from command keywords.
- Recompute saved reviews and reject missing or modified logs.
- Recognize Codex nested tool events and clarify unvalidated client formats.
- Validate release tag against package metadata before PyPI publishing.

- Add Windows process timeout handling and Windows CI coverage.
- Add top-level tool event counting for common non-Claude session JSONL formats.
- Add security and privacy guidance for local evidence and command execution.

## 0.2.0 — 2026-11-06

### Added

- Claim review with `agentproof review --summary FILE` and repeated `--claim` options.
- Transparent `VERIFIED`, `OBSERVED`, `UNVERIFIED`, and `CONFLICT` outcomes.
- Human confirmation questions included in the Markdown report.
- Pilot feedback issue template for two real AI coding tasks.

### Changed

- Verification claims only use fresh successful commands that look like tests, builds,
  lint, formatting, or checks; arbitrary successful commands are not treated as test evidence.
- Deleted files are included when determining whether a task changed the workspace.

### Limits

AgentProof records local evidence and highlights evidence gaps. It does not prove that a
requirement is correct, discover tests automatically, or judge production behavior.

### Publishing

The repository now contains a GitHub Actions workflow for PyPI publishing through
Trusted Publishing. The package is not advertised as available on PyPI until a release
has completed that workflow successfully.

## 0.1.0

Initial public development release with checkpoints, command verification, session signal
import, stale evidence detection, reports, and safe snapshot restore.
