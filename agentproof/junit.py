"""Read bounded JUnit XML as reported test evidence, never a correctness verdict."""
import hashlib
import io
import xml.etree.ElementTree as ET


MAX_XML = 8 * 1024 * 1024
MAX_ELEMENTS = 50000
MAX_FAILURES = 20


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


def counts(cases):
    result = {"tests": len(cases), "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    for case in cases:
        outcomes = {tag(child) for child in case if tag(child) in {"failure", "error", "skipped"}}
        if len(outcomes) > 1:
            raise ValueError("Contradictory testcase outcomes")
        outcome = next(iter(outcomes), None)
        for attribute in ("status", "result"):
            hint = case.get(attribute, "").lower()
            if hint in {"notrun", "disabled", "suppressed", "skipped"}:
                if outcome not in {None, "skipped"}:
                    raise ValueError("Contradictory skipped testcase status")
                outcome = "skipped"
            elif hint in {"passed", "pass", "success"}:
                if outcome is not None:
                    raise ValueError("Contradictory passing testcase status")
            elif hint not in {"", "run", "completed"}:
                raise ValueError("Unsupported testcase status/result attribute")
        key = {"failure": "failures", "error": "errors", "skipped": "skipped"}.get(outcome, "passed")
        result[key] += 1
    return result


def parse_junit(path):
    with path.open("rb") as source:
        raw = source.read(MAX_XML + 1)
    if len(raw) > MAX_XML:
        raise ValueError("JUnit report exceeds 8 MiB limit")
    try:
        text = raw.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("JUnit DTDs and entity declarations are not supported")
        root, depth, element_count = None, 0, 0
        for event, element in ET.iterparse(io.StringIO(text), events=("start", "end")):
            if event == "start":
                depth, element_count = depth + 1, element_count + 1
                if root is None:
                    root = element
                if depth > 32 or element_count > MAX_ELEMENTS:
                    raise ValueError("JUnit report exceeds element or nesting limit")
            else:
                depth -= 1
    except (UnicodeError, ET.ParseError) as exc:
        raise ValueError(f"Invalid UTF-8 JUnit XML: {exc}") from exc
    if tag(root) not in {"testsuite", "testsuites"}:
        raise ValueError("JUnit root must be testsuite or testsuites")
    cases = [element for element in root.iter() if tag(element) == "testcase"]
    result = counts(cases)
    # Validate declared counts at every suite, without double-counting nested suites.
    mismatches, mismatch_count = [], 0
    for suite in root.iter():
        if tag(suite) not in {"testsuite", "testsuites"}:
            continue
        actual = counts([case for case in suite.iter() if tag(case) == "testcase"])
        for key in ("tests", "failures", "errors", "skipped"):
            if key in suite.attrib:
                try:
                    declared = int(suite.attrib[key])
                except ValueError as exc:
                    raise ValueError(f"Invalid JUnit {key} count") from exc
                if declared < 0:
                    raise ValueError(f"Invalid negative JUnit {key} count")
                if declared != actual[key]:
                    mismatch_count += 1
                    if len(mismatches) < 20:
                        mismatches.append({"suite": suite.get("name", "")[:200], "metric": key,
                                           "declared": declared, "case_records": actual[key]})
    problems = []
    for case in cases:
        for child in case:
            if tag(child) in {"failure", "error"} and len(problems) < MAX_FAILURES:
                problems.append({"name": case.get("name", "")[:500],
                                 "class": case.get("classname", "")[:500],
                                 "file": case.get("file", "")[:500],
                                 "line": case.get("line", "")[:50], "outcome": tag(child),
                                 "message": (child.get("message") or child.text or "")[:1000]})
                break
    status = "INCONSISTENT" if mismatches else "VALID"
    parsed = {"status": status, "counts": result, "problems": problems,
            "problems_truncated": result["failures"] + result["errors"] > len(problems),
            "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
            "count_mismatches": mismatches, "mismatches_truncated": mismatch_count > len(mismatches)}
    if mismatches:
        parsed["error"] = "; ".join(f"Suite {m['suite']}: declared {m['metric']}={m['declared']}, "
                                    f"testcase records={m['case_records']}" for m in mismatches[:3])
    return parsed
