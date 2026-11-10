"""Dependency-free local task evidence. Never interprets model claims as proof."""
import argparse
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from . import __version__
from .checks import configured_acceptance, configured_checks
from .junit import parse_junit
from .presentation import summary

STORE = ".agentproof"
MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024


def terminate_process_group(proc):
    """Terminate a timed-out command on POSIX and Windows."""
    if os.name == "nt":
        try:
            result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                    capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            proc.kill()
            proc.wait()
            raise OSError("Process-tree termination unavailable; parent terminated only") from exc
        if result.returncode and proc.poll() is None:
            proc.kill()
            proc.wait()
            raise OSError("Windows process-tree termination failed; parent terminated only")
    else:
        import signal
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def safe_path(root, name):
    p = Path(name)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] in (".git", STORE):
        raise ValueError(f"Unsafe snapshot path: {name}")
    target = root / p
    # Reject symlinks in every component, including the file itself.
    if any(x.is_symlink() for x in [target, *target.parents] if x != root.parent):
        raise ValueError(f"Symlink path is not supported: {name}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes repository: {name}")
    return target


def sensitive(name):
    p = Path(name)
    return (p.name == ".env" or p.name.startswith(".env.") or
            p.suffix.lower() in (".pem", ".key", ".p12", ".pfx") or
            p.name in ("id_rsa", "id_ed25519", "credentials.json"))


def inventory(root):
    names = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    files, omitted, total = {}, [], 0
    for name in sorted(set(os.fsdecode(x) for x in names.split(b"\0") if x)):
        if Path(name).parts[0] == STORE:
            continue
        if sensitive(name):
            omitted.append({"path": name, "reason": "sensitive filename"})
            continue
        try:
            path = safe_path(root, name)
        except ValueError:
            omitted.append({"path": name, "reason": "symlink or unsafe path"})
            continue
        if not path.exists():
            continue
        if not path.is_file():
            omitted.append({"path": name, "reason": "not a regular file (possibly submodule)"})
            continue
        if path.stat().st_size > MAX_FILE:
            raise ValueError(f"File exceeds 10 MiB snapshot limit: {name}")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL:
            raise ValueError("Workspace exceeds 100 MiB snapshot limit")
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(),
                       "mode": path.stat().st_mode & 0o777}
    return files, omitted


def fingerprint(files, omitted):
    return hashlib.sha256(json.dumps([files, omitted], sort_keys=True).encode()).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_path(root, name):
    relative = Path(name)
    if not relative.parts or relative.parts[0] != STORE:
        raise ValueError("File is outside evidence directory")
    return safe_path(root / STORE, str(Path(*relative.parts[1:])))


def junit_evidence(root, check):
    stored = check.get("test_results")
    if stored is None:
        return None
    result = dict(stored)
    if stored["status"] in {"VALID", "INCONSISTENT"}:
        try:
            path = evidence_path(root, stored["path"])
            if path.stat().st_size != stored["size"] or file_digest(path) != stored["sha256"]:
                result["status"] = "MODIFIED"
        except FileNotFoundError:
            result["status"] = "MISSING"
        except (OSError, ValueError):
            result["status"] = "UNREADABLE"
    return result


def usable_test_results(result):
    if result is None:
        return True  # No structured test evidence was requested.
    return (result["status"] == "VALID" and result["counts"]["tests"] > result["counts"]["skipped"]
            and result["counts"]["failures"] == result["counts"]["errors"] == 0)


def check_evidence(root, check, current_fp):
    """Keep historical execution, current file scope, and log integrity separate."""
    known = current_fp is not None and check.get("before") is not None and check.get("after") is not None
    fresh = known and check["before"] == check["after"] == current_fp
    integrity = "UNRECORDED"
    if check.get("log_sha256"):
        try:
            log = evidence_path(root, check["log"])
            integrity = "INTACT" if file_digest(log) == check["log_sha256"] else "MODIFIED"
        except FileNotFoundError:
            integrity = "MISSING"
        except (OSError, ValueError):
            integrity = "UNREADABLE"
    execution = "UNKNOWN" if check.get("exit_code") is None else "PASS" if check["exit_code"] == 0 else "FAIL"
    if check.get("timed_out"):
        execution = "TIMEOUT"
    if check.get("interrupted") or check.get("exit_code") == 130:
        execution = "INTERRUPTED"
    tests = junit_evidence(root, check)
    return {"execution": execution, "workspace": "UNKNOWN" if not known else "CURRENT" if fresh else "STALE",
            "log_integrity": integrity,
            "capture_error": check.get("capture_error"),
            "termination_error": check.get("termination_error"),
            "test_results": tests,
            "usable": (execution == "PASS" and fresh and integrity == "INTACT"
                       and not check.get("capture_error") and usable_test_results(tests))}


def checkpoint(root, state, label):
    files, omitted = inventory(root)
    ident = uuid.uuid4().hex[:12]
    directory = root / STORE / "snapshots" / ident
    directory.mkdir(parents=True)
    for name, info in files.items():
        data = safe_path(root, name).read_bytes()
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise ValueError("Workspace changed during snapshot; retry when edits stop")
        dest = directory / "files" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    snap = {"id": ident, "label": label, "time": now(), "files": files,
            "omitted": omitted, "fingerprint": fingerprint(files, omitted)}
    save(directory / "manifest.json", snap)
    state["checkpoints"].append({k: snap[k] for k in ("id", "label", "time")})
    save(root / STORE / "task.json", state)
    return snap


def checkpoint_from_commit(root, state, commit, label):
    """Capture a baseline from a Git commit for a task started after the fact."""
    resolved = git(root, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    entries = git(root, "ls-tree", "-r", "-z", "--full-tree", resolved)
    files, omitted, total = {}, [], 0
    for raw in entries.split(b"\0"):
        if not raw:
            continue
        header, name_bytes = raw.split(b"\t", 1)
        mode, kind, _object = header.decode("ascii").split(" ", 2)
        name = os.fsdecode(name_bytes)
        if kind != "blob" or mode not in {"100644", "100755"}:
            omitted.append({"path": name, "reason": "not a regular file in commit"})
            continue
        if sensitive(name):
            omitted.append({"path": name, "reason": "sensitive filename"})
            continue
        safe_path(root, name)
        data = git(root, "show", f"{resolved}:{name}")
        if len(data) > MAX_FILE:
            raise ValueError(f"File exceeds 10 MiB snapshot limit: {name}")
        total += len(data)
        if total > MAX_TOTAL:
            raise ValueError("Workspace exceeds 100 MiB snapshot limit")
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "mode": int(mode, 8) & 0o777}
    ident = uuid.uuid4().hex[:12]
    directory = root / STORE / "snapshots" / ident
    directory.mkdir(parents=True)
    for name in files:
        data = git(root, "show", f"{resolved}:{name}")
        dest = directory / "files" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    snap = {"id": ident, "label": label, "time": now(), "files": files,
            "omitted": omitted, "fingerprint": fingerprint(files, omitted),
            "baseline_mode": "historical_commit", "commit": resolved}
    save(directory / "manifest.json", snap)
    state["checkpoints"].append({k: snap[k] for k in ("id", "label", "time")})
    save(root / STORE / "task.json", state)
    return snap


