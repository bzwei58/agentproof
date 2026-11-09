# Reported test results (0.5.0+)

JUnit XML adds reported test counts and failure locations to a command receipt. It does
not certify that a reporter is truthful, that tests cover requirements, or that code is
correct. AgentProof does not install a test runner or discover the right tests for you.

For a project that already uses pytest:

```bash
agentproof verify --kind test --junit -- python -m pytest '--junitxml={junit}'
agentproof status --brief
```

AgentProof replaces `{junit}` with a new absolute path under `.agentproof/results/` for
each execution. The runner must write its report to that path. The file must not already
exist. An unrelated old XML file does not fill a missing output. A fresh path prevents
accidental report reuse; it does not prevent a command from deliberately fabricating or
copying results. Commands are executed as argv arrays; no shell expansion is implied.

For repeated use, add `result_format` to a configured check:

```json
{
  "version": 1,
  "checks": [
    {
      "name": "unit-tests",
      "kind": "test",
      "result_format": "junit",
      "command": ["python", "-m", "pytest", "--junitxml={junit}"],
      "timeout": 300
    }
  ]
}
```

Preview with `agentproof check --list`, then run `agentproof check`. The configuration
and direct command must use both the JUnit option and placeholder together. Invalid
configuration is rejected before executing commands. Existing checks without JUnit
continue to record command results and do not acquire test counts automatically.

## What the report means

- Reported total, passed, failures, errors and skipped counts are derived from testcase
  records. Nested suites are counted once. Declared suite totals are compared with the testcase records; differences are retained as INCONSISTENT evidence.
- Failure details include the reported case name, class/file, line and message, where
  available. At most 20 problem records are retained, with an explicit truncation flag.
- `VALID` means the XML was parsed consistently. File hashes and size are rechecked;
  `MODIFIED`, `MISSING` or `UNREADABLE` result files cannot qualify as usable evidence.
  `PENDING` means processing did not finish. Malformed/unsupported XML is `INVALID`.
  `INCONSISTENT` preserves both declared totals and derived case-record counts, and
  cannot qualify as usable evidence. Some pytest subtest reports use different counting
  scopes; inspect the reporter rather than assuming these totals are interchangeable.
- A requested report that is missing/invalid/inconsistent, reports failures/errors, reports zero
  cases, or reports only skipped cases makes the CLI return 1 even when the command
  returned 0. Historical command exit and reported test outcomes remain separate.
  Partial skips are shown for review; they do not by themselves change a successful exit.
- File capture errors still return 2. JUnit results are retained where possible, but
  file matching is UNKNOWN and cannot be treated as current successful evidence.
- Completion claims stay UNVERIFIED. Matching JUnit receipts explain the reported
  counts; requirements and coverage still need human review.

The parser accepts UTF-8 `testsuite`/`testsuites` documents, including XML namespaces.
Failure, error and skipped testcase children determine outcomes; common notrun/disabled
metadata is treated conservatively. Contradictory or unsupported status metadata is
rejected. DTDs/entities are rejected. Limits are 8 MiB, 50,000 elements and nesting depth
32. Other result formats and JUnit dialects may require a dedicated adapter.

`finish` archives source XML with logs, snapshots and reports. These files may contain
private paths, test names and failure output. Keep `.agentproof/` private. Archived
reports are static. The JSON assessment retains `schema_version: 1`; consumers should
treat unfamiliar states as unknown and never infer success from them.
