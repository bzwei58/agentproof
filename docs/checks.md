# Reusable project checks (0.4.0+)

Create `agentproof.json` at the Git repository root. Use commands you already trust and
run for that project. Configuration is optional; individual `verify` commands still work.

```json
{
  "version": 1,
  "checks": [
    {
      "name": "unit-tests",
      "kind": "test",
      "command": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"],
      "timeout": 300
    }
  ]
}
```

Each entry requires a unique nonempty `name`, a `kind` (`test`, `lint`, `build`, or
`other`) and a nonempty `command` array. The optional timeout defaults to 300 seconds
and must be positive and finite. Unknown fields and invalid entries are rejected before
any command runs. Executable paths depend on your environment; on Windows an npm
command may require `npm.cmd` rather than `npm`.

The kind is a user declaration. A zero exit status does not establish that tests ran,
how many ran, their coverage, or that the task is correct. Review the actual output.

```bash
agentproof check --list
agentproof init --goal "Describe the task"
# Let your coding agent edit the project.
agentproof review --claim "All tests passed" --claim "Fixed payment retries"
agentproof check
agentproof status --brief
agentproof diff
```

`check --list` works before initialization and never executes configured commands.
`check` validates the complete configuration, then executes its commands sequentially
at the repository root. It generates a summary, a report and a baseline comparison.
The current assessment is also available as `status --json` (`schema_version: 1`).
Unknown additive JSON fields may appear; consumers should check the schema version and
ignore fields they do not use. File lists, commands, goals and claim text may be private.

Commands are argv arrays, not shell text. Pipes, redirects and environment expansion are
not interpreted. If shell behavior is necessary, explicitly configure your shell and
its arguments. Configuration can execute code with your permissions: inspect it with
`check --list`, including after changes from a pull request.

Exit status is 0 if all configured commands returned zero, 1 if a command failed or was
interrupted, and 2 for operational or configuration errors. A check failure does not stop
the remaining configured checks; interruption does. Zero is not an acceptance decision.
Checks that modify captured files invalidate earlier receipts; inspect freshness in the
generated report. Stop concurrent edits when capturing or running checks.

## Continue with the next task

```bash
agentproof finish
agentproof history
agentproof init --goal "Next task"
```

`finish` writes a current report and comparison, copies evidence into a new directory
under `.agentproof/history/`, and only then removes the active evidence. It does not run
checks, modify source files, change staging, or approve correctness. Failed or unverified
tasks may be archived too. Copy failure keeps the active task intact. If cleanup fails
after the archive is complete, retain that archive and inspect the error before retrying.

Archived reports and comparisons are static. The archived `task.json` preserves the
original check records and all submitted claim sets; current reports show only the latest
claim set. Archived evidence cannot currently be resumed or restored through the CLI.
Snapshots and archives duplicate file content; keep capacity in mind. Keep the entire
`.agentproof/` directory private.

## Incomplete runs (0.4.1+)

The latest configured run includes each planned check, including `NOT_RUN` after an
interruption or operational error. An execution receipt is persisted before post-command
capture: a completed command is retained even when files exceed capture limits.
File matching then becomes `UNKNOWN`; it cannot qualify as current successful evidence.
Reports remain available and explain why the baseline comparison could not be generated.
An unfinished receipt with no exit code is `UNKNOWN`, never a passing execution.
On Windows, timeout handling requests tree termination with taskkill; failures are retained
as diagnostics. Escaped or detached processes are outside that guarantee.