def get_snapshot(root, state, ident):
    if ident not in {x["id"] for x in state["checkpoints"]}:
        raise ValueError("Unknown checkpoint ID")
    return load(root / STORE / "snapshots" / ident / "manifest.json")


def import_session(path):
    metrics = {"records": 0, "malformed_lines": 0, "tool_calls": 0,
               "tool_errors": 0, "compactions": 0, "truncation_markers": 0}
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                metrics["malformed_lines"] += 1
                continue
            if not isinstance(record, dict):
                metrics["malformed_lines"] += 1
                continue
            metrics["records"] += 1
            if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
                metrics["compactions"] += 1
            # Codex rollout response items wrap API events in payload.
            payload = record.get("payload")
            if record.get("type") == "response_item" and isinstance(payload, dict):
                if payload.get("type") in {"function_call", "custom_tool_call"}:
                    metrics["tool_calls"] += 1
                elif payload.get("type") in {"function_call_output", "custom_tool_call_output"}:
                    output = payload.get("output", "")
                    # Do not infer errors from prose; rollout output is opaque.
                    metrics["tool_errors"] += bool(payload.get("is_error"))
                    if re.search(r"\btruncated\b", str(output), re.I):
                        metrics["truncation_markers"] += 1
            if record.get("type") == "compacted":
                metrics["compactions"] += 1
            message = record.get("message")
            content = message.get("content", []) if isinstance(message, dict) else []
            if not isinstance(content, list):
                content = []
            # Codex, Cline and other clients often store tool events as top-level
            # records instead of Claude's message.content blocks. Count only the
            # event metadata; raw prompts and outputs are never persisted.
            top_level_type = str(record.get("type", "")).lower()
            if top_level_type in {"tool_use", "tool_call", "function_call"}:
                metrics["tool_calls"] += 1
            if top_level_type in {"tool_result", "tool_output", "function_result"}:
                metrics["tool_errors"] += bool(record.get("is_error") or record.get("error"))
                if re.search(r"\btruncated\b", json.dumps(record, ensure_ascii=False), re.I):
                    metrics["truncation_markers"] += 1
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    metrics["tool_calls"] += 1
                if block.get("type") == "tool_result":
                    metrics["tool_errors"] += bool(block.get("is_error"))
                    if re.search(r"\btruncated\b", json.dumps(block.get("content", "")), re.I):
                        metrics["truncation_markers"] += 1
    return metrics


def latest_check_records(checks):
    latest = {}
    for check in checks:
        key = check.get("name") or (check.get("command_template", check["command"]), check.get("kind", "other"))
        latest[key] = check
    return list(latest.values())


