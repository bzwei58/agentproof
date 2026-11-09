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
