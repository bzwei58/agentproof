# Contributing to AgentProof

Thanks for helping improve AgentProof. The project is intentionally local-first and evidence-oriented: changes should make observed facts easier to inspect without pretending to prove correctness.

## Before opening a pull request

1. Open an issue for a new behavior or compatibility change when the scope is unclear.
2. Keep changes focused and explain the user problem in the pull request description.
3. Add or update a regression test for behavior changes.
4. Run `python -m unittest discover -s tests -v` locally.
5. Run `python -m pip wheel . --no-deps --wheel-dir dist` for packaging changes.
6. Never include `.agentproof/`, project source, credentials, or command logs from a private project.

## Branches

Use short, descriptive branch names based on the work:

- `feature/<short-name>` for new behavior
- `fix/<short-name>` for bug fixes
- `docs/<short-name>` for documentation and maintenance
- `release/<version>` for release preparation

The default branch is `main`. Work is merged through pull requests; do not push directly to `main`.

## Evidence and privacy

AgentProof can record source file fingerprints and command output. Tests must use disposable repositories and must not depend on a user's home directory, credentials, network service, or private project files. See [SECURITY.md](SECURITY.md) before reporting a vulnerability.

## Pull requests

A pull request should state:

- the concrete user problem;
- the behavior before and after the change;
- the tests and platform checks run;
- any compatibility or evidence limitations.

Reviewers should reject claims that are not backed by a reproducible test or a clearly documented observation.