def acceptance_assessment(root, latest, workspace_error):
    """Map explicit acceptance items to check evidence without judging semantics."""
    try:
        items = configured_acceptance(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [{"id": "config", "description": "Invalid acceptance configuration", "status": "CONFIG_ERROR",
                 "checks": [], "questions": [str(exc)], "next_steps": [f"Fix agentproof.json: {exc}"]}]
    result = []
    by_name = {check.get("name"): check for check in latest}
    for item in items:
        linked = [by_name.get(name) for name in item.get("checks", [])]
        missing = [name for name, check in zip(item.get("checks", []), linked) if check is None]
        unusable = [check for check in linked if check is not None and not check["usable"]]
        if missing or unusable or workspace_error:
            status = "MISSING_EVIDENCE"
            next_steps = ([f"Run configured check: {name}" for name in missing]
                          + [f"Rerun or inspect check: {check.get('name')}" for check in unusable])
        else:
            status = "NEEDS_HUMAN_REVIEW"
            next_steps = list(item.get("questions", [])) or ["Confirm that the linked checks cover this acceptance item."]
        result.append({"id": item["id"], "description": item["description"], "status": status,
                       "checks": item.get("checks", []), "files": item.get("files", []),
                       "linked_checks": [{key: check.get(key) for key in
                                           ("name", "log", "execution", "workspace", "usable", "test_results")}
                                          for check in linked if check is not None],
                       "questions": item.get("questions", []),
                       "next_steps": next_steps})
    return result


def acceptance_fingerprint(items):
    """Fingerprint the validated acceptance contract, independent of formatting."""
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def decision_check_snapshot(check):
    """Keep the evidence identity a human decision was made against."""
    result = {key: check.get(key) for key in
              ("name", "command", "log", "log_sha256", "before", "after", "execution", "workspace",
               "log_integrity", "capture_error", "termination_error", "usable")}
    tests = check.get("test_results")
    if tests is not None:
        result["test_results"] = {key: tests.get(key) for key in ("path", "sha256", "size", "status")}
    return result


def apply_decisions(root, state, acceptance, latest, current_fp):
    """Overlay explicit human decisions, invalidating them when evidence moves."""
    try:
        configured = configured_acceptance(root)
        config_fp = acceptance_fingerprint(configured)
    except (OSError, ValueError, json.JSONDecodeError):
        config_fp = None
    by_name = {check.get("name"): check for check in latest}
    decisions = state.setdefault("decisions", [])
    latest_decisions = {}
    for decision in decisions:
        latest_decisions[decision.get("acceptance_id")] = decision
    for item in acceptance:
        decision = latest_decisions.get(item["id"])
        if decision is None:
            continue
        expected = {name: decision_check_snapshot(by_name[name]) for name in item.get("checks", [])
                    if name in by_name}
        recorded = {entry.get("name"): entry for entry in decision.get("checks", [])}
        valid = (current_fp is not None and config_fp is not None
                 and decision.get("workspace_fingerprint") == current_fp
                 and decision.get("acceptance_fingerprint") == config_fp
                 and set(recorded) == set(expected)
                 and all(recorded[name] == expected[name] for name in expected)
                 and decision.get("status") in {"CONFIRMED", "REJECTED"}
                 and (decision["status"] != "CONFIRMED" or item["status"] == "NEEDS_HUMAN_REVIEW"))
        if valid:
            item["status"] = decision.get("status", "STALE_CONFIRMATION")
            item["decision"] = {key: decision.get(key) for key in
                                 ("status", "note", "reason", "time") if decision.get(key) is not None}
            item["next_steps"] = ([]
                                   if item["status"] == "CONFIRMED" else
                                   ["Address the rejection reason and confirm again."])
        else:
            item["status"] = "STALE_CONFIRMATION"
            item["decision"] = {key: decision.get(key) for key in
                                 ("status", "note", "reason", "time") if decision.get(key) is not None}
            item["next_steps"] = ["Re-run affected checks and review the current evidence, then confirm or reject again."]
    return acceptance


def assessment(root, state):
    workspace_error = None
    try:
        current, omitted = inventory(root)
    except (OSError, ValueError) as exc:
        current, omitted, workspace_error = None, [], str(exc)
    baseline = get_snapshot(root, state, state["checkpoints"][0]["id"])["files"]
    changed = ([("added" if p not in baseline else "deleted" if p not in current else "modified", p)
                for p in sorted(set(baseline) | set(current)) if baseline.get(p) != current.get(p)]
               if current is not None else [])
    current_fp = fingerprint(current, omitted) if current is not None else None
    checks = [{"command": c["command"], "kind": c.get("kind", "other"),
               "command_template": c.get("command_template", c["command"]),
               "name": c.get("name"), "log": c["log"], "exit_code": c["exit_code"],
               "log_sha256": c.get("log_sha256"), "before": c.get("before"), "after": c.get("after"),
               **check_evidence(root, c, current_fp)} for c in state["checks"]]
    latest = latest_check_records(checks)
    latest_ids = {id(check) for check in latest}
    historical = [check for check in checks if id(check) not in latest_ids]
    acceptance = acceptance_assessment(root, latest, workspace_error)
    acceptance = apply_decisions(root, state, acceptance, latest, current_fp)
    attention = []
    if workspace_error:
        attention.append(f"Workspace comparison UNKNOWN: {workspace_error}. Execution records are retained.")
    if not checks:
        attention.append("No verification commands recorded. Run the project's actual checks.")
    for check in latest:
        label = check["name"] or check["command"]
        if check["log_integrity"] != "INTACT":
            attention.append(f"Log {check['log_integrity']}: {label}. Inspect or rerun this check.")
        if check["workspace"] == "STALE":
            attention.append(f"STALE: {label}. Rerun against the current files.")
        elif check["workspace"] == "UNKNOWN":
            attention.append(f"File matching UNKNOWN: {label}. {check['capture_error'] or 'Capture is incomplete.'}")
        elif check["execution"] != "PASS":
            attention.append(f"{check['execution']}: {label}. Inspect log: {check['log']}.")
        tests = check["test_results"]
        if tests is not None:
            if tests["status"] != "VALID":
                attention.append(f"JUnit {tests['status']}: {label}. " + tests.get("error", "Inspect or rerun for current test evidence."))
            if tests["status"] in {"VALID", "INCONSISTENT"}:
                count = tests["counts"]
                attention.append(f"JUnit reported for {label}: {count['tests']} tests, {count['passed']} passed, "
                                 f"{count['failures']} failures, {count['errors']} errors, {count['skipped']} skipped.")
                if count["tests"] == 0 or count["tests"] == count["skipped"]:
                    attention.append(f"No executed test cases reported for {label}. Check discovery and skip conditions.")
                for problem in tests["problems"][:3]:
                    location = problem["file"] or problem["class"]
                    message = " ".join(problem["message"].split())[:180]
                    attention.append(f"Inspect {problem['outcome']}: {location}::{problem['name']} — {message}")
                if len(tests["problems"]) > 3 or tests.get("problems_truncated"):
                    attention.append(f"More failure details for {label}; inspect the full report or source JUnit XML.")
    for item in acceptance:
        attention.append(f"Acceptance {item['status']}: {item['id']} — {item['description']}")
        attention.extend(f"Next: {step}" for step in item["next_steps"])
    plan = state.get("check_runs", [{}])[-1].get("planned", [])
    for step in plan:
        if step["status"] in {"NOT_RUN", "RUNNING", "ERROR"}:
            attention.append(f"{step['status']}: configured check {step['name']}. "
                             + step.get("error", "No completed result in the latest configured run."))
    review = None
    if state.get("reviews"):
        saved = state["reviews"][-1]
        review = review_claims(root, state, [x["claim"] for x in saved["items"]], saved["source"],
                               current_inventory=(current, omitted))
    else:
        attention.append("No completion claims supplied. Review the goal and relevant diff manually.")
    return {"schema_version": 1, "goal": state["goal"], "generated": now(),
            "baseline_mode": state.get("baseline_mode", "workspace"),
            "report_language": state.get("report_language", "en"),
            "baseline_commit": state.get("baseline_commit"),
            "changes": [{"kind": k, "path": p} for k, p in changed], "checks": checks,
            "latest_checks": latest, "historical_checks": historical,
            "planned_checks": plan, "workspace_error": workspace_error,
            "acceptance": acceptance, "attention": attention, "review": review, "omitted": omitted,
            "outcomes": state.get("outcomes", []),
            "limitations": "Command receipts and file changes do not certify task completion."}


def brief_report(data):
    return summary(data, data.get("report_language", "en"))


def report(root, state, data=None, archived=False, artifact_links=False):
    data = data if data is not None else assessment(root, state)
    def source_label(name):
        if archived:
            try:
                return Path(name).relative_to(STORE).as_posix()
            except ValueError:
                pass
        return name
    def evidence_link(name):
        label = source_label(name)
        if archived or artifact_links:
            target = Path(name).relative_to(STORE).as_posix()
            return f"[{label}]({target})"
        return f"`{label}`"
    chinese = data.get("report_language") == "zh-CN"
    title = "# AgentProof 交付报告" if chinese else "# AgentProof handoff report"
    detail_title = "详细证据（有疑问时再展开）" if chinese else "Technical evidence (open when needed)"
    lines = [title, "", f"Generated: {data['generated']}", "", brief_report(data), "",
             "<details>", f"<summary>{detail_title}</summary>", "", "## Changes since baseline", ""]
    if data.get("baseline_mode") == "historical_commit":
        lines += [f"Baseline: Git commit `{data.get('baseline_commit')}` (after-the-fact baseline).",
                  "This baseline can compare the current workspace with that commit, but cannot prove the complete pre-AI state or process.", ""]
    lines += ([f"- {c['kind']}: `{c['path']}`" for c in data["changes"]]
              or ["Comparison unavailable." if data["workspace_error"] else "No captured file changes."])
    lines += ["", "Compare with the captured task baseline using `agentproof diff`."]
    lines += ["", "## Verification", ""]
    if not state["checks"]:
        lines += ["No verification commands recorded."]
    current_checks = data.get("latest_checks", data["checks"])
    by_log = {check.get("log"): check for check in state["checks"]}
    current_state_checks = [by_log.get(check.get("log"), check) for check in current_checks]
    for check, evidence in zip(current_state_checks, current_checks):
        freshness = {"CURRENT": "matches current captured files", "STALE": "STALE / files changed",
                     "UNKNOWN": "UNKNOWN / file matching unavailable"}[evidence["workspace"]]
        status = evidence["execution"]
        command = check["command"]
        preview = " ".join(command.split())
        expanded = "\n" in command or len(preview) > 120
        if len(preview) > 120:
            preview = preview[:117] + "..."
        ticks = "`" * (max((len(x) for x in re.findall(r"`+", preview)), default=0) + 1)
        lines += [f"- **{status}** {ticks} {preview} {ticks} — recorded exit {check['exit_code']}; "
                  f"{freshness}; "
                  f"declared kind: {check.get('kind', 'other')}; "
                  f"log integrity: **{evidence['log_integrity']}**; "
                  f"log: {evidence_link(check['log'])}"]
        if check.get("timed_out"):
            lines += ["  Timed out; process-tree termination was requested."]
        if check.get("capture_error"):
            lines += [f"  Capture error: {check['capture_error']}"]
        if check.get("termination_error"):
            lines += [f"  Termination error: {check['termination_error']}"]
        if evidence["test_results"] is not None:
            tests = evidence["test_results"]
            lines += [f"  JUnit: **{tests['status']}**; source: `{source_label(tests['path'])}`."]
            if tests.get("counts"):
                lines += ["  Recorded report counts: " + "; ".join(f"{k}: {v}" for k, v in tests["counts"].items()) + "."]
                for problem in tests["problems"]:
                    location = problem["file"] or problem["class"]
                    if problem["line"]:
                        location += ":" + problem["line"]
                    message = " ".join(problem["message"].split())[:300]
                    lines += [f"  - Reported {problem['outcome']}: `{location}::{problem['name']}` — {message}"]
                if tests.get("problems_truncated"):
                    lines += ["  Additional problem records omitted; inspect source XML."]
        if expanded:
            fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", command)), default=0) + 1)
            lines += ["", "<details>", "<summary>Full command</summary>", "",
                      fence + "sh", command, fence, "", "</details>", ""]
    historical = data.get("historical_checks", [])
    if historical:
        lines += ["", "<details>", "<summary>Historical checks (collapsed)</summary>", "",
                  "Older executions remain available in `status --json` and their evidence logs.", ""]
        for check in historical:
            label = check.get("name") or check.get("command", "")
            lines.append(f"- **{check.get('execution', 'UNKNOWN')}** `{label}` — "
                         f"log: `{source_label(check.get('log', ''))}`; "
                         f"workspace: {check.get('workspace', 'UNKNOWN')}; "
                         f"log integrity: {check.get('log_integrity', 'UNKNOWN')}")
        lines += ["", "</details>"]
    if data["planned_checks"]:
        lines += ["", "## Latest configured run", ""]
        lines += [f"- **{step['status']}** `{step['name']}` — declared kind: {step['kind']}"
                  for step in data["planned_checks"]]
    if data.get("acceptance"):
        lines += ["", "## Acceptance scenarios and human decisions", ""]
        for item in data["acceptance"]:
            lines.append(f"- **{item['status']}** `{item['id']}` — {item['description']}")
            for path in item.get("files", []):
                captured = any(change["path"] == path for change in data["changes"])
                if captured:
                    target = "diffs/" + hashlib.sha256(path.encode()).hexdigest()[:16] + ".patch"
                    entry = f"[diff]({target})" if archived or artifact_links else f"`agentproof diff {path}`"
                    lines.append(f"  - File: `{path}` — {entry}")
                else:
                    lines.append(f"  - File: `{path}` — no captured change; inspect its existing behavior.")
            for check in item.get("linked_checks", []):
                lines.append(f"  - Check `{check['name']}`: {check['execution']}; {check['workspace']}; "
                             f"log: {evidence_link(check['log'])}")
                tests = check.get("test_results")
                if tests is not None:
                    lines.append(f"    - JUnit: {tests['status']}; {evidence_link(tests['path'])}")
                    for problem in tests.get("problems", []):
                        location = problem.get("file") or problem.get("class") or "unknown file"
                        lines.append(f"    - Failing case: `{location}::{problem['name']}` — "
                                     + " ".join(problem.get("message", "").split())[:300])
            for step in item.get("next_steps", []):
                if step not in item.get("questions", []):
                    lines.append(f"  - Next: {step}")
            decision = item.get("decision")
            if decision:
                if decision.get("note"):
                    lines.append(f"  - Note: {decision['note']}")
                if decision.get("reason"):
                    lines.append(f"  - Reason: {decision['reason']}")
                if decision.get("time"):
                    lines.append(f"  - Recorded: {decision['time']}")
    lines += ["", "## Imported session signals", ""]
    for session in state["sessions"]:
        lines += ["- " + "; ".join(f"{k}: {v}" for k, v in session["metrics"].items())]
    if not state["sessions"]:
        lines += ["No session imported."]
    lines += ["", "## Human task outcomes", ""]
    if data.get("outcomes"):
        for outcome in data["outcomes"]:
            duration = (f"; review seconds: {outcome['review_seconds']}"
                        if outcome.get("review_seconds") is not None else "")
            found = ("; issue found" if outcome.get("found_issue") is True else
                     "; no issue found" if outcome.get("found_issue") is False else "; issue result not recorded")
            note = f"; {outcome['note']}" if outcome.get("note") else ""
            lines.append(f"- {outcome['decision']}{duration}{found}{note} ({outcome['time']})")
    else:
        lines += ["No human outcome recorded. Run `agentproof outcome --decision ...` after review."]
    lines += ["", "This report does not certify correctness or task completion.",
              "Signals are heuristic observations, not proof of lost context or model errors.",
              "Imported sessions are not automatically correlated with this task's file changes.",
              "", "## Scope and limitations", "",
              "- Snapshots cover Git-tracked and non-ignored untracked regular files, including initial local edits.",
              "- Git index, ignored files, symlinks, submodules and external services are not restored or verified.",
              "- Sensitive filenames are excluded; this is not a secret scanner.",
              "- A passing command proves its exit status, not test coverage or requirements satisfaction.",
              "- Work concurrently edited while checking may invalidate evidence; stop edits before verification."]
    lines += [f"- Omitted `{x['path']}`: {x['reason']}" for x in data["omitted"]]
    lines += ["", "## Checkpoints", ""]
    lines += [f"- `{s['id']}` — {s['label']} ({s['time']})" for s in state["checkpoints"]]
    if data["review"]:
        lines += ["", "## Claim review", ""]
        latest = data["review"]
        lines += [f"Latest claim set: `{latest['source']}`; recomputed at {latest['time']}.", ""]
        for item in latest["items"]:
            lines.append(f"- **{item['status']}** {item['claim']} — {item['reason']}")
            lines.append(f"  - Evidence level: **{item.get('evidence_level', 'UNKNOWN')}**")
            lines += [f"  - Next: {step}" for step in item.get("next_steps", [])]
            lines += [f"  - Receipt: {receipt}" for receipt in item.get("receipts", [])]
        if latest.get("questions"):
            lines += ["", "### Human confirmation", ""]
            lines += [f"- {question}" for question in latest["questions"]]
    lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def review_claims(root, state, claims, source, current_inventory=None):
    """Match plain-language completion claims to observed local evidence.

    This deliberately uses transparent rules instead of another model. A claim is
    never upgraded to VERIFIED just because it sounds confident.
    """
    current, omitted = current_inventory if current_inventory is not None else inventory(root)
    current_fp = fingerprint(current, omitted) if current is not None else None
    baseline = get_snapshot(root, state, state["checkpoints"][0]["id"])["files"]
    changed = ([p for p in sorted(set(baseline) | set(current)) if baseline.get(p) != current.get(p)]
               if current is not None else [])
    checks = latest_check_records(state.get("checks", []))
    fresh_pass = [c for c in checks if check_evidence(root, c, current_fp)["usable"]]
    items, questions = [], []
    for raw in claims:
        claim = " ".join(raw.strip().split())
        if not claim:
            continue
        lower = claim.lower()
        kind = next((k for k, pattern in (
            ("test", r"\b(?:tests?|pytest|unittest)\b|测试"),
            ("lint", r"\blint\b|静态检查"),
            ("build", r"\bbuild\b|构建")) if re.search(pattern, lower)), None)
        says_pass = bool(re.search(r"\b(?:pass|passed|passing|success|successful|green)\b|通过|成功", lower))
        says_change = bool(re.search(r"\b(?:fix|fixed|change|changed|implement|implemented)\b|修复|修改|实现", lower))
        negated = bool(re.search(r"\b(?:not|never|no|failed)\b|未|没有|失败|不", lower))
        if negated:
            items.append({"status": "UNVERIFIED", "claim": claim,
                          "evidence_level": "PARTIAL_EVIDENCE",
                          "next_steps": ["Inspect the original output and confirm the stated limitation."],
                          "reason": "Negative or qualified statement; inspect the original evidence."})
            questions.append(f"Confirm the stated limitation: {claim}")
        elif kind and says_pass:
            matching = [c for c in fresh_pass if c.get("kind") == kind]
            receipts = ["Recorded exit 0 for: " + c["command"] + " [log: " + c["log"]
                        + "; declared kind: " + kind + "]" for c in matching]
            structured = False
            for check in matching:
                tests = junit_evidence(root, check)
                if tests is not None and tests["status"] == "VALID":
                    structured = True
                    receipts.append("JUnit reported: " + "; ".join(f"{k}: {v}" for k, v in tests["counts"].items())
                                    + " [source: " + tests["path"] + "]")
            level = "EVIDENCE_NEEDS_REVIEW" if receipts else "NO_EVIDENCE"
            items.append({"status": "UNVERIFIED", "claim": claim, "receipts": receipts,
                          "evidence_level": level,
                          "next_steps": (["Inspect the linked report and confirm scope, coverage and requirements."
                                          ] if receipts else [f"Run a fresh {kind} check and retain its output." ]),
                          "reason": ("Current JUnit report counts and outcomes are linked below. "
                                     "These are reporter-provided; coverage and requirements remain unverified."
                                     if structured else
                                     "Matching command receipts exist, but the check category is user-declared. "
                                     "Test execution, count, coverage and the full claim are not established."
                                     if receipts else
                                     "No fresh successful check of the declared type with intact log evidence.")})
            questions.append(f"Confirm scope and actual output for: {claim}")
        elif says_change:
            items.append({"status": "UNVERIFIED", "claim": claim,
                          "evidence_level": "PARTIAL_EVIDENCE" if changed else "NO_EVIDENCE",
                          "next_steps": [f"Inspect the relevant diff and add a reproducible acceptance check for: {claim}"],
                          "reason": (f"Observed {len(changed)} changed captured files; file changes do not establish this fix or implementation."
                                     if changed else "Captured file comparison is unavailable." if current is None else
                                     "No captured file changes; this does not rule out changes outside the captured scope.")})
            questions.append(f"Inspect the relevant diff and a reproducible acceptance check for: {claim}")
        else:
            items.append({"status": "UNVERIFIED", "claim": claim, "evidence_level": "NO_EVIDENCE",
                          "next_steps": ["指定可复现的文件、命令或外部结果证据。"], "reason": "没有可匹配的本地证据规则"})
            questions.append("为这条声明指定可复现的文件、命令或外部结果证据。")
    return {"time": now(), "source": source, "items": items, "questions": list(dict.fromkeys(questions))}


def log_tail(path):
    """Bound memory and terminal output even when a check writes a huge log."""
    with path.open("rb") as source:
        size = source.seek(0, os.SEEK_END)
        source.seek(max(0, size - 8192))
        text = source.read(8192).decode("utf-8", errors="replace")
    lines = text.splitlines()
    tail = "\n".join(lines[-12:])
    truncated = size > 8192 or len(lines) > 12 or len(tail) > 4000
    tail = tail[-4000:]
    # Do not emit terminal control sequences from command output.
    tail = "".join(c for c in tail if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))
    return ("[earlier output omitted]\n" if truncated else "") + (tail or "(no command output)")


