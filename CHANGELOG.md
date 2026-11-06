# Changelog

All notable changes to AgentProof are documented here.

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

## 0.1.0

Initial public development release with checkpoints, command verification, session signal
import, stale evidence detection, reports, and safe snapshot restore.
