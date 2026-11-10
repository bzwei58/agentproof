"""Explicit, reusable project checks; no command discovery or shell evaluation."""
import json
import math
from pathlib import PurePosixPath, PureWindowsPath


CONFIG = "agentproof.json"


def configured_checks(root):
    path = root / CONFIG
    if not path.is_file():
        raise ValueError(f"Create {CONFIG} with an explicit checks list first; see docs/checks.md")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) - {"version", "checks", "acceptance"}
            or type(data.get("version")) is not int or data["version"] != 1
            or not isinstance(data.get("checks"), list) or not data["checks"]):
        raise ValueError(f"{CONFIG} must contain version 1 and a nonempty checks list")
    result, names = [], set()
    for entry in data["checks"]:
        if not isinstance(entry, dict) or set(entry) - {"name", "kind", "command", "timeout", "result_format"}:
            raise ValueError("Check entries support only name, kind, command, timeout and result_format")
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
        result_format = entry.get("result_format")
        if result_format not in (None, "junit"):
            raise ValueError(f"Unsupported result_format for {name}")
        if (result_format == "junit") != any("{junit}" in argument for argument in command):
            raise ValueError(f"{name}: result_format junit requires a command containing {{junit}}")
        names.add(name)
        result.append({"name": name, "kind": kind, "command": command, "timeout": timeout,
                       "result_format": result_format})
    acceptance = data.get("acceptance", [])
    if not isinstance(acceptance, list):
        raise ValueError("acceptance must be an array")
    ids = set()
    for item in acceptance:
        if (not isinstance(item, dict) or set(item) - {"id", "description", "checks", "questions", "files"}
                or not isinstance(item.get("id"), str) or not item["id"].strip()
                or item["id"] in ids or not isinstance(item.get("description"), str)
                or not item["description"].strip()):
            raise ValueError("Acceptance items require unique id and description")
        checks_for_item = item.get("checks", [])
        questions = item.get("questions", [])
        files = item.get("files", [])
        if (not isinstance(checks_for_item, list) or any(not isinstance(x, str) or x not in names for x in checks_for_item)
                or not isinstance(questions, list) or any(not isinstance(x, str) or not x.strip() for x in questions)
                or not isinstance(files, list) or any(not isinstance(x, str) or not x.strip() for x in files)):
            raise ValueError(f"Invalid acceptance mapping for {item['id']}")
        for name in files:
            path = PurePosixPath(name)
            if ("\x00" in name or "\\" in name or path.is_absolute() or PureWindowsPath(name).drive
                    or ".." in path.parts or not path.parts or path.parts[0] in {".git", ".agentproof"}):
                raise ValueError(f"Acceptance files must be repository-relative paths: {name}")
        ids.add(item["id"])
    return result


def configured_acceptance(root):
    """Return optional requirement-to-check mappings from the same validated config."""
    path = root / CONFIG
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    configured_checks(root)
    return data.get("acceptance", [])