def workspace_diff(root, state, selected=None):
    snap = get_snapshot(root, state, state["checkpoints"][0]["id"])
    current, _ = inventory(root)
    names = sorted(set(snap["files"]) | set(current))
    if selected is not None:
        safe_path(root, selected)
        if selected not in names:
            raise ValueError(f"Path is outside the captured baseline and current files: {selected}")
        names = [selected]
    lines = []
    for name in names:
        old, new = snap["files"].get(name), current.get(name)
        if old == new:
            continue
        before, after = b"", b""
        if old is not None:
            source = safe_path(root / STORE / "snapshots" / snap["id"] / "files", name)
            before = source.read_bytes()
            if hashlib.sha256(before).hexdigest() != old["sha256"]:
                raise ValueError(f"Corrupted baseline file: {name}")
        if new is not None:
            after = safe_path(root, name).read_bytes()
            if hashlib.sha256(after).hexdigest() != new["sha256"]:
                raise ValueError("Workspace changed during diff; retry when edits stop")
        lines.append(f"diff --agentproof a/{name} b/{name}\n")
        if old is not None and new is not None and old["mode"] != new["mode"]:
            lines.append(f"old mode {old['mode']:03o}\nnew mode {new['mode']:03o}\n")
        try:
            if b"\0" in before or b"\0" in after:
                raise UnicodeError()
            old_lines, new_lines = (x.decode("utf-8").splitlines(keepends=True) for x in (before, after))
        except UnicodeError:
            lines.append("Binary or non-UTF-8 file changed; inspect with an appropriate viewer.\n")
            continue
        for line in difflib.unified_diff(old_lines, new_lines,
                                         fromfile=f"a/{name}" if old is not None else "/dev/null",
                                         tofile=f"b/{name}" if new is not None else "/dev/null"):
            lines.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(lines) or "No captured file changes.\n"


