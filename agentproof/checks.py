"""Explicit, reusable project checks; no command discovery or shell evaluation."""
import json
import math


CONFIG = "agentproof.json"


def configured_checks(root):
    path = root / CONFIG
    if not path.is_file():
        raise ValueError(f"Create {CONFIG} with an explicit checks list first; see docs/checks.md")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) != {"version", "checks"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["checks"], list) or not data["checks"]):
        raise ValueError(f"{CONFIG} must contain version 1 and a nonempty checks list")
    result, names = [], set()
    for entry in data["checks"]:
        if not isinstance(entry, dict) or set(entry) - {"name", "kind", "command", "timeout"}:
            raise ValueError("Check entries support only name, kind, command and timeout")
        name, kind, command = (entry.get(k) for k in ("name", "kind", "command"))
        timeout = entry.get("timeout", 300)
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Check names must be nonempty and unique")
        if kind not in ("test", "lint", "build", "other"):
            raise ValueError(f"Invalid check kind for {name}")
        if (not isinstance(command, list) or not command
                or any(not isinstance(x, str) or not x or "\0" in x for x in command)):
            raise ValueError(f"Check command for {name} must be a nonempty string array")
        try:
            valid_timeout = type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError(f"Timeout for {name} must be positive and finite")
        names.add(name)
        result.append({"name": name, "kind": kind, "command": command, "timeout": timeout})
    return result
