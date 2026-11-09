# AgentProof

**Evidence for AI coding tasks. Local checkpoints, real command results, honest handoff reports.**

[中文说明](README.zh-CN.md) · [Five-minute start](docs/quickstart.md) · [Example report](examples/report.md) · [Changelog](CHANGELOG.md) · [Security](SECURITY.md) · MIT license

AI says “done.” What actually changed? Which checks ran, and do they still match the code?
AgentProof answers those questions without another model or an API key.

```text
$ agentproof init --goal "Fix payment retries"
Task initialized. Baseline: ... (42 files)
$ agentproof verify --kind test -- python -m unittest discover -s tests
PASS (exit 0). Log: .agentproof/checks/....log
$ agentproof report
/path/to/project/.agentproof/report.md
```

## Install

Requires Python 3.10+, Git, and macOS, Linux, or Windows.
Available on [PyPI](https://pypi.org/project/agentproof-cli/).

With [pipx installed and on PATH](https://pipx.pypa.io/stable/installation/), install the latest release:

```bash
pipx install agentproof-cli
```

Then run `agentproof --version` from any project. If the command is missing, run
`pipx ensurepath` and reopen your terminal. The package is named `agentproof-cli`, while
the command is `agentproof`; remove it with `pipx uninstall agentproof-cli`.
To try unreleased development changes, install `git+https://github.com/bzwei58/agentproof.git@main` instead.

If you do not use pipx, create a virtual environment and install the same package with its
Python: `python3 -m venv /your/chosen/venv`, then
`/your/chosen/venv/bin/python -m pip install agentproof-cli`.
Use `/your/chosen/venv/bin/agentproof` or activate that environment.

Start with the [five-minute walkthrough](docs/quickstart.md): one failing test, a fix,
a stale check and a report, all in a disposable directory. For a real project, follow the workflow below.

## Workflow

Run from any directory inside the Git repository you want to inspect. Commands execute at
that repository's root. One active task is supported per checkout; archive it with `finish` before the next task, or use a worktree for concurrent tasks.

1. **Start before AI edits.** `agentproof init --goal "Fix payment retries"` captures initial
   local changes too, without creating commits or touching the Git index.
2. Let your existing coding agent work. AgentProof does not launch or intercept it.
3. **Save a milestone.** `agentproof checkpoint --label "first implementation"`.
4. **Run real checks.** `agentproof verify --kind test -- python -m unittest discover -s tests`.
   Run each test, lint or build command separately. Shell operators are not interpreted;
   explicitly use `sh -c '...'` if you need shell syntax. Only run commands you trust.
5. **Optionally import session signals.** `agentproof import-session /path/to/session.jsonl`.
6. **Review AI claims.** `agentproof review --summary ai-summary.txt` (one claim per line), or repeat
   `--claim "All tests passed"`. Completion claims remain `UNVERIFIED`; matching command receipts are shown separately.
7. **Review.** `agentproof status` prints evidence; `agentproof report` writes Markdown.
8. **Recover selected snapshot contents.** `agentproof restore CHECKPOINT_ID` previews changes;
   add `--apply` to write them, after an automatic backup checkpoint.

### Faster repeated use (0.4.0+)

Define explicit project commands in [`agentproof.json`](docs/checks.md), inspect them with
`agentproof check --list`, then run `agentproof check` to execute them and generate a report.
A failed check returns 1 and still produces the report; invalid configuration returns 2
before any configured command runs. Commands run sequentially without shell evaluation.

- `agentproof status --brief`: review priorities and current evidence gaps.
- `agentproof status --json`: machine-readable assessment with `schema_version: 1`.
- `agentproof diff [repository-relative-path]`: compare captured files against the task baseline,
  including initial uncommitted edits. Reports link a local `changes.patch` comparison.
- `agentproof finish`: archive snapshots, logs, latest report and comparison; start the next task with `init`.
- `agentproof history`: list archived tasks and report locations.

Archiving does not approve correctness, alter project files or modify Git staging. Archived
reports are static, private records. The comparison is for human inspection, not `git apply`.
Current reports use the latest claim set; earlier sets remain in task metadata. Run
`pipx upgrade agentproof-cli` to update an existing installation.

`verify --timeout 60 -- COMMAND ...` sets a timeout in seconds (default 300).
A nonzero command result returns CLI exit code 1; usage/operational errors return 2.
Command stdout and stderr are retained in `.agentproof/checks/`. On failure, the terminal
also shows the last 12 lines, capped at 4,000 characters, plus an explicit timeout message
when applicable. Successful commands stay quiet. The full log is always retained locally.

## What is recorded

### Reported test results (0.5.0+)

For a project already using pytest, run:

```bash
agentproof verify --kind test --junit -- python -m pytest '--junitxml={junit}'
```

The report shows reporter-provided test counts, failures, errors and skips, with
bounded failure details. Every execution gets a unique output path. Missing, invalid, inconsistent,
modified, zero-test or skipped-only results need review; a zero command exit does not
override failed test results. [JUnit usage and boundaries](docs/junit.md).

### Captured evidence

- SHA-256 fingerprints and modes of captured files, plus full file checkpoints.
- Added, modified and deleted files relative to the task's initial checkpoint.
- Actual verification command, exit status, output log and before/after fingerprints.
- Whether each check still matches the current captured files. Editing code after a check
  or having a check modify files marks it **STALE**.
- Long or multiline commands have compact report summaries; expand **Full command** to
  inspect the exact recorded invocation.
- Optional Claude Code JSONL counts: tool calls, error results, compaction boundaries,
  truncation markers and malformed records. Raw imported messages are not copied.
- Claim review matches completion statements to local file changes and fresh checks using transparent rules.

## Boundaries

This is an **evidence recorder and claim-gap detector**, not an automatic correctness judge. A passing check does
not prove requirements satisfaction, coverage or absence of bugs. There is no model-based
risk scoring, live session watcher, automatic test discovery or cross-agent correlation yet.
Claim review never calls a model and cannot decide whether a requirement is substantively correct.
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
It rejects symlink paths, verifies snapshot content hashes and preflights file/directory
conflicts, including blocked parent paths, before changing files.
It is not a filesystem transaction; concurrent changes or I/O errors can still interrupt writes.
Restore does not recover excluded files or delete newly created ones. A pre-restore checkpoint lets you
recover overwritten captured content. Do not manually edit snapshot manifests or files.

## Development

```bash
git clone https://github.com/bzwei58/agentproof.git
cd agentproof
python3 -m venv .venv
.venv/bin/python -m pip install -e .
python3 -m unittest discover -s tests -v
python3 -m agentproof --help
```

Want to try it on two real tasks? See the [pilot guide and feedback form](docs/pilot.md).

Tests use disposable Git repositories and cover stale evidence, nonzero exits, timeout,
initial uncommitted edits, restoration backups, symlink rejection, secret exclusions and
partial session logs. GitHub Actions runs them on macOS/Linux/Windows and Python 3.10/3.12/3.13.

Ideas for later: task resume, more session adapters, configurable snapshot scope, optional
CI handoff artifacts. Contributions should preserve the distinction between observed facts
and model-generated claims. Please include a reproducible example and tests for behavior changes.

## Evidence matching in 0.3.1

Declare each check with `verify --kind test|lint|build|other -- COMMAND`. The category is
user-supplied, not a certification. Completion claims stay `UNVERIFIED`, including when
files changed or a declared test command returned zero. Matching command receipts are
listed separately; they do not establish test execution, count, coverage or requirements.

Reports distinguish historical command execution, current captured file matching and log
integrity (`INTACT`, `MODIFIED`, `MISSING`, `UNRECORDED` or `UNREADABLE`). Only successful,
current checks with intact logs can appear as matching receipts. Both `status` and `report`
recompute these facts; exported Markdown remains a static snapshot. Local hashes detect
some changes; they are not independent proof against someone rewriting the evidence store.
Ignored dependencies, external services and environment changes remain outside scope.

Session import recognizes Claude message blocks and Codex `response_item.payload` and
`compacted` events. Native Cline/Cursor formats remain unvalidated.