def write_report(root, state):
    store = root / STORE
    # A human-readable comparison, not a Git-apply or rollback artifact.
    try:
        comparison = workspace_diff(root, state)
    except (OSError, ValueError) as exc:
        comparison = f"Comparison unavailable: {exc}\n"
    (store / "changes.patch").write_text(comparison, encoding="utf-8")
    data = assessment(root, state)
    related = {path for item in data["acceptance"] for path in item.get("files", [])}
    for change in data["changes"]:
        path = change["path"]
        if path in related:
            directory = store / "diffs"
            directory.mkdir(exist_ok=True)
            target = directory / (hashlib.sha256(path.encode()).hexdigest()[:16] + ".patch")
            try:
                patch = workspace_diff(root, state, path)
            except (OSError, ValueError) as exc:
                patch = f"Comparison unavailable: {exc}\n"
            target.write_text(patch, encoding="utf-8")
    content = report(root, state, data=data, artifact_links=True)
    content = content.replace("Compare with the captured task baseline using `agentproof diff`.",
                              "[View captured baseline diff](changes.patch), or run `agentproof diff`.")
    (store / "report.md").write_text(content, encoding="utf-8")
    return store / "report.md"


def finish_task(root, state):
    store = root / STORE
    write_report(root, state)
    ident = uuid.uuid4().hex[:12]
    history = store / "history"
    if history.is_symlink():
        raise ValueError("Task history directory must not be a symlink")
    history.mkdir(exist_ok=True)
    pending, destination = history / ("." + ident + ".pending"), history / ident
    pending.mkdir()
    try:
        for name in ("snapshots", "checks", "results", "diffs"):
            source = store / name
            if source.is_symlink():
                raise ValueError(f"Cannot archive symlink evidence directory: {name}")
            if source.exists():
                shutil.copytree(source, pending / name, symlinks=True)
        for name in ("report.md", "changes.patch"):
            shutil.copyfile(store / name, pending / name)
        archived_report = pending / "report.md"
        content = report(root, state, archived=True).replace(
            "Compare with the captured task baseline using `agentproof diff`.",
            "[View captured baseline diff](changes.patch). Archived comparison is static.")
        archived_report.write_text(content, encoding="utf-8")
        save(pending / "task.json", {**state, "id": ident, "finished": now()})
        pending.replace(destination)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    # Do not remove active evidence until a complete archive is in place.
    (store / "task.json").unlink()
    for name in ("snapshots", "checks", "results", "diffs"):
        if (store / name).exists():
            shutil.rmtree(store / name)
    for name in ("report.md", "changes.patch"):
        (store / name).unlink(missing_ok=True)
    return destination


