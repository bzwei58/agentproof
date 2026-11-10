# Validation status

The integrity fix is covered by disposable-repository regression tests, including an
actual unittest runner, edited code, modified log output, a successful build paired
with a test claim, command comments containing `test`, and Chinese/negated statements.

These are controlled engineering checks, not independent user trials. No measured
reduction in review time is claimed. Earlier toy exercises must not be counted as
real-user adoption or proof of time savings.

For the next pilot, use two comparable real tasks and record:

- the decision made after reviewing the report;
- evidence gaps found and false positives;
- time spent generating/reading the report, separately from test execution;
- which code and outputs still needed manual inspection;
- whether the tool was reused voluntarily.

Use the existing pilot issue template with anonymized results. Never publish private
transcripts, source, logs or `.agentproof/` contents. Native Cline/Cursor session support
still needs documented format samples and dedicated compatibility tests.

## 0.4.0 workflow checks

Disposable-repository tests cover config preview, full validation before execution,
failure receipts, latest-check precedence, baseline diffs, missing logs, sequential task
archival and copy failure preserving active evidence. These checks exercise implementation
behavior; they do not establish user time savings. A structured-result adapter, semantic
acceptance mapping and native client integrations remain future work.

## 0.5.0 JUnit evidence

Controlled checks cover nested/namespaced suites, inconsistent counts, unsupported
status metadata, parser limits, zero/skipped-only cases, missing/modified output, unique
per-run paths, latest-result grouping and archived source XML.

A separate clean wheel installation with a real pytest runner exercised three cases:
one failure plus one pass and one skip, then a fix producing two passes and one skip.
A skipped-only pytest run returned zero while AgentProof returned 1 for incomplete test
evidence. XML tampering, archival and a second task were also checked. This is an
engineering smoke test on a disposable project, not independent user validation or a
measurement of human review time.

A full-project pytest run exposed a suite total differing from its testcase record
count when subtests were reported. Both values are now retained as INCONSISTENT
evidence, never treated as a complete successful test report. The discrepancy is
displayed for review rather than silently normalized.

## 0.5.1 evidence levels and acceptance mapping

Controlled tests cover evidence levels, actionable next steps, valid acceptance mappings,
missing linked checks and human-question output. These features organize evidence; they do
not decide whether a requirement is substantively satisfied. Independent user validation
and measured review-time impact remain outstanding.

## 0.8.0 human review workflow

The repository's own 0.8.0 development is the first maintainer-owned real AI coding task
for this workflow. Engineering observations are distinct from a human review outcome:
review time, perceived effort and acceptance remain pending until the reviewer supplies them.

Controlled regression checks cover missing/failed/stale confirmation evidence, log and XML
tampering, changed configuration, transactional resume failure and retry, symlink refusal,
file-diff links and outcome inputs. The suite has 89 tests: on macOS 88 pass and the Windows
process-tree regression is skipped. Source distributions include the shared test helpers;
tests also pass after extracting the sdist. A clean wheel install exercises checks,
confirmation, archival, resume and report generation in a disposable repository.

The PR #18 Actions report was downloaded and inspected: it used the base commit, contained
actual changed files and intact current check logs, and every relative Markdown evidence link
resolved inside the artifact. Both acceptance items remained NEEDS_HUMAN_REVIEW. These
observations establish the delivery mechanics; they do not measure review-time savings.

## 0.8.1 report readability

The real task reviewer could not tell what they were expected to review from the 0.8.0 handoff.
That is recorded as a usability problem, with the overall decision deferred and no invented
reading duration. The revised report leads with the requested task and observed file changes,
check results and specific human questions. Technical evidence remains available in a folded
section; missing or stale evidence is not hidden by the new wording.

Regression checks cover overview/evidence ordering, questions appearing once, Chinese output,
remembered versus temporary language choices, and invalidated confirmations or missing logs
remaining visible in plain language. These checks establish report behavior, not that the new
wording has solved the reviewer's comprehension problem. That still needs their feedback.
