"""Dependency-free local task evidence. Never interprets model claims as proof."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from . import __version__

STORE = ".agentproof"
MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024


def terminate_process_group(proc):
    """Terminate a timed-out command on POSIX and Windows."""
    if os.name == "nt":
        proc.kill()
    else:
        import signal
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def load(path):
    return json.loads(path.read_text())


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


def get_snapshot(root, state, ident):
    if ident not in {x["id"] for x in state["checkpoints"]}:
        raise ValueError("Unknown checkpoint ID")
    return load(root / STORE / "snapshots" / ident / "manifest.json")


def import_session(path):
    metrics = {"records": 0, "malformed_lines": 0, "tool_calls": 0,
               "tool_errors": 0, "compactions": 0, "truncation_markers": 0}
    with path.open() as source:
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
            message = record.get("message")
            content = message.get("content", []) if isinstance(message, dict) else []
            if not isinstance(content, list):
                content = []
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


def report(root, state):
    current, omitted = inventory(root)
    baseline = get_snapshot(root, state, state["checkpoints"][0]["id"])["files"]
    changed = [("added" if p not in baseline else "deleted" if p not in current else "modified", p)
               for p in sorted(set(baseline) | set(current)) if baseline.get(p) != current.get(p)]
    current_fp = fingerprint(current, omitted)
    lines = ["# AgentProof task evidence", "", f"Goal: {state['goal']}", "",
             f"Generated: {now()}", "", "This report records evidence; it does not certify correctness or task completion.",
             "", "## Changes since task start", ""]
    lines += [f"- {kind}: `{name}`" for kind, name in changed] or ["No captured file changes."]
    lines += ["", "## Verification", ""]
    if not state["checks"]:
        lines += ["No verification commands recorded."]
    for check in state["checks"]:
        fresh = check["before"] == check["after"] == current_fp
        status = "PASS" if check["exit_code"] == 0 else "FAIL"
        command = check["command"]
        preview = " ".join(command.split())
        expanded = "\n" in command or len(preview) > 120
        if len(preview) > 120:
            preview = preview[:117] + "..."
        ticks = "`" * (max((len(x) for x in re.findall(r"`+", preview)), default=0) + 1)
        lines += [f"- **{status}** {ticks} {preview} {ticks} — exit {check['exit_code']}; "
                  f"{'matches current captured files' if fresh else 'STALE / files changed'}; "
                  f"log: `{check['log']}`"]
        if check.get("timed_out"):
            lines += ["  Timed out; the process group was terminated."]
        if expanded:
            fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", command)), default=0) + 1)
            lines += ["", "<details>", "<summary>Full command</summary>", "",
                      fence + "sh", command, fence, "", "</details>", ""]
    lines += ["", "## Imported session signals", ""]
    for session in state["sessions"]:
        lines += ["- " + "; ".join(f"{k}: {v}" for k, v in session["metrics"].items())]
    if not state["sessions"]:
        lines += ["No session imported."]
    lines += ["", "Signals are heuristic observations, not proof of lost context or model errors.",
              "Imported sessions are not automatically correlated with this task's file changes.",
              "", "## Scope and limitations", "",
              "- Snapshots cover Git-tracked and non-ignored untracked regular files, including initial local edits.",
              "- Git index, ignored files, symlinks, submodules and external services are not restored or verified.",
              "- Sensitive filenames are excluded; this is not a secret scanner.",
              "- A passing command proves its exit status, not test coverage or requirements satisfaction.",
              "- Work concurrently edited while checking may invalidate evidence; stop edits before verification."]
    lines += [f"- Omitted `{x['path']}`: {x['reason']}" for x in omitted]
    lines += ["", "## Checkpoints", ""]
    lines += [f"- `{s['id']}` — {s['label']} ({s['time']})" for s in state["checkpoints"]]
    if state.get("reviews"):
        lines += ["", "## Claim review", ""]
        for review in state["reviews"]:
            lines += [f"Reviewed: `{review['source']}` ({review['time']})", ""]
            for item in review["items"]:
                lines.append(f"- **{item['status']}** {item['claim']} — {item['reason']}")
            if review.get("questions"):
                lines += ["", "### Human confirmation", ""]
                lines += [f"- {question}" for question in review["questions"]]
    return "\n".join(lines) + "\n"


def review_claims(root, state, claims, source):
    """Match plain-language completion claims to observed local evidence.

    This deliberately uses transparent rules instead of another model. A claim is
    never upgraded to VERIFIED just because it sounds confident.
    """
    current, omitted = inventory(root)
    current_fp = fingerprint(current, omitted)
    baseline = get_snapshot(root, state, state["checkpoints"][0]["id"])["files"]
    changed = [p for p in sorted(set(baseline) | set(current)) if baseline.get(p) != current.get(p)]
    checks = state.get("checks", [])
    verification_words = re.compile(r"\b(test|tests|pytest|unittest|jest|vitest|mocha|lint|format|build|compile|check|检查|测试|构建|编译)\b", re.I)
    fresh_pass = [c for c in checks if c["exit_code"] == 0 and c["before"] == c["after"] == current_fp
                  and verification_words.search(c["command"])]
    items, questions = [], []
    for raw in claims:
        claim = " ".join(raw.strip().split())
        if not claim:
            continue
        lower = claim.lower()
        is_test = bool(re.search(r"\b(test|tests|pytest|unittest|lint|build|check|检查|测试|构建)\b", lower))
        says_pass = bool(re.search(r"\b(pass|passed|passing|success|successful|green|通过|成功|完成)\b", lower))
        says_change = bool(re.search(r"\b(fix|fixed|change|changed|implement|implemented|修复|修改|实现)\b", lower))
        if is_test and says_pass:
            if fresh_pass:
                items.append({"status": "VERIFIED", "claim": claim,
                              "reason": f"{len(fresh_pass)} 个成功检查与当前文件指纹一致"})
            elif checks:
                items.append({"status": "CONFLICT", "claim": claim,
                              "reason": "检查存在，但没有一个成功结果仍与当前文件一致"})
                questions.append("重新运行与该声明对应的测试、构建或 lint，并确认结果匹配当前代码。")
            else:
                items.append({"status": "UNVERIFIED", "claim": claim, "reason": "没有记录任何检查命令"})
                questions.append("补充可复现的测试、构建或 lint 命令。")
        elif says_change:
            if changed:
                items.append({"status": "OBSERVED", "claim": claim,
                              "reason": f"检测到 {len(changed)} 个文件相对任务起点发生变化"})
            else:
                items.append({"status": "CONFLICT", "claim": claim, "reason": "当前工作区没有相对任务起点的文件变化"})
                questions.append("确认修改是否写入了另一个目录、分支或工作树。")
        else:
            items.append({"status": "UNVERIFIED", "claim": claim, "reason": "没有可匹配的本地证据规则"})
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


def run(args, root):
    store = root / STORE
    if args.action == "init":
        if (store / "task.json").exists():
            raise ValueError("Task already exists; use a separate repository/worktree for another task")
        # Keep local evidence out of commits without editing the shared .gitignore.
        exclude_path = Path(os.fsdecode(git(root, "rev-parse", "--git-path", "info/exclude")).strip())
        if not exclude_path.is_absolute():
            exclude_path = root / exclude_path
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        with exclude_path.open("a") as f:
            f.write("\n/.agentproof/\n")
        state = {"version": 1, "goal": args.goal, "created": now(),
                 "checkpoints": [], "checks": [], "sessions": []}
        snap = checkpoint(root, state, "task start")
        print(f"Task initialized. Baseline: {snap['id']} ({len(snap['files'])} files)")
        return 0
    if not (store / "task.json").exists():
        raise ValueError("Run agentproof init --goal '...' first")
    state = load(store / "task.json")
    if args.action == "checkpoint":
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
            claims.extend(summary_path.read_text(errors="replace").splitlines())
        if not claims:
            raise ValueError("Provide --summary FILE or at least one --claim")
        review = review_claims(root, state, claims, args.summary or "command line claims")
        state.setdefault("reviews", []).append(review)
        save(store / "task.json", state)
        for item in review["items"]:
            print(f"{item['status']}: {item['claim']} — {item['reason']}")
        if review["questions"]:
            print("\nHuman confirmation:")
            for question in review["questions"]:
                print(f"- {question}")
    elif args.action == "verify":
        command = args.command
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            raise ValueError("Provide a command after --")
        before = fingerprint(*inventory(root))
        log = Path(STORE) / "checks" / (uuid.uuid4().hex[:12] + ".log")
        (root / log).parent.mkdir(parents=True, exist_ok=True)
        timed_out = False
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
                    terminate_process_group(proc)
                    code, timed_out = 124, True
                except KeyboardInterrupt:
                    terminate_process_group(proc)
                    code = 130
            except OSError as exc:
                output.write(f"{command[0]}: {exc}".encode())
                code = 127
        after = fingerprint(*inventory(root))
        import shlex
        state["checks"].append({"command": shlex.join(command), "exit_code": code,
                                "time": now(), "before": before, "after": after,
                                "log": str(log), "timed_out": timed_out})
        save(store / "task.json", state)
        print(f"{'PASS' if code == 0 else 'FAIL'} (exit {code}). Log: {log}")
        if code != 0:
            if timed_out:
                print(f"Timed out after {args.timeout:g}s; the process group was terminated.")
            print("Last command output:")
            print(log_tail(root / log))
        return 0 if code == 0 else 1
    elif args.action in ("report", "status"):
        result = report(root, state)
        if args.action == "report":
            path = store / "report.md"
            path.write_text(result)
            print(path)
        else:
            print(result)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"AgentProof {__version__}")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("init", help="Start a task and capture its baseline")
    p.add_argument("--goal", required=True)
    p = sub.add_parser("checkpoint", help="Capture current files without changing Git history")
    p.add_argument("--label", default="manual checkpoint")
    p = sub.add_parser("import-session", help="Import Claude Code JSONL signal counts")
    p.add_argument("path")
    p = sub.add_parser("review", help="Match AI completion claims to recorded local evidence")
    p.add_argument("--summary", help="Text file containing one completion claim per line")
    p.add_argument("--claim", action="append", help="A completion claim; repeat for multiple claims")
    p = sub.add_parser("verify", help="Run a command, capture output and record workspace fingerprints")
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("command", nargs=argparse.REMAINDER)
    sub.add_parser("report", help="Write .agentproof/report.md")
    sub.add_parser("status", help="Print current evidence report")
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