def resume_task(store, archive, task):
    """Stage a complete evidence copy before publishing an active task."""
    names = ("snapshots", "checks", "results", "diffs")
    if (store / "task.json").exists():
        raise ValueError("An active task already exists; finish it before resuming history")
    if any((store / name).exists() or (store / name).is_symlink() for name in names):
        raise ValueError("Active evidence directories already exist; inspect them before resuming")
    if archive.is_symlink() or archive.parent.is_symlink():
        raise ValueError("Archived evidence must not contain symlinks")
    for path in archive.rglob("*"):
        if path.is_symlink():
            raise ValueError("Archived evidence must not contain symlinks")
    staging = store / (".resume-" + uuid.uuid4().hex)
    staging.mkdir()
    moved = []
    try:
        for name in names:
            source = archive / name
            if source.exists():
                shutil.copytree(source, staging / name)
        resumed = {key: value for key, value in task.items() if key not in {"id", "finished"}}
        resumed.update(resumed_from=task.get("id", archive.name), resumed_at=now())
        save(staging / "task.json", resumed)
        for name in (*names, "task.json"):
            source = staging / name
            if source.exists():
                source.replace(store / name)
                moved.append(name)
    except BaseException:
        for name in reversed(moved):
            destination = store / name
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def run(args, root):
    store = root / STORE
    if args.action == "history":
        entries = [(path, load(path)) for path in (store / "history").glob("*/task.json")
                   if not path.parent.name.startswith(".")]
        if args.history_action in {"show", "resume"}:
            match = next(((path, task) for path, task in entries
                          if task.get("id") == args.id or path.parent.name == args.id), None)
            if match is None:
                raise ValueError(f"Unknown archived task: {args.id}")
            archive, task = match
            if args.history_action == "show":
                if args.json:
                    print(json.dumps(task, ensure_ascii=False, indent=2))
                else:
                    print(f"{task.get('id', archive.parent.name)} | {task.get('finished', 'unknown')} | {task['goal']}")
                    print(f"Report: {archive.parent / 'report.md'}")
                    print(f"Evidence: {archive.parent}")
                return 0
            resume_task(store, archive.parent, task)
            print(f"Resumed archived task: {task.get('id', archive.parent.name)}")
            print("The archive remains unchanged; new evidence will be recorded in the active task.")
            return 0
        for path, task in sorted(entries, key=lambda item: item[1]["finished"], reverse=True):
            print(f"{task['id']} | {task['finished']} | {task['goal']}")
            print(f"  Report: {path.parent / 'report.md'}")
        if not entries:
            print("No archived tasks.")
        return 0
    if args.action == "check":
        checks = configured_checks(root)
        if args.list:
            print(json.dumps(checks, ensure_ascii=False, indent=2))
            return 0
    if args.action == "init":
        if (store / "task.json").exists():
            raise ValueError("Task already exists; use finish to archive it, or another worktree for a concurrent task")
        # Keep local evidence out of commits without editing the shared .gitignore.
        exclude_path = Path(os.fsdecode(git(root, "rev-parse", "--git-path", "info/exclude")).strip())
        if not exclude_path.is_absolute():
            exclude_path = root / exclude_path
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        exclusions = exclude_path.read_text(encoding="utf-8", errors="replace").splitlines() if exclude_path.exists() else []
        if "/.agentproof/" not in exclusions:
            with exclude_path.open("a", encoding="utf-8") as f:
                f.write("\n/.agentproof/\n")
        state = {"version": 1, "goal": args.goal, "created": now(),
                 "checkpoints": [], "checks": [], "sessions": [], "decisions": [], "outcomes": [],
                 "report_language": args.lang}
        if args.from_commit:
            state["baseline_mode"] = "historical_commit"
            state["baseline_commit"] = git(root, "rev-parse", "--verify", f"{args.from_commit}^{{commit}}").decode().strip()
            snap = checkpoint_from_commit(root, state, state["baseline_commit"],
                                          f"historical baseline: {state['baseline_commit'][:12]}")
            print("This is an after-the-fact baseline; it cannot prove the complete pre-AI state or process.")
        else:
            state["baseline_mode"] = "workspace"
            snap = checkpoint(root, state, "task start")
        print(f"Task initialized. Baseline: {snap['id']} ({len(snap['files'])} files)")
        return 0
    if not (store / "task.json").exists():
        raise ValueError("Run agentproof init --goal '...' first")
    state = load(store / "task.json")
    state.setdefault("decisions", [])
    if args.action in ("confirm", "reject"):
        try:
            acceptance_items = configured_acceptance(root)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot record a decision: {exc}") from exc
        item = next((entry for entry in acceptance_items if entry["id"] == args.acceptance_id), None)
        if item is None:
            raise ValueError(f"Unknown acceptance id: {args.acceptance_id}")
        current, omitted = inventory(root)
        current_fp = fingerprint(current, omitted)
        latest = latest_check_records([{
            "command": c["command"], "kind": c.get("kind", "other"),
            "command_template": c.get("command_template", c["command"]),
            "name": c.get("name"), "log": c["log"], "log_sha256": c.get("log_sha256"),
            "before": c.get("before"), "after": c.get("after"), "exit_code": c.get("exit_code"),
            **check_evidence(root, c, current_fp)} for c in state.get("checks", [])])
        try:
            configured = configured_acceptance(root)
            config_fp = acceptance_fingerprint(configured)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot record a decision: {exc}") from exc
        by_name = {check.get("name"): check for check in latest}
        if args.action == "confirm":
            missing = [name for name in item.get("checks", [])
                       if name not in by_name or not by_name[name]["usable"]]
            if missing:
                raise ValueError("Cannot confirm without current successful evidence: " + ", ".join(missing))
        elif not args.reason.strip():
            raise ValueError("A nonempty rejection reason is required")
        decision = {"acceptance_id": args.acceptance_id,
                    "status": "CONFIRMED" if args.action == "confirm" else "REJECTED",
                    "time": now(), "workspace_fingerprint": current_fp,
                    "acceptance_fingerprint": config_fp,
                    "checks": [decision_check_snapshot(by_name[name]) for name in item.get("checks", [])
                               if name in by_name]}
        if args.action == "confirm":
            decision["note"] = args.note
        else:
            decision["reason"] = args.reason
        state["decisions"].append(decision)
        save(store / "task.json", state)
        assessment_data = assessment(root, state)
        recorded = next(entry for entry in assessment_data["acceptance"] if entry["id"] == args.acceptance_id)
        print(f"{recorded['status']}: {args.acceptance_id}")
        if args.action == "confirm" and args.note:
            print(f"Note: {args.note}")
        if args.action == "reject":
            print(f"Reason: {args.reason}")
        if recorded["status"] == "STALE_CONFIRMATION":
            print("The decision was recorded, but its evidence is already stale; review and decide again.")
        return 0
    if args.action == "outcome":
        if args.review_seconds is not None and (not math.isfinite(args.review_seconds) or args.review_seconds < 0):
            raise ValueError("Review time must be finite and nonnegative")
        state.setdefault("outcomes", []).append({
            "time": now(), "review_seconds": args.review_seconds,
            "found_issue": args.found_issue, "decision": args.decision, "note": args.note})
        save(store / "task.json", state)
        print("Recorded task outcome.")
        return 0
    if args.action == "check":
        planned = [{**c, "status": "NOT_RUN"} for c in checks]
        state.setdefault("check_runs", []).append({"started": now(), "planned": planned})
        save(store / "task.json", state)
        outcome = 0
        for index, check in enumerate(checks):
            print(f"Check: {check['name']} (declared kind: {check['kind']})")
            state["check_runs"][-1]["planned"][index]["status"] = "RUNNING"
            save(store / "task.json", state)
            error = None
            try:
                code = run(argparse.Namespace(action="verify", command=check["command"],
                           timeout=check["timeout"], kind=check["kind"], check_name=check["name"],
                           junit=check["result_format"] == "junit"), root)
            except (OSError, ValueError) as exc:
                code, error = 2, str(exc)
                print(f"Check error: {error}")
            outcome = max(outcome, code)
            state = load(store / "task.json")
            step = state["check_runs"][-1]["planned"][index]
            step["status"] = "ERROR" if code == 2 else "FAIL" if code else "PASS"
            if error:
                step["error"] = error
            interrupted = code != 2 and state["checks"][-1].get("exit_code") == 130
            if interrupted:
                step["status"] = "INTERRUPTED"
            save(store / "task.json", state)
            if code == 2 or interrupted:
                print("Checks interrupted; remaining commands were not run.")
                break
        state["check_runs"][-1]["ended"] = now()
        save(store / "task.json", state)
        print(brief_report(assessment(root, state)))
        print(f"Report: {write_report(root, state)}")
        return outcome
    elif args.action == "finish":
        destination = finish_task(root, state)
        print(f"Task archived: {destination.name}")
        print(f"Report: {destination / 'report.md'}")
        print("Archiving is not approval of correctness. Run init to start the next task.")
    elif args.action == "diff":
        print(workspace_diff(root, state, args.path), end="")
    elif args.action == "checkpoint":
        snap = checkpoint(root, state, args.label)
        print(f"Checkpoint: {snap['id']} ({len(snap['files'])} files)")
    elif args.action == "import-session":
        metrics = import_session(Path(args.path))
        state["sessions"].append({"imported": now(), "metrics": metrics})
        save(store / "task.json", state)
        print(json.dumps(metrics, indent=2))
    elif args.action == "review":
        claims = list(args.claim or [])
        if args.summary:
            summary_path = Path(args.summary)
            if not summary_path.is_file():
                raise ValueError(f"Summary file not found: {args.summary}")
            claims.extend(summary_path.read_text(encoding="utf-8", errors="replace").splitlines())
        if not claims:
            raise ValueError("Provide --summary FILE or at least one --claim")
        review = review_claims(root, state, claims, args.summary or "command line claims")
        state.setdefault("reviews", []).append(review)
        save(store / "task.json", state)
        for item in review["items"]:
            print(f"{item['status']}: {item['claim']} — {item['reason']}")
            print(f"  Evidence level: {item.get('evidence_level', 'UNKNOWN')}")
            for receipt in item.get("receipts", []):
                print(f"  Receipt: {receipt}")
            for step in item.get("next_steps", []):
                print(f"  Next: {step}")
        if review["questions"]:
            print("\nHuman confirmation:")
            for question in review["questions"]:
                print(f"- {question}")
    elif args.action == "verify":
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("Timeout must be positive and finite")
        command = args.command
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            raise ValueError("Provide a command after --")
        template = list(command)
        junit = getattr(args, "junit", False)
        if junit != any("{junit}" in part for part in command):
            raise ValueError("--junit and a command containing {junit} must be used together")
        before = fingerprint(*inventory(root))
        log = Path(STORE) / "checks" / (uuid.uuid4().hex[:12] + ".log")
        (root / log).parent.mkdir(parents=True, exist_ok=True)
        result_path = None
        if junit:
            result_path = Path(STORE) / "results" / (log.stem + ".xml")
            target = evidence_path(root, str(result_path))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("JUnit output path already exists; refusing to reuse evidence")
            command = [part.replace("{junit}", str(target)) for part in command]
        timed_out = False
        termination_error = None
        import shlex
        record = {"command": shlex.join(command), "command_template": shlex.join(template), "exit_code": None,
                  "name": getattr(args, "check_name", None), "time": now(), "before": before,
                  "after": None, "log": str(log), "timed_out": False, "kind": args.kind,
                  "argv": command}
        if junit:
            record["test_results"] = {"status": "PENDING", "path": str(result_path)}
        state["checks"].append(record)
        save(store / "task.json", state)
        with (root / log).open("wb") as output:
            try:
                popen_kwargs = {"cwd": root, "stdout": output, "stderr": subprocess.STDOUT}
                if os.name != "nt":
                    popen_kwargs["start_new_session"] = True
                else:
                    popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                proc = subprocess.Popen(command, **popen_kwargs)
                try:
                    code = proc.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    code, timed_out = 124, True
                    try:
                        terminate_process_group(proc)
                    except OSError as exc:
                        termination_error = str(exc)
                except KeyboardInterrupt:
                    code = 130
                    try:
                        terminate_process_group(proc)
                    except OSError as exc:
                        termination_error = str(exc)
            except OSError as exc:
                output.write(f"{command[0]}: {exc}".encode())
                code = 127
        # Save the historical execution before any fallible post-command capture.
        record.update(exit_code=code, timed_out=timed_out, ended=now(), termination_error=termination_error)
        save(store / "task.json", state)
        try:
            record["log_sha256"] = file_digest(root / log)
            record["after"] = fingerprint(*inventory(root))
        except (OSError, ValueError) as exc:
            record["capture_error"] = str(exc)
        if junit:
            result = {"path": str(result_path)}
            try:
                result.update(parse_junit(evidence_path(root, str(result_path))))
            except FileNotFoundError:
                result.update(status="MISSING", error="This execution did not produce its JUnit report.")
            except ValueError as exc:
                result.update(status="INVALID", error=str(exc))
            except OSError as exc:
                result.update(status="UNREADABLE", error=str(exc))
            record["test_results"] = result
        save(store / "task.json", state)
        print(f"{'PASS' if code == 0 else 'FAIL'} (exit {code}). Log: {log}")
        if code != 0:
            if timed_out:
                print(f"Timed out after {args.timeout:g}s; process-tree termination was requested.")
            print("Last command output:")
            print(log_tail(root / log))
        if record.get("capture_error"):
            print(f"Execution retained; file matching UNKNOWN: {record['capture_error']}")
            return 2
        if junit:
            print(f"JUnit: {record['test_results']['status']}; source: {result_path}")
            if record["test_results"].get("counts"):
                print("Reported tests: " + json.dumps(record["test_results"]["counts"]))
            if not usable_test_results(record["test_results"]):
                print("Test evidence needs review; zero/skipped-only cases or invalid/failed results are not a successful test check.")
                return 1
        return 0 if code == 0 else 1
    elif args.action in ("report", "status"):
        if args.action == "report":
            if args.lang:
                state["report_language"] = args.lang
                save(store / "task.json", state)
            print(write_report(root, state))
        else:
            data = assessment(root, state)
            if args.lang and not args.json:
                data["report_language"] = args.lang
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(brief_report(data) if args.brief else report(root, state, data))
    elif args.action == "restore":
        snap = get_snapshot(root, state, args.id)
        pending = []
        for name, info in snap["files"].items():
            target = safe_path(root, name)
            source = safe_path(store / "snapshots" / args.id / "files", name)
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != info["sha256"]:
                raise ValueError(f"Corrupted snapshot file: {name}")
            if target.exists() and not target.is_file():
                raise ValueError(f"Restore conflicts with directory: {name}")
            for parent in target.parents:
                if parent == root:
                    break
                if parent.exists() and not parent.is_dir():
                    raise ValueError(f"Restore path blocked by non-directory: {parent.relative_to(root)}")
            if (not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != info["sha256"]
                    or target.stat().st_mode & 0o777 != info["mode"]):
                pending.append((name, data, info["mode"]))
        print(f"Restore {len(pending)} files from {args.id}. Files created later remain; Git index stays unchanged.")
        for name, _, _ in pending:
            print(f"  {name}")
        if not args.apply:
            print("Preview only. Use --apply to write files (automatic pre-restore backup).")
        elif pending:
            backup = checkpoint(root, state, "before restore " + args.id)
            for name, _, _ in pending:
                target = safe_path(root, name)
                if target.exists() and (name not in backup["files"] or
                        hashlib.sha256(target.read_bytes()).hexdigest() != backup["files"][name]["sha256"]):
                    raise ValueError(f"Cannot safely back up {name}; restore aborted before writing")
            for name, data, mode in pending:
                target = safe_path(root, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(mode)
            print(f"Restored. Backup checkpoint: {backup['id']}")
    return 0


def main(argv=None):
    # Windows consoles may default to a legacy code page that cannot render the
    # bilingual review questions. Keep reports and diagnostics lossless.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"AgentProof {__version__}")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("init", help="Start a task and capture its baseline")
    p.add_argument("--goal", required=True)
    p.add_argument("--lang", choices=["en", "zh-CN"], default="en", help="Report language for this task")
    p.add_argument("--from-commit", help="Use a Git commit as an after-the-fact baseline")
    p = sub.add_parser("checkpoint", help="Capture current files without changing Git history")
    p.add_argument("--label", default="manual checkpoint")
    p = sub.add_parser("import-session", help="Import Claude Code JSONL signal counts")
    p.add_argument("path")
    p = sub.add_parser("review", help="Match AI completion claims to recorded local evidence")
    p.add_argument("--summary", help="Text file containing one completion claim per line")
    p.add_argument("--claim", action="append", help="A completion claim; repeat for multiple claims")
    p = sub.add_parser("confirm", help="Record a human confirmation for an acceptance item")
    p.add_argument("acceptance_id")
    p.add_argument("--note", default="", help="Optional note explaining the confirmation")
    p = sub.add_parser("reject", help="Record a human rejection for an acceptance item")
    p.add_argument("acceptance_id")
    p.add_argument("--reason", required=True, help="Reason the acceptance item was rejected")
    p = sub.add_parser("outcome", help="Record the human result of reviewing this task")
    p.add_argument("--review-seconds", type=float, help="Approximate time spent reviewing the report")
    issue = p.add_mutually_exclusive_group()
    issue.add_argument("--found-issue", action="store_true", dest="found_issue", default=None,
                       help="The review found a missed problem")
    issue.add_argument("--no-issue", action="store_false", dest="found_issue", help="No problem was found")
    p.add_argument("--decision", choices=["accepted", "rejected", "deferred"], required=True)
    p.add_argument("--note", default="", help="Optional short observation")
    p = sub.add_parser("verify", help="Run a command, capture output and record workspace fingerprints")
    p.add_argument("--kind", choices=["test", "lint", "build", "other"], default="other",
                   help="Declared check type, not a correctness certification")
    p.add_argument("--junit", action="store_true", help="Read this run's JUnit XML via a {junit} command placeholder")
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("report", help="Write .agentproof/report.md")
    p.add_argument("--lang", choices=["en", "zh-CN"], help="Save the report language for this task")
    p = sub.add_parser("status", help="Print current evidence report")
    p.add_argument("--lang", choices=["en", "zh-CN"], help="Report language for this display")
    output = p.add_mutually_exclusive_group()
    output.add_argument("--brief", action="store_true", help="Print review priorities first")
    output.add_argument("--json", action="store_true", help="Print a versioned machine-readable assessment")
    p = sub.add_parser("check", help="Run explicit project checks and generate a review report")
    p.add_argument("--list", action="store_true", help="Inspect configured commands without running them")
    p = sub.add_parser("diff", help="Compare current captured files with the task baseline")
    p.add_argument("path", nargs="?", help="Optional repository-relative captured path")
    sub.add_parser("finish", help="Archive task evidence without approving correctness")
    p = sub.add_parser("history", help="List, inspect or resume archived tasks")
    p.add_argument("history_action", nargs="?", choices=["show", "resume"])
    p.add_argument("id", nargs="?")
    p.add_argument("--json", action="store_true", help="Show an archived task as JSON")
    p = sub.add_parser("restore", help="Preview file restoration; new files are retained")
    p.add_argument("id")
    p.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    lock = None
    try:
        root = Path(os.fsdecode(git(Path.cwd(), "rev-parse", "--show-toplevel")).strip()).resolve()
        store = root / STORE
        if store.is_symlink():
            raise ValueError("Evidence directory must not be a symlink")
        store.mkdir(exist_ok=True, mode=0o700)
        candidate = store / "lock"
        try:
            candidate.mkdir()
        except FileExistsError:
            raise ValueError("Another AgentProof command is running. If interrupted, remove .agentproof/lock manually.")
        lock = candidate
        return run(args, root)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"agentproof: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            lock.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
