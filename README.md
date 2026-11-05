# AgentProof

**Evidence for AI coding tasks. Local checkpoints, real command results, honest handoff reports.**

[中文说明](README.zh-CN.md) · [Example report](examples/report.md) · MIT license

AI says “done.” What actually changed? Which checks ran, and do they still match the code?
AgentProof answers those questions without another model or an API key.

```text
$ agentproof init --goal "Fix payment retries"
Task initialized. Baseline: ... (42 files)
$ agentproof verify -- python -m unittest discover -s tests
PASS (exit 0). Log: .agentproof/checks/....log
$ agentproof report
/path/to/project/.agentproof/report.md
```

## Install

Requires Python 3.10+, Git, and macOS or Linux. Windows is not supported in v0.1.
This project is not published to PyPI yet.

```bash
git clone https://github.com/bzwei58/agentproof.git
cd agentproof
python3 -m venv .venv
.venv/bin/python -m pip install -e .
# Use the absolute path to .venv/bin/agentproof from your project.
```

For a global isolated command, use `pipx install .` from this checkout if you have pipx.

## Workflow

Run from any directory inside the Git repository you want to inspect. Commands execute at
that repository's root. One active task is supported per checkout; use a worktree for another task.

1. **Start before AI edits.** `agentproof init --goal "Fix payment retries"` captures initial
   local changes too, without creating commits or touching the Git index.
2. Let your existing coding agent work. AgentProof does not launch or intercept it.
3. **Save a milestone.** `agentproof checkpoint --label "first implementation"`.
4. **Run real checks.** `agentproof verify -- python -m unittest discover -s tests`.
   Run each test, lint or build command separately. Shell operators are not interpreted;
   explicitly use `sh -c '...'` if you need shell syntax. Only run commands you trust.
5. **Optionally import session signals.** `agentproof import-session /path/to/session.jsonl`.
6. **Review.** `agentproof status` prints evidence; `agentproof report` writes Markdown.
7. **Recover selected snapshot contents.** `agentproof restore CHECKPOINT_ID` previews changes;
   add `--apply` to write them, after an automatic backup checkpoint.

`verify --timeout 60 -- COMMAND ...` sets a timeout in seconds (default 300).
A nonzero command result returns CLI exit code 1; usage/operational errors return 2.
Command stdout and stderr are retained in `.agentproof/checks/`. On failure, the terminal
also shows the last 12 lines, capped at 4,000 characters, plus an explicit timeout message
when applicable. Successful commands stay quiet. The full log is always retained locally.

## What is recorded

- SHA-256 fingerprints and modes of captured files, plus full file checkpoints.
- Added, modified and deleted files relative to the task's initial checkpoint.
- Actual verification command, exit status, output log and before/after fingerprints.
- Whether each check still matches the current captured files. Editing code after a check
  or having a check modify files marks it **STALE**.
- Long or multiline commands have compact report summaries; expand **Full command** to
  inspect the exact recorded invocation.
- Optional Claude Code JSONL counts: tool calls, error results, compaction boundaries,
  truncation markers and malformed records. Raw imported messages are not copied.

## Boundaries

This is an **evidence recorder**, not an automatic correctness judge. A passing check does
not prove requirements satisfaction, coverage or absence of bugs. There is no model-based
risk scoring, live session watcher, automatic test discovery or cross-agent correlation yet.
Session import is best-effort for Claude Code-style `message.content` blocks and
`system/compact_boundary` records. Other formats and future schema changes may not be counted.
Truncation text is a heuristic signal, not proof that important context was lost.

Snapshots include Git-tracked and non-ignored untracked regular files. Ignored files,
symlinks, submodule contents, Git index state, external services and environment variables
are outside the captured scope. Known secret filenames (`.env*`, private-key extensions,
`credentials.json`) are excluded and listed; **this is not secret detection**. Verification
logs can contain secrets printed by your own commands. Keep `.agentproof/` private.
Initialization adds a local Git exclusion, and evidence is stored with a private directory mode.

A file over 10 MiB or total captured content over 100 MiB aborts snapshotting. Avoid concurrent
edits while capturing or verifying. Only one AgentProof command can run per checkout.
After an interrupted process, inspect and remove a stale `.agentproof/lock` manually.

Restore rewrites files present in the target snapshot. **Files added later remain**; this is
file recovery, not an exact whole-workspace rollback. It leaves Git history and staging alone.
It rejects symlink paths and verifies snapshot content hashes before writing. Restore does
not recover excluded files or delete newly created ones. A pre-restore checkpoint lets you
recover overwritten captured content. Do not manually edit snapshot manifests or files.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m agentproof --help
```

Tests use disposable Git repositories and cover stale evidence, nonzero exits, timeout,
initial uncommitted edits, restoration backups, symlink rejection, secret exclusions and
partial session logs. GitHub Actions runs them on macOS/Linux and Python 3.10/3.12/3.13.

Ideas for later: task history, more session adapters, configurable snapshot scope, optional
CI handoff artifacts. Contributions should preserve the distinction between observed facts
and model-generated claims. Please include a reproducible example and tests for behavior changes.
