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
Optional `result_format: "junit"` enables [reported test counts and failures](junit.md)
and requires a `{junit}` placeholder in the command (0.5.0+).

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

## Acceptance items (0.5.1+)

Use optional `acceptance` entries to connect a requirement to named checks and the
questions a person must answer. This makes evidence gaps actionable without claiming that
the tool understands business correctness.

```json
{
  "version": 1,
  "checks": [
    {"name": "payment-tests", "kind": "test",
     "command": ["python", "-m", "pytest", "tests/payment", "--junitxml={junit}"],
     "result_format": "junit"}
  ],
  "acceptance": [
    {
      "id": "timeout-no-double-charge",
      "description": "A client timeout does not charge the customer twice",
      "checks": ["payment-tests"],
      "questions": [
        "Does the test cover server-side success followed by a lost client response?",
        "Does the assertion check the number of charges, not only the response code?"
      ]
    }
  ]
}
```

Each acceptance `id` is unique. Every referenced check must exist. An item with no usable
linked evidence is `MISSING_EVIDENCE`; an item whose checks are usable is still
`NEEDS_HUMAN_REVIEW` and lists its questions. It never becomes an automatic approval.
Invalid mappings are rejected before configured commands run. Older configurations without
`acceptance` remain valid.

### Recording the human decision

After inspecting the report, use the acceptance id to record the decision:

```bash
agentproof confirm timeout-no-double-charge --note "Reviewed the diff and test output."
agentproof reject timeout-no-double-charge --reason "Missing the server-success/client-timeout case."
```

`confirm` records a `CONFIRMED` decision and `reject` records `REJECTED`. The note is
optional for confirmation; a rejection reason is required. Each record includes a workspace
fingerprint, the acceptance configuration fingerprint and the linked check receipt identity.
If files, the acceptance mapping, a linked log or structured result changes, or the linked check
is run again, the old decision becomes `STALE_CONFIRMATION`. This requests a new review; it is
not an automatic correctness judgement. The full decision history remains in `task.json` and
the latest decision is shown in `status`, `status --json` and `report`.

### After-the-fact baseline

For a task that began before AgentProof was initialized, use:

```bash
agentproof init --from-commit HEAD~1 --goal "Review the change"
```

The report marks this as `baseline_mode: historical_commit`. It is a useful file comparison
anchor, but it cannot prove what the workspace contained before the AI task or reconstruct the
full interaction history.
