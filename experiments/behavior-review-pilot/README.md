# Historical behavior-verification pilot

This is an experiment, not a new AgentProof feature or product release. It compares existing
checks, a direct text-only AI review, and AI-proposed checks that are actually executed.

The immediate question is whether execution adds a reproducible finding or makes a static
finding easier to understand. It does not establish reduced human review time.

Read the [Chinese results and current limitations](RESULTS.zh-CN.md) before interpreting the experiment.

## Cases and limitations

Three cases are frozen from actual repository history: a restore implementation with a known
path-validation defect, its corrected counterpart as a scoped negative control, and the 0.8.1
report-comprehension task without an objective human-comprehension criterion.

The operator selected the cases and already knows the defect. The model inputs omit case
labels, revisions, commit messages, later fixes and the evaluator's known-answer replay. The
historical task contract was scoped retrospectively from the restore guarantee documented in
the fix; it was not prospectively registered before the original implementation. Both restore
candidates use the same test suite from the pre-fix commit. The current report candidate uses
its current tests. This is a tiny retrospective snapshot audit, not a representative benchmark
of live pull-request reviews, and it cannot support discovery-rate or false-positive estimates.

Each model arm receives the same frozen case packet: a requirement, candidate source and
available tests (relevant excerpts for the larger report case). The direct-review arm requests
static findings. The probe arm requests candidate Python tests or a specific clarification.
Both use the configured default Claude model through tool-free print calls, a fresh context,
the same response-length instruction and per-call cost ceiling. Actual model identity, tokens,
cost and elapsed time are recorded; they are not assumed equal. Single trials provide no
reliability estimate. Two model calls are used to compare methods, not as a proposed two-call
production design.

Model-generated probes are inspected before execution and run against isolated temporary
repositories using `probe_support.py`. No production implementation is changed. A temporary
repository is not an OS security sandbox. The model's code is not automatically executed by
the inference phase. Invalid test programs are recorded as invalid, not repaired silently or
counted as implementation defects. A matching failure against the corrected implementation
must be examined for a wrong assertion or a genuine additional finding.

The manually written `replay.py` is a known-answer adjudication tool. It must never be counted
as a model-generated discovery. The ambiguous case must retain uncertainty: passing template
or heading checks cannot establish whether a person understands the report.

## Reproduce

Run from the repository root with Python 3.10+ and Git. Model comparison additionally requires
an authenticated Claude CLI; inference uses its configured model without altering settings.
Only public repository source is supplied. Raw outputs remain under the ignored evidence
folder and are not included in the package.

```bash
python experiments/behavior-review-pilot/prepare.py .agentproof/experiments/behavior-review-pilot
python experiments/behavior-review-pilot/run.py baseline .agentproof/experiments/behavior-review-pilot
python experiments/behavior-review-pilot/run.py models .agentproof/experiments/behavior-review-pilot
# Inspect each generated_probe.py before running:
# Save probe.inspection.json beside it, containing {"source_sha256": "<reviewed file SHA256>"}.
python experiments/behavior-review-pilot/run.py probes .agentproof/experiments/behavior-review-pilot
# Replay an inspected generated suite unchanged on the fixed control:
python experiments/behavior-review-pilot/run.py probes .agentproof/experiments/behavior-review-pilot --case R2 --target K4
python experiments/behavior-review-pilot/export.py .agentproof/experiments/behavior-review-pilot experiments/behavior-review-pilot/evidence
```

Preparation refuses to overwrite a prior run. Use another output folder for a new trial.
A successful inference answer is frozen and not resampled. Infrastructure failures remain
recorded and may be retried with the identical packet; disclose those retries. No finding is
inferred when a provider is unavailable. The private manifest records revisions and hashes;
those labels are never passed to the inference process.

The initial run used a 180-second per-call timeout. After infrastructure timeouts it was
extended to 360 seconds; subsequent isolated retries may use `--model-timeout 600 --case R2`
(or another case). This changes the wait limit, not the frozen prompt or the requested model.
Successful answers and invalid returned answers are terminal and never resampled. A malformed
JSON response is preserved verbatim and is not silently repaired. The CLI-reported model ID,
not the CLI brand, identifies the model used. Cost receipts with an unknown pricing basis
are provider-reported figures, not independently verified charges.

## Decision rule

Proceed only if executable verification adds a concrete, correctly attributed result that
existing checks missed, or turns a static suspicion into useful reproducible evidence. If
static review already finds the issue, report that honestly: the benefit is confirmation and
reproduction, not discovery. A clean control and the ambiguous case must not be mislabeled to
make the experiment look successful. Human comprehension and actual review burden require
real user feedback afterwards; unknown durations remain unknown.
