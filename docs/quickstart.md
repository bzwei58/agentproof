# Your first report in five minutes

[中文](quickstart.zh-CN.md) · [Install](../README.md#install) · [Try it on real tasks](pilot.md)

Have `agentproof`, Python 3.10+ and Git available in your terminal. This walkthrough creates
an intentionally broken toy project in a new temporary directory. It demonstrates the
workflow; it is not evidence that AgentProof finds bugs automatically.
Run the blocks in order in the **same terminal**, without enabling `set -e` (one check is meant to fail).

## 1. Create a disposable project

```bash
AGENTPROOF_DEMO=$(mktemp -d "${TMPDIR:-/tmp}/agentproof-demo.XXXXXX")
cd "$AGENTPROOF_DEMO"
git init -q
printf '__pycache__/\n' > .gitignore
cat > price.py <<'PY'
def total_price(unit_price, quantity):
    return unit_price + quantity
PY
cat > test_price.py <<'PY'
import unittest
from price import total_price

class PriceTest(unittest.TestCase):
    def test_three_items(self):
        self.assertEqual(total_price(12, 3), 36)
PY
```

The `.gitignore` keeps Python cache files outside captured evidence. In real projects,
ignore generated build/cache output using the project's normal ignore rules.

## 2. Start and record a failure

```bash
agentproof init --goal "Correct the total price for multiple items"
agentproof verify --kind test -- python3 -B -m unittest discover -v
```

Expect **FAIL (exit 1)** and an assertion showing `15 != 36`. The command log is retained.
This failure is intentional; continue to the next step.

## 3. Fix the code and verify it

```bash
cat > price.py <<'PY'
def total_price(unit_price, quantity):
    return unit_price * quantity
PY
agentproof checkpoint --label "Correct quantity calculation"
agentproof verify --kind test -- python3 -B -m unittest discover -v
```

Expect **PASS (exit 0)**. There is one real test, not an empty test discovery run.
Save the printed checkpoint ID if you want to try a restore preview later.

## 4. See why an old pass is not enough

```bash
printf '\n# Temporary review note\n' >> price.py
agentproof report
agentproof status
```

The earlier passing check now says **STALE / files changed**. Even a comment changes the
captured file fingerprint. AgentProof does not decide whether that edit affects behavior.

## 5. Verify the current files and read the report

```bash
agentproof verify --kind test -- python3 -B -m unittest discover -v
agentproof report
printf 'Report: %s/.agentproof/report.md\n' "$PWD"
```

Open the printed report path. The newest pass must say **matches current captured files**;
the earlier checks remain in the history. Logs and snapshots live in `.agentproof/` and are
excluded from Git. To preview recovery, run `agentproof restore` followed by the checkpoint
ID you saved. Preview does not write files. `--apply` would write files after an automatic backup;
files added later remain, so this is not an exact whole-workspace rollback.

## Use it on a real task

In a fresh checkout/worktree, run `agentproof init --goal "your task"` **before** AI edits.
After the agent finishes, run `agentproof verify --kind test -- YOUR_EXISTING_TEST_COMMAND` and generate
a report. Choose the project's actual tests/build/lint commands; AgentProof does not discover
them for you. Read the recorded logs and the code diff before accepting the change.

One task is supported per checkout. See the [pilot guide](pilot.md) for two-task feedback.
If you see `command not found`, finish pipx's PATH setup and reopen your terminal. If an init
fails because of the 10 MiB/file or 100 MiB total limit, use a smaller project/worktree;
Git-tracked files remain in scope even if added to `.gitignore` later.
