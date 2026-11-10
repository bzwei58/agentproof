"""Plain-language handoff derived from recorded evidence, without semantic inference."""


def summary(data, language="en"):
    def say(en, zh):
        return zh if language == "zh-CN" else en

    lines = [say("## 1. What changed", "## 1. 这次改了什么"), "",
             say(f"Task requested: {data['goal']}", f"这次要做的事：{data['goal']}")]
    claims = (data.get("review") or {}).get("items", [])
    if claims:
        lines += ["", say("Implementer's handoff (statements to check against the evidence):",
                           "实现者的交付说明（还需结合下面的证据核对）："), ""]
        lines += [f"- {item['claim']}" for item in claims[:3]]
        lines += [""]
        if len(claims) > 3:
            lines += [say("More statements are retained in the detailed evidence.", "其余说明保留在详细证据中。"), ""]
    else:
        lines += [say("No delivery description was supplied; file changes below do not explain the behavior. Ask the implementer for a short handoff if needed.",
                      "实现者尚未提供交付说明；下方文件清单不能解释功能变化。需要时请让实现者补一句具体说明。"), ""]
    if data["workspace_error"]:
        lines += [say("File comparison could not be completed; the change scope is unknown.",
                      "文件比较没有完成，目前无法确定改动范围。")]
    else:
        changes = data["changes"]
        lines += [say(f"Observed changes in {len(changes)} captured files.",
                      f"实际记录到 {len(changes)} 个文件发生变化。"), ""]
        kinds = {"added": say("added", "新增"), "modified": say("modified", "修改"),
                 "deleted": say("deleted", "删除")}
        related = {path for item in data.get("acceptance", []) for path in item.get("files", [])}
        ordered = sorted(changes, key=lambda change: (change["path"] not in related, change["path"]))
        lines += [f"- {kinds.get(change['kind'], change['kind'])} `{change['path']}`" for change in ordered[:5]]
        lines += [""]
        if len(changes) > 5:
            lines += [say(f"The remaining {len(changes) - 5} files are listed in the evidence below.",
                          f"其余 {len(changes) - 5} 个文件见下方展开的详细证据。")]
    lines += [say("File changes alone do not show that the requested behavior works.",
                  "文件发生变化，只能说明做过修改；是否符合你的要求，还要结合检查结果和实际表现。")]
    if data.get("baseline_mode") == "historical_commit":
        lines += [say("This comparison uses an after-the-fact Git baseline; it cannot reconstruct the full pre-AI workspace.",
                      "这次使用事后选取的 Git 提交作为比较起点，无法还原 AI 开始前的完整工作区。")]

    lines += ["", say("## 2. What checks found", "## 2. 检查发现了什么"), ""]
    checks = data["latest_checks"]
    if not checks:
        lines += [say("No checks have been recorded. Ask the implementer to run the project's checks before deciding.",
                      "还没有记录检查结果。先让实现者运行项目已有的检查，再作决定。")]
    for check in checks:
        label = check.get("name") or check["kind"]
        if check["usable"]:
            message = say("completed successfully; still matches the current files and its log is intact",
                          "执行成功，结果仍对应当前文件，日志完整")
        else:
            reasons = []
            if check["execution"] != "PASS":
                reasons.append({"FAIL": say("command failed", "命令执行失败"),
                                "TIMEOUT": say("command timed out", "执行超时"),
                                "INTERRUPTED": say("command was interrupted", "执行中断")}.get(
                                    check["execution"], say("execution result is unknown", "执行结果未知")))
            if check["workspace"] != "CURRENT":
                reasons.append(say("result does not establish the state of the current files; rerun the check",
                                   "结果不能证明当前文件的情况，需要重新检查"))
            if check["log_integrity"] != "INTACT":
                reasons.append(say("the log is missing, changed or could not be verified", "日志缺失、变化或无法核对"))
            result = check.get("test_results")
            if result is not None:
                counts = result.get("counts", {})
                if result["status"] != "VALID" or counts.get("failures") or counts.get("errors"):
                    reasons.append(say("the test report needs investigation", "测试报告存在问题，需要处理"))
                elif counts.get("tests", 0) <= counts.get("skipped", 0):
                    reasons.append(say("no executed test cases were reported", "没有报告实际执行的测试用例"))
            message = "; ".join(reasons) or say("evidence capture is incomplete", "证据记录不完整")
        lines.append(f"- {label}: {message}" + say(".", "。"))
        tests = check.get("test_results")
        if tests is not None and tests.get("counts"):
            count = tests["counts"]
            lines.append(say(f"  Reported cases: {count['passed']} passed, {count['failures']} failed, "
                             f"{count['errors']} errors, {count['skipped']} skipped.",
                             f"  报告中的用例：通过 {count['passed']}，失败 {count['failures']}，"
                             f"错误 {count['errors']}，跳过 {count['skipped']}。"))
    pending = [step["name"] for step in data.get("planned_checks", [])
               if step["status"] in {"NOT_RUN", "RUNNING", "ERROR"}]
    if pending:
        lines.append(say("Checks not completed: ", "尚未完成的检查：") + ", ".join(pending))
    if data.get("workspace_error"):
        lines.append(say("Current file matching could not be established.", "目前无法核对检查结果是否对应当前文件。"))
    lines += ["", say("A successful command is not a guarantee that every requirement or scenario is covered.",
                  "命令执行成功，不等于所有需求和场景都已得到验证。")]

    lines += ["", say("## 3. What you still need to decide", "## 3. 还需要你判断什么"), "",
              say("Read the result against your original request. You do not need to certify the whole codebase or inspect every log.",
                  "请对照你原本想要的结果来看。你不需要保证全部代码正确，也不用把所有日志重新检查一遍。")]
    statuses = {
        "NEEDS_HUMAN_REVIEW": say("Checks are available; your judgment is still needed", "检查结果已具备，仍需要你的判断"),
        "MISSING_EVIDENCE": say("Evidence is incomplete; ask the implementer to fill the gap first", "证据不完整，先让实现者补齐"),
        "CONFIRMED": say("You previously confirmed this result", "你之前已确认这一项"),
        "REJECTED": say("You asked for changes to this result", "你之前已指出这一项需要修改"),
        "STALE_CONFIRMATION": say("The evidence changed after your decision; review this result again", "你上次决定后证据发生了变化，需要重新看这一项"),
        "CONFIG_ERROR": say("Review setup is invalid; ask the implementer to fix it", "审查配置有误，先让实现者修正")}
    for item in data.get("acceptance", []):
        lines += ["", f"- {item['description']} — {statuses.get(item['status'], item['status'])}" + say(".", "。")]
        if item["status"] in {"NEEDS_HUMAN_REVIEW", "STALE_CONFIRMATION"}:
            questions = item.get("questions") or [say("Does the result meet your original request? What is missing?",
                                                     "实际结果是否符合你原本的要求？还有哪里不符合？")]
            lines += [f"  - {question}" for question in questions]
        decision = item.get("decision", {})
        note = decision.get("note") or decision.get("reason")
        if note:
            lines += [say(f"  - Your recorded feedback: {note}", f"  - 已记录的反馈：{note}")]
    if not data.get("acceptance"):
        lines += [say("No specific review scenarios were configured. State which requested behavior you can or cannot confirm; ask the implementer for a concrete example if needed.",
                      "尚未设置具体审查场景。请说明原本要求的哪项表现你已看明白、哪项还无法判断；必要时让实现者提供一个可观察的例子。")]
    if data.get("review"):
        lines.append(say("AI completion claims still need confirmation against the requested behavior.",
                         "AI 的完成声明仍不能直接当作需求已满足的结论。"))
    lines += ["", say('Reply in ordinary words: "This meets my request", "Change this: …", or "I cannot judge this: …". You can leave any item undecided.',
                       '你可以直接回复：“这符合我的要求”“这里要改：……”或“这一点我看不懂：……”。不确定的地方可以暂不决定。'),
              say("Open the technical evidence only when a result is unclear. Feedback is not a correctness guarantee.",
                  "某个结果有疑问时，再展开下方证据查阅。你的反馈不代表保证代码正确。")]
    return "\n".join(lines) + "\n"
